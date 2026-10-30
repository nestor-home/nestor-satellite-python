"""The SDK's copy of the protocol schemas, and what the SDK sends, against the server's source of truth.

When this fails after a server change, refresh the copy (see AGENTS.md) and adapt the SDK.
"""

import json
from importlib.resources import files
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from nestor.modules.behavior import Behavior as ServerBehavior
from nestor.modules.behavior import Expression as ServerExpression
from nestor.modules.satellites import wire as server
from referencing import Registry, Resource

from nestor_satellite import AUDIO_CAPTURE, PERSON_DETECTION, Behavior, Capability, Device, Expression, Manifest, wire

COPY = {p.name: json.loads(p.read_text()) for p in files("nestor_satellite").joinpath("schemas").iterdir()
        if p.name.endswith(".json")}  # fmt: skip
MANIFEST = Manifest(
    Device("enceinte-salon", "raspberry-pi-4", "salon"),
    (Capability(AUDIO_CAPTURE, {"formats": ["opus/48000"]}), Capability(PERSON_DETECTION, {"rate_hz": 10})),
)


def test_the_copied_schemas_are_the_servers() -> None:
    assert server.schemas() == COPY, "refresh src/nestor_satellite/schemas from ../nestor (AGENTS.md)"


def test_the_sdk_knows_every_value_the_server_may_send() -> None:
    assert {b.value for b in Behavior} == {b.value for b in ServerBehavior}
    assert {e.value for e in Expression} == {e.value for e in ServerExpression}


def _validator(name: str) -> Any:
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in COPY.values())
    return Draft202012Validator(COPY[name], registry=registry)


@pytest.mark.parametrize(
    "message",
    [wire.hello(MANIFEST), wire.ack("intent-1"), wire.person_detected(2, 0.75), wire.speech_activity(True)],
    ids=lambda m: m["type"],
)
def test_what_the_sdk_sends_follows_the_schema_and_the_server_reads_it(message: dict[str, Any]) -> None:
    _validator(f"{message['type']}.json").validate(message)
    assert server.decode(json.loads(json.dumps(message))).message.message_type == message["type"]


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        (server.Welcome(device_id="enceinte-salon", room="salon", capabilities=("audio.capture",)),
         wire.Welcome("enceinte-salon", "salon", frozenset({"audio.capture"}))),
        (server.BehaviorIntent(behavior=ServerBehavior.THINKING), wire.BehaviorIntent(Behavior.THINKING)),
        (server.ExpressionIntent(expression=ServerExpression.SIGH, intensity=0.4),
         wire.ExpressionIntent(Expression.SIGH, 0.4)),
        (server.BeatIntent(), wire.BeatIntent()),
    ],
    ids=["welcome", "behavior", "expression", "beat"],
)  # fmt: skip
def test_the_sdk_reads_what_the_server_sends(sent: server.Message, expected: object) -> None:
    data = json.loads(json.dumps(server.encode(sent, id="m-1")))
    assert wire.parse(data) == wire.Received("m-1", expected)  # ty: ignore[invalid-argument-type]
