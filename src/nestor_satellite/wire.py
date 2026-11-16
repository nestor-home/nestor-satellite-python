"""Messages on the link, one WebSocket text frame each: a JSON envelope `{"type", "id", "at", "payload"}`.

The satellite sends `hello` first (its manifest and token), then `ack`s, observations, typed text,
`update.status` and `media.snapshot`; the core sends `welcome` and `intent.*` (`intent.media.*` included). A message
type this SDK does not know is ignored, so a newer core does not break an older driver; a known type with an invalid
payload is a `ProtocolError`.
"""

import base64
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nestor_satellite.protocol import Behavior, Expression, Manifest, ProtocolError, UpdateState

TYPED_MAX = 4000  # characters a person types in one go (`input.text`)
SHOWN_MAX = 16000  # characters of one message of the assistant (`intent.text`)
SESSION_MAX = 64  # characters of a media session's id
LASTING_MAX_S = 3600  # the longest a media session may last
SNAPSHOT_MAX = 4_000_000  # characters of a still picture in base64 (`media.snapshot`): about 3 MB of JPEG


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


def update_status(state: UpdateState, version: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """`notes`: an https address of what `version` changes, for people (the core only shows it)."""
    if (version is None) != (state is UpdateState.CURRENT) or (notes is not None and version is None):
        raise ProtocolError("a version, and its notes, go with every update state but current, and only with them")
    if notes is not None and not (notes.startswith("https://") and 9 <= len(notes) <= 512 and " " not in notes):
        raise ProtocolError("notes are an https address of at most 512 characters")
    payload = {"state": state.value} | ({"version": version} if version else {}) | ({"notes": notes} if notes else {})
    return envelope("update.status", payload)


def snapshot(of: str, jpeg: bytes) -> dict[str, Any]:
    """The still picture asked by the intent `of` (`media.snapshot`)."""
    encoded = base64.b64encode(jpeg).decode()
    if not 0 < len(encoded) <= SNAPSHOT_MAX:
        raise ProtocolError(f"a still picture holds 1 to {SNAPSHOT_MAX} characters in base64")
    return envelope("media.snapshot", {"of": of, "jpeg": encoded})


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


@dataclass(frozen=True, slots=True)
class MediaOpenIntent:
    """A person opened a session: connect at once, with the camera's video when `video`, the microphone and the
    speaker both ways when `audio` (a call), for `max_s` seconds at most."""

    session: str
    video: bool
    audio: bool
    max_s: int


@dataclass(frozen=True, slots=True)
class MediaCloseIntent:
    session: str


@dataclass(frozen=True, slots=True)
class SnapshotIntent:
    """Take a still picture now and send it (`media.snapshot`, of this intent's id)."""


type MediaIntent = MediaOpenIntent | MediaCloseIntent | SnapshotIntent
type Intent = BehaviorIntent | ExpressionIntent | BeatIntent | IdentifyIntent | TextIntent | UpdateIntent | MediaIntent


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
            case "intent.media.open":
                video, audio, max_s = payload["video"], payload["audio"], payload["max_s"]
                if not isinstance(video, bool) or not isinstance(audio, bool):
                    raise ValueError("expected booleans video and audio")
                if isinstance(max_s, bool) or not isinstance(max_s, int) or not 0 < max_s <= LASTING_MAX_S:
                    raise ValueError(f"expected max_s, an integer from 1 to {LASTING_MAX_S}")
                message = MediaOpenIntent(_session(payload["session"]), video, audio, max_s)
            case "intent.media.close":
                message = MediaCloseIntent(_session(payload["session"]))
            case "intent.media.snapshot":
                message = SnapshotIntent()
            case _:
                message = None
    except (KeyError, TypeError, ValueError) as error:
        raise ProtocolError(f"invalid {kind} payload {payload!r}: {error}") from None
    return Received(data["id"], message)


def _session(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= SESSION_MAX:
        raise ValueError(f"expected a session id of 1 to {SESSION_MAX} characters")
    return value


def _unit(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise ProtocolError(f"expected a number between 0 and 1, not {value!r}")
    return float(value)
