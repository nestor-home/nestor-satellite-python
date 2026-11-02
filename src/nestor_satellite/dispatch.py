"""What the satellite does with each message from the core: hand intents to the body (and the
assistant's words to the text display) and acknowledge them, hand the welcome back to the link."""

import json
import logging
from collections.abc import Callable
from typing import Any

from nestor_satellite import wire
from nestor_satellite.body import Body
from nestor_satellite.protocol import ProtocolError

logger = logging.getLogger(__name__)

type TextDisplay = Callable[[str, bool], None]  # shows the assistant's words (text, final); returns at once


class Dispatcher:
    def __init__(
        self, body: Body, reply: Callable[[dict[str, Any]], None], *, text_display: TextDisplay | None = None
    ) -> None:
        self.body = body
        self._reply = reply
        self._text_display = text_display

    def handle(self, raw: str | bytes) -> wire.Welcome | None:
        """Carry out one message; the welcome is returned, everything else is handled here."""
        try:
            received = wire.parse(json.loads(raw))
        except (ValueError, ProtocolError) as error:
            logger.warning("ignoring an off-contract message from the core: %s", error)
            return None
        self.body.heartbeat()
        match received.message:
            case None:
                return None  # a newer message type: nothing to acknowledge
            case wire.Welcome() as welcome:
                return welcome
            case wire.BehaviorIntent(behavior=behavior):
                self.body.behavior(behavior)
            case wire.ExpressionIntent(expression=expression, intensity=intensity):
                self.body.expression(expression, intensity)
            case wire.IdentifyIntent():
                self.body.identify()
            case wire.TextIntent(text=text, final=final):
                if self._text_display is not None:
                    self._text_display(text, final)
            case _:  # the last intent: a beat
                self.body.beat()
        self._reply(wire.ack(received.id))
        return None
