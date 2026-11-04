"""Messages on the link, one WebSocket text frame each: a JSON envelope `{"type", "id", "at", "payload"}`.

The satellite sends `hello` first (its manifest and token), then `ack`s, observations, typed text and
`update.status`; the core sends `welcome` and `intent.*`. A message type this SDK does not know is ignored, so a newer
core does not break an older driver; a known type with an invalid payload is a `ProtocolError`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nestor_satellite.protocol import Behavior, Expression, Manifest, ProtocolError, UpdateState

TYPED_MAX = 4000  # characters a person types in one go (`input.text`)
SHOWN_MAX = 16000  # characters of one message of the assistant (`intent.text`)


def envelope(message_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": message_type, "id": uuid.uuid4().hex, "at": datetime.now(UTC).isoformat(), "payload": payload}


def hello(manifest: Manifest, token: str) -> dict[str, Any]:
    return envelope("hello", {"manifest": manifest.to_json(), "token": token})


def ack(intent_id: str) -> dict[str, Any]:
    return envelope("ack", {"of": intent_id})


def person_detected(count: int, confidence: float) -> dict[str, Any]:
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ProtocolError(f"a person count is a non-negative integer, not {count!r}")
    return envelope("observation.person.detected", {"count": count, "confidence": _unit(confidence)})


def speech_activity(active: bool) -> dict[str, Any]:
    return envelope("observation.speech.activity", {"active": bool(active)})


def text_input(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not 0 < len(text) <= TYPED_MAX:
        raise ProtocolError(f"typed text holds 1 to {TYPED_MAX} characters")
    return envelope("input.text", {"text": text})


def update_status(state: UpdateState, version: str | None = None) -> dict[str, Any]:
    if (version is None) != (state is UpdateState.CURRENT):
        raise ProtocolError("a version goes with every update state but current, and only with them")
    return envelope("update.status", {"state": state.value} | ({"version": version} if version else {}))


@dataclass(frozen=True, slots=True)
class Welcome:
    device_id: str
    room: str
    capabilities: frozenset[str]  # what the household allowed, among what was declared


@dataclass(frozen=True, slots=True)
class BehaviorIntent:
    behavior: Behavior


@dataclass(frozen=True, slots=True)
class ExpressionIntent:
    expression: Expression
    intensity: float = 1.0


@dataclass(frozen=True, slots=True)
class BeatIntent:
    pass


@dataclass(frozen=True, slots=True)
class IdentifyIntent:
    pass


@dataclass(frozen=True, slots=True)
class TextIntent:
    text: str
    final: bool = True  # the last message of the assistant's turn


@dataclass(frozen=True, slots=True)
class UpdateIntent:
    version: str  # a person asks for it: installed only if it is the newer version the device found


type Intent = BehaviorIntent | ExpressionIntent | BeatIntent | IdentifyIntent | TextIntent | UpdateIntent


@dataclass(frozen=True, slots=True)
class Received:
    id: str
    message: Welcome | Intent | None  # None: a message type this SDK does not know


def parse(data: Any) -> Received:
    """Read a message from the core."""
    if not isinstance(data, dict) or not {"type", "id", "payload"} <= data.keys():
        raise ProtocolError(f"not a Nestor message: {data!r}")
    kind, payload = data["type"], data["payload"]
    if not isinstance(payload, dict) or not isinstance(data["id"], str):
        raise ProtocolError(f"malformed {kind} message")
    try:
        match kind:
            case "welcome":
                message = Welcome(str(payload["device_id"]), str(payload["room"]), frozenset(payload["capabilities"]))
            case "intent.behavior":
                message = BehaviorIntent(Behavior(payload["behavior"]))
            case "intent.expression":
                intensity = _unit(payload.get("intensity", 1.0))
                message = ExpressionIntent(Expression(payload["expression"]), intensity)
            case "intent.beat":
                message = BeatIntent()
            case "intent.identify":
                message = IdentifyIntent()
            case "intent.text":
                text, final = payload["text"], payload.get("final", True)
                if not isinstance(text, str) or not 0 < len(text) <= SHOWN_MAX or not isinstance(final, bool):
                    raise ValueError(f"expected 1 to {SHOWN_MAX} characters and a boolean final")
                message = TextIntent(text, final)
            case "intent.update":
                if not isinstance(payload["version"], str) or not 0 < len(payload["version"]) <= 64:
                    raise ValueError("expected a version of 1 to 64 characters")
                message = UpdateIntent(payload["version"])
            case _:
                message = None
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError(f"invalid {kind} payload {payload!r}: {error}") from None
    return Received(data["id"], message)


def _unit(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ProtocolError(f"expected a number between 0 and 1, not {value!r}")
    return float(value)
