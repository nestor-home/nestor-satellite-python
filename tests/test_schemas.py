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
from nestor.modules.satellites import PROTOCOL as ServerProtocol
from nestor.modules.satellites import PairingMethod as ServerMethod
from nestor.modules.satellites import wire as server
from referencing import Registry, Resource

from nestor_satellite import (
    AUDIO_CAPTURE,
    PERSON_DETECTION,
    PROTOCOL,
    Behavior,
    Capability,
    Device,
    Expression,
    Manifest,
    PairingMethod,
    wire,
)

COPY = {p.name: json.loads(p.read_text()) for p in files("nestor_satellite").joinpath("schemas").iterdir()
        if p.name.endswith(".json")}  # fmt: skip
MANIFEST = Manifest(
    Device("enceinte-salon", "raspberry-pi-4", "salon", brand="raspberry"),
    (Capability(AUDIO_CAPTURE, {"formats": ["opus/48000"]}), Capability(PERSON_DETECTION, {"rate_hz": 10})),
    (PairingMethod.SPEECH, PairingMethod.PROVISIONING),
)


def test_the_copied_schemas_are_the_servers() -> None:
    assert server.schemas() == COPY, "refresh src/nestor_satellite/schemas from ../nestor (AGENTS.md)"


def test_the_sdk_knows_every_value_the_server_may_send() -> None:
    assert {b.value for b in Behavior} == {b.value for b in ServerBehavior}
    assert {e.value for e in Expression} == {e.value for e in ServerExpression}
    assert [m.value for m in PairingMethod] == [m.value for m in ServerMethod]
    assert {m.value for m in PairingMethod if m.enrollment} == {
        m.value for m in ServerMethod if m.proof.value == "enrollment_code"
    }
    assert ServerProtocol == PROTOCOL


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
        (server.IdentifyIntent(), wire.IdentifyIntent()),
    ],
    ids=["welcome", "behavior", "expression", "beat", "identify"],
)  # fmt: skip
def test_the_sdk_reads_what_the_server_sends(sent: server.Message, expected: object) -> None:
    data = json.loads(json.dumps(server.encode(sent, id="m-1")))
    assert wire.parse(data) == wire.Received("m-1", expected)  # ty: ignore[invalid-argument-type]


def test_the_manifest_the_sdk_sends_is_the_one_the_server_reads() -> None:
    assert server.Manifest.from_json(MANIFEST.to_json()).to_json() == MANIFEST.to_json()


def test_the_pairing_bodies_the_sdk_sends_follow_their_schemas(tmp_path: Any) -> None:
    """Every body sent while pairing, recorded on a scripted core, against the published schemas."""
    import asyncio

    import httpx

    from nestor_satellite.i18n import Messages
    from nestor_satellite.pairing import Pairing

    sent: list[tuple[str, Any]] = []
    answers = [
        httpx.Response(403, json={"error": "enrollment_refused", "detail": "no", "reason": "unknown"}),
        httpx.Response(201, json={"pairing_id": "p1", "status": "pending", "expires_at": "2026-10-30T12:00:00+00:00",
                                  "device_code": "042917"}),
        httpx.Response(200, json={"status": "pending"}),
        httpx.Response(200, json={"status": "approved"}),
        httpx.Response(200, json={"status": "approved", "token": "nst2_ok"}),
    ]  # fmt: skip

    def core(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append((request.url.path, json.loads(request.content)))
        return answers.pop(0)

    manifest = Manifest(MANIFEST.device, MANIFEST.capabilities, (PairingMethod.INPUT, PairingMethod.PROVISIONING))
    device = Pairing(manifest, messages=Messages("en"), enrollment="K7QF2M9X", poll_s=0.05)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(core), base_url="http://n") as http:
            pairing = asyncio.create_task(device.pair(http))
            while len(answers) > 2:  # until it waits with its device code
                await asyncio.sleep(0.01)
            await device.enroll("ABCD2345", PairingMethod.INPUT)
            await pairing

    asyncio.run(scenario())
    schemas = {
        "/satellites/pairings": "pairing.request.json",
        "/satellites/pairings/p1/enrollment": "pairing.enrollment.json",
    }
    for path, body in sent:
        _validator(schemas[path]).validate(body)
    pair, enroll = "/satellites/pairings", "/satellites/pairings/p1/enrollment"
    assert [path for path, _ in sent] == [pair, pair, enroll], "with its code, without once refused, then the typed one"
    assert server.PairingRequest.from_json(sent[0][1]).enrollment_code == "K7QF2M9X"
    assert server.PairingRequest.from_json(sent[1][1]).enrollment_code is None
    assert server.EnrollmentProof.from_json(sent[2][1]).method.value == "enrollment.input"
