"""What the SDK sends, against its copy of the protocol schemas, and what the server reads of it, and back."""

import json
from importlib.resources import files
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from nestor.modules.behavior import Behavior as ServerBehavior
from nestor.modules.behavior import Expression as ServerExpression
from nestor.modules.satellites import wire as server
from referencing import Registry, Resource

from nestor_satellite import (
    AUDIO_CAPTURE,
    PERSON_DETECTION,
    Behavior,
    Capability,
    Device,
    Expression,
    Manifest,
    PairingMethod,
    UpdateState,
    wire,
)

COPY = {p.name: json.loads(p.read_text()) for p in files("nestor_satellite").joinpath("schemas").iterdir()
        if p.name.endswith(".json")}  # fmt: skip
MANIFEST = Manifest(
    Device("enceinte-salon", "raspberry-pi-4", "salon", brand="raspberry"),
    (Capability(AUDIO_CAPTURE, {"formats": ["opus/48000"]}), Capability(PERSON_DETECTION, {"rate_hz": 10})),
    (PairingMethod.SPEECH, PairingMethod.PROVISIONING),
)


def _received(sent: server.Message) -> wire.Received:
    return wire.parse(json.loads(json.dumps(server.encode(sent, id="m-1"))))


def test_the_sdk_reads_every_behavior_and_expression_the_server_may_send() -> None:
    for behavior in ServerBehavior:
        expected = wire.BehaviorIntent(Behavior(behavior.value))
        assert _received(server.BehaviorIntent(behavior=behavior)) == wire.Received("m-1", expected)
    for expression in ServerExpression:
        expected = wire.ExpressionIntent(Expression(expression.value), 0.5)
        sent = server.ExpressionIntent(expression=expression, intensity=0.5)
        assert _received(sent) == wire.Received("m-1", expected)


def test_the_server_reads_every_pairing_method_the_sdk_declares() -> None:
    manifest = Manifest(MANIFEST.device, MANIFEST.capabilities, tuple(PairingMethod))
    assert server.Manifest.from_json(manifest.to_json()).to_json() == manifest.to_json()
    for method in (m for m in PairingMethod if m.enrollment):
        proof = server.EnrollmentProof.from_json({"enrollment_code": "ABCD2345", "method": method.value})
        assert proof.method.value == method.value


def _validator(name: str) -> Any:
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in COPY.values())
    return Draft202012Validator(COPY[name], registry=registry)


@pytest.mark.parametrize(
    "message",
    [
        wire.hello(MANIFEST, "nst1_token"),
        wire.ack("intent-1"),
        wire.person_detected(2, 0.75),
        wire.speech_activity(True),
        wire.text_input("Allume la lumière"),
        wire.text_input("x" * wire.TYPED_MAX),
        wire.update_status(UpdateState.CURRENT),
        wire.update_status(UpdateState.AVAILABLE, "0.6.0"),
    ],
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
        (server.TextIntent(text="Il fait 19 °C."), wire.TextIntent("Il fait 19 °C.", True)),
        (server.TextIntent(text="Je regarde…", final=False), wire.TextIntent("Je regarde…", False)),
        (server.TextIntent(text="x" * wire.SHOWN_MAX), wire.TextIntent("x" * wire.SHOWN_MAX)),
        (server.UpdateIntent(version="0.6.0"), wire.UpdateIntent("0.6.0")),
    ],
    ids=["welcome", "behavior", "expression", "beat", "identify", "text", "text in progress", "longest text",
         "update"],
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
        httpx.Response(200, json={"status": "approved", "token": "nst1_ok"}),
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
