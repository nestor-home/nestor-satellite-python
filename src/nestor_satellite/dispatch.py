"""What the satellite does with each message from the core: hand intents to the body and
acknowledge them, take note of the welcome, notice the core hanging up. The connection reports
the link up to the body once it is welcomed."""

import json
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

from nestor_satellite import wire
from nestor_satellite.body import Body
from nestor_satellite.protocol import ProtocolError

logger = logging.getLogger(__name__)


class Outcome(Enum):
    NOTHING = "nothing"
    WELCOMED = "welcomed"
    HUNG_UP = "hung_up"  # the transport said the core left


class Dispatcher:
    def __init__(self, body: Body, reply: Callable[[dict[str, Any]], None]) -> None:
        self.body = body
        self._reply = reply
        self.welcome: wire.Welcome | None = None

    def handle(self, raw: str) -> Outcome:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("type") == wire.SIGNALLING:
                gone = data.get("message", {}).get("type") == "peerLeft"
                return Outcome.HUNG_UP if gone else Outcome.NOTHING
            received = wire.parse(data)
        except (ValueError, ProtocolError) as error:
            logger.warning("ignoring an off-contract message from the core: %s", error)
            return Outcome.NOTHING
        self.body.heartbeat()
        match received.message:
            case None:
                return Outcome.NOTHING  # a newer message type: nothing to acknowledge
            case wire.Welcome() as welcome:
                self.welcome = welcome
                return Outcome.WELCOMED
            case wire.BehaviorIntent(behavior=behavior):
                self.body.behavior(behavior)
            case wire.ExpressionIntent(expression=expression, intensity=intensity):
                self.body.expression(expression, intensity)
            case wire.IdentifyIntent():
                self.body.identify()
            case _:  # the last intent: a beat
                self.body.beat()
        self._reply(wire.ack(received.id))
        return Outcome.NOTHING
