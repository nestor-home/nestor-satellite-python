from typing import Any

import pytest

from nestor_satellite import Capability, Device, Expression, ProtocolError, wire


def message(kind: str, payload: Any) -> dict[str, Any]:
    return {"type": kind, "id": "m-1", "at": "2026-10-30T12:00:00+00:00", "payload": payload}


def test_a_message_type_this_sdk_does_not_know_is_read_as_nothing() -> None:
    assert wire.parse(message("intent.dance", {"style": "tango"})) == wire.Received("m-1", None)


def test_an_expression_without_intensity_is_at_full_intensity() -> None:
    received = wire.parse(message("intent.expression", {"expression": "joy"}))
    assert received.message == wire.ExpressionIntent(Expression.JOY, 1.0)


@pytest.mark.parametrize(
    "data",
    [
        ["not", "a", "message"],
        {"type": "intent.beat", "payload": {}},
        message("intent.beat", ["not", "an", "object"]),
        message("intent.behavior", {"behavior": "dancing"}),
        message("intent.expression", {"expression": "joy", "intensity": 3}),
        message("intent.expression", {"expression": "joy", "intensity": True}),
        message("welcome", {"room": "salon"}),
    ],
    ids=["not an object", "no id", "payload not an object", "unknown behavior", "too intense", "boolean", "partial"],
)
def test_a_malformed_message_from_the_core_is_a_protocol_error(data: Any) -> None:
    with pytest.raises(ProtocolError):
        wire.parse(data)


@pytest.mark.parametrize(("count", "confidence"), [(-1, 0.5), (True, 0.5), (1.5, 0.5), (1, 1.2), (1, "high")])
def test_an_invalid_observation_is_refused_before_it_is_sent(count: Any, confidence: Any) -> None:
    with pytest.raises(ProtocolError):
        wire.person_detected(count, confidence)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Device("Salon TV", "tv", "salon"),
        lambda: Device("tv", "tv", "salon:2"),
        lambda: Device("tv", "", "salon"),
        lambda: Capability("camera"),
        lambda: Capability("audio.capture", {"name": "other"}),
    ],
    ids=["id with spaces", "room with a colon", "no model", "no family", "name in params"],
)
def test_a_manifest_that_the_core_would_refuse_cannot_be_built(build: Any) -> None:
    with pytest.raises(ProtocolError):
        build()
