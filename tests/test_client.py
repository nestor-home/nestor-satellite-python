"""What the conformance suite does not see: how a satellite is configured, what its manifest claims,
observation rules, a core that cannot be reached or cannot pair it, and how the link behaves when the
core closes it."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
from nestor.modules.satellites import wire as server

from nestor_satellite import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    IDENTIFY,
    PAIRING_CODE,
    PAIRING_URI,
    PERSON_DETECTION,
    SPEECH_ACTIVITY,
    TEXT_DISPLAY,
    TEXT_INPUT,
    Body,
    Camera,
    CapabilityNotAllowed,
    Device,
    Display,
    EnrollmentOutcome,
    Frame,
    InvalidEnrollment,
    Manifest,
    PairingFailed,
    PairingMethod,
    RecordingSink,
    Satellite,
    SilenceSource,
    TokenStore,
    wire,
)

from .core import Core

DEVICE = Device("capteur-entree", "esp32", "entree")
URI = "nestor+https://nestor.example.org/proxy/?code=K7QF-2M9X"


class Screen(Display):
    def show(self, text: str, code: str | None = None) -> None: ...

    def clear(self) -> None: ...


def satellite(tmp_path: Path, *capabilities: str, core: str | None = "http://127.0.0.1:9", **options: Any) -> Satellite:
    options.setdefault("environ", {})
    return Satellite(core, Manifest.of(DEVICE, *capabilities), tokens=TokenStore(tmp_path / "t.json"), **options)


def test_a_satellite_without_screen_or_voice_can_only_be_provisioned(tmp_path: Path) -> None:
    assert satellite(tmp_path).manifest.pairing_methods == (PairingMethod.PROVISIONING,)


class Lens(Camera):
    async def read(self) -> Frame:
        return Frame(1, 1, b"\xff")


def test_its_pairing_methods_follow_from_its_outputs_and_inputs(tmp_path: Path) -> None:
    every = satellite(tmp_path, display=Screen(), voice=print, camera=Lens(), enrolls_by=[PairingMethod.INPUT])
    assert every.manifest.pairing_methods == tuple(PairingMethod)
    spoken = satellite(tmp_path, voice=print)
    assert spoken.manifest.pairing_methods == (PairingMethod.SPEECH, PairingMethod.PROVISIONING)


def test_a_speaker_without_a_screen_says_its_code_with_the_sdks_voice(tmp_path: Path) -> None:
    speaker = satellite(tmp_path, sink=RecordingSink())
    assert speaker.manifest.pairing_methods == (PairingMethod.SPEECH, PairingMethod.PROVISIONING)


@pytest.mark.parametrize(
    "options", [{}, {"sink": RecordingSink(), "display": Screen()}], ids=["no speaker", "a screen"]
)
def test_without_a_speaker_or_with_a_screen_the_code_is_not_spoken(tmp_path: Path, options: dict[str, Any]) -> None:
    assert PairingMethod.SPEECH not in satellite(tmp_path, **options).manifest.pairing_methods


@pytest.mark.parametrize(
    ("options", "declared"),
    [
        ({}, set()),
        ({"text_display": lambda _text, _final: None}, {TEXT_DISPLAY}),
        ({"text_input": True}, {TEXT_INPUT}),
        ({"source": SilenceSource()}, {AUDIO_CAPTURE}),
        ({"sink": RecordingSink()}, {AUDIO_PLAYBACK}),
    ],
    ids=["nothing", "a text display", "typing", "a microphone", "a speaker"],
)
def test_text_and_audio_capabilities_follow_from_what_the_satellite_is_given(
    tmp_path: Path, options: dict[str, Any], declared: set[str]
) -> None:
    assert satellite(tmp_path, IDENTIFY, **options).manifest.names == declared | {IDENTIFY}


@pytest.mark.parametrize("claimed", [TEXT_DISPLAY, TEXT_INPUT, AUDIO_CAPTURE, AUDIO_PLAYBACK])
def test_a_manifest_cannot_claim_text_or_audio_the_satellite_was_not_given(tmp_path: Path, claimed: str) -> None:
    with pytest.raises(ValueError, match="follow from what the satellite is given"):
        satellite(tmp_path, claimed)


def test_a_qr_code_is_declared_only_with_a_camera_to_read_it(tmp_path: Path) -> None:
    for method in (PairingMethod.QR, PairingMethod.DISPLAY):
        with pytest.raises(ValueError, match="enrolls_by"):
            satellite(tmp_path, enrolls_by=[method])
    assert PairingMethod.QR not in satellite(tmp_path, enrolls_by=[PairingMethod.INPUT]).manifest.pairing_methods


def test_a_pairing_uri_in_the_environment_says_where_the_core_is(tmp_path: Path) -> None:
    headless = satellite(tmp_path, core=None, environ={PAIRING_URI: URI})
    assert headless.core == "https://nestor.example.org/proxy"
    assert satellite(tmp_path, environ={PAIRING_URI: URI}).core == "http://127.0.0.1:9", "an explicit address wins"


def test_an_enrollment_code_alone_needs_the_cores_address(tmp_path: Path) -> None:
    assert satellite(tmp_path, environ={PAIRING_CODE: "K7QF2M9X"}).core == "http://127.0.0.1:9"
    with pytest.raises(ValueError, match="NESTOR_PAIRING_URI"):
        satellite(tmp_path, core=None, environ={PAIRING_CODE: "K7QF2M9X"})


def test_a_misconfigured_enrollment_fails_at_once_naming_where_it_came_from(tmp_path: Path) -> None:
    with pytest.raises(InvalidEnrollment, match="NESTOR_PAIRING_URI n'est ni"):
        satellite(tmp_path, environ={PAIRING_URI: "http://nestor.local/?code=K7QF2M9X", "LANG": "fr_FR.UTF-8"})
    with pytest.raises(InvalidEnrollment, match="enrollment is not"):
        satellite(tmp_path, enrollment="hello", environ={PAIRING_URI: URI})


async def test_a_code_can_only_be_handed_in_the_way_the_satellite_declared(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, enrolls_by=[PairingMethod.INPUT])
    with pytest.raises(ValueError, match="enrollment methods"):
        await sensor.enroll("K7QF2M9X", method=PairingMethod.QR)
    with pytest.raises(ValueError, match="enrollment methods"):
        await sensor.enroll("K7QF2M9X", method=PairingMethod.DISPLAY)


async def test_what_is_not_an_enrollment_code_is_said_so(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    sensor = satellite(tmp_path, enrolls_by=[PairingMethod.INPUT])
    assert await sensor.enroll("hello") is EnrollmentOutcome.INVALID
    assert "This is not an enrollment code." in caplog.text


async def test_a_paired_satellite_has_nothing_to_enroll(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, enrolls_by=[PairingMethod.INPUT])
    sensor.tokens.save(sensor.core, DEVICE.id, "nst1_paired")
    assert await sensor.enroll(URI) is EnrollmentOutcome.NOT_PENDING


def test_an_observation_needs_its_capability_declared(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, PERSON_DETECTION)
    with pytest.raises(CapabilityNotAllowed):
        sensor.speech_activity(True)
    assert sensor.person_detected(1, 0.9) is False, "not connected: nothing is queued"


def test_typed_text_needs_typing_given_and_allowed(tmp_path: Path) -> None:
    with pytest.raises(CapabilityNotAllowed):
        satellite(tmp_path).send_text("Bonjour")
    keyboard = satellite(tmp_path, text_input=True)
    assert keyboard.send_text("Bonjour") is False, "not connected: nothing is queued"
    keyboard.welcome = wire.Welcome(DEVICE.id, "entree", frozenset())
    with pytest.raises(CapabilityNotAllowed):
        keyboard.send_text("Bonjour")


def test_an_observation_the_household_did_not_allow_is_refused(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, PERSON_DETECTION, SPEECH_ACTIVITY)
    sensor.welcome = wire.Welcome(DEVICE.id, "entree", frozenset({SPEECH_ACTIVITY}))
    with pytest.raises(CapabilityNotAllowed):
        sensor.person_detected(1, 0.9)
    assert sensor.speech_activity(False) is False


class CountingSource(SilenceSource):
    def __init__(self) -> None:
        self.opened = self.closed = 0

    async def start(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1


class CountingSink(RecordingSink):
    def __init__(self) -> None:
        super().__init__()
        self.opened = self.closed = 0

    async def start(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1


async def test_an_unreachable_core_is_retried_until_cancelled_and_audio_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source, sink = CountingSource(), CountingSink()
    speaker = satellite(tmp_path, source=source, sink=sink, display=Screen(), backoff_s=(0.01, 0.05), locale="fr")
    running = asyncio.create_task(speaker.run())
    async with asyncio.timeout(10):
        while caplog.text.count("La liaison avec Nestor a échoué") < 3:
            await asyncio.sleep(0.01)
    assert not running.done(), "a core that is down is not a reason to stop"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (sink.opened, sink.closed) == (1, 1)
    assert source.opened == 0, "the microphone opens for an audio connection only"
    assert "Pairing code" not in caplog.text


async def test_a_core_that_cannot_pair_this_device_stops_it_and_audio_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    answer = {"error": "protocol_unsupported", "detail": "the device speaks nestor.satellite/1, this core only /2"}
    sink = CountingSink()
    sensor = satellite(tmp_path, sink=sink, display=Screen(),
                       transport=httpx.MockTransport(lambda _request: httpx.Response(422, json=answer)))  # fmt: skip
    with pytest.raises(PairingFailed, match="update the device or Nestor"):
        await sensor.run()
    assert (sink.opened, sink.closed) == (1, 1)
    assert "does not speak this device's protocol version" in caplog.text


async def test_a_code_typed_on_a_waiting_satellite_pairs_it(tmp_path: Path) -> None:
    from nestor.modules.satellites import PairingMethod as ServerMethod

    from .test_pairing import Core as PairingCore

    core = PairingCore()
    screen = Screen()
    keypad = satellite(tmp_path, core="http://nestor.local:8471", transport=core, display=screen,
                       enrolls_by=[PairingMethod.INPUT], poll_s=0.02, backoff_s=(5.0, 5.0))  # fmt: skip
    running = asyncio.create_task(keypad.run())
    while not core.service.pending():
        await asyncio.sleep(0.01)
    started = await core.service.start_addition(ServerMethod.INPUT)
    assert started.code is not None
    try:
        assert await keypad.enroll(started.code.lower()) is EnrollmentOutcome.ACCEPTED
        while keypad.tokens.load(keypad.core, DEVICE.id) is None:
            await asyncio.sleep(0.01)
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    [paired] = core.service.satellites()
    assert paired.id == DEVICE.id


# The link, against the server's gateway on a local port.


class LinkWatcher(Body):
    """A body that notes when the link comes up and goes down."""

    def __init__(self) -> None:
        self.links: list[bool] = []

    def connected(self, up: bool) -> None:
        self.links.append(up)


@contextlib.asynccontextmanager
async def running(device: Satellite) -> AsyncGenerator[asyncio.Task[None]]:
    task = asyncio.create_task(device.run())
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def linked(core: Core, tmp_path: Path, uri: str | None, *capabilities: str, **options: Any) -> Satellite:
    options.setdefault("backoff_s", (0.05, 0.2))
    return satellite(tmp_path, *capabilities, core=core.url, enrollment=uri, poll_s=0.05, **options)


async def test_text_goes_both_ways_over_the_link(tmp_path: Path) -> None:
    shown: list[tuple[str, bool]] = []
    body = LinkWatcher()
    async with Core().serving() as core:
        uri = await core.enrollment()
        device = linked(core, tmp_path, uri, IDENTIFY, body=body, text_input=True,
                        text_display=lambda text, final: shown.append((text, final)))  # fmt: skip
        async with running(device):
            link = await core.next_link()
            assert link.allowed == {TEXT_INPUT, TEXT_DISPLAY, IDENTIFY}
            while device.welcome is None:
                await asyncio.sleep(0.01)
            assert device.welcome.room == DEVICE.room and body.links == [True]

            await link.send(server.TextIntent(text="Je regarde…", final=False), id="t-1")
            await link.send(server.TextIntent(text="Il fait 19 °C."), id="t-2")
            assert [(await link.next())["payload"] for _ in range(2)] == [{"of": "t-1"}, {"of": "t-2"}]
            assert shown == [("Je regarde…", False), ("Il fait 19 °C.", True)]

            assert device.send_text("Allume la lumière du salon") is True
            typed = server.decode(await link.next()).message
            assert typed == server.TextInput(text="Allume la lumière du salon")
        assert body.links == [True, False]


async def test_a_refused_token_is_forgotten_and_the_device_pairs_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with Core().serving() as core:
        uri = await core.enrollment()
        device = linked(core, tmp_path, uri)
        device.tokens.save(core.url, DEVICE.id, "nst1_from-another-life")
        async with running(device):
            await core.next_link()
            assert device.tokens.load(core.url, DEVICE.id) != "nst1_from-another-life"
    assert "Nestor no longer knows this device" in caplog.text


async def test_a_link_replaced_by_a_newer_one_stops(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    async with Core().serving() as core:
        uri = await core.enrollment()
        first = linked(core, tmp_path, uri)
        async with running(first) as task:
            await core.next_link()
            second = linked(core, tmp_path, None)  # the same device, started twice
            async with running(second):
                await core.next_link()
                async with asyncio.timeout(5):
                    await task  # returns: the newer link is the current one
                assert second.connections == 1
    assert "took over" in caplog.text


@pytest.mark.parametrize(("code", "said"), [(1001, ""), (4400, "refused this device's greeting")])
async def test_a_link_the_core_closes_is_opened_again_with_the_same_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, code: int, said: str
) -> None:
    async with Core().serving() as core:
        device = linked(core, tmp_path, await core.enrollment())
        async with running(device):
            link = await core.next_link()
            token = device.tokens.load(core.url, DEVICE.id)
            await link.close(code)
            again = await core.next_link()
            assert again.satellite.id == DEVICE.id
            assert (device.tokens.load(core.url, DEVICE.id), device.connections) == (token, 2)
    assert said in caplog.text


async def test_a_device_allowed_no_audio_offers_none(tmp_path: Path) -> None:
    async with Core().serving() as core:
        uri = await core.enrollment(capabilities=[IDENTIFY])
        device = linked(core, tmp_path, uri, IDENTIFY, source=SilenceSource(), sink=RecordingSink())
        async with running(device):
            link = await core.next_link()
            assert link.allowed == {IDENTIFY}
            await asyncio.sleep(0.5)
    assert core.audio_offers == 0


async def test_failed_audio_is_offered_again_and_a_refused_token_pairs_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = CountingSource()
    async with Core().serving() as core:  # this core has no voice: it refuses every offer (403)
        device = linked(core, tmp_path, await core.enrollment(), source=source, sink=RecordingSink(), display=Screen())
        async with running(device):
            link = await core.next_link()
            assert link.allowed == {AUDIO_CAPTURE, AUDIO_PLAYBACK}
            async with asyncio.timeout(10):
                while core.audio_offers < 2:
                    await asyncio.sleep(0.02)
            assert "Audio with Nestor failed" in caplog.text
            await core.service.revoke(DEVICE.id)
            async with asyncio.timeout(10):
                while not core.service.pending():  # 401 on the next offer: it asks for a device code
                    await asyncio.sleep(0.02)
    assert source.opened >= 2 and source.opened == source.closed, "the microphone, for each offer only"
    assert "Nestor no longer knows this device" in caplog.text


async def test_a_microphone_that_cannot_open_leaves_no_connection_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    from nestor_satellite import webrtc

    class Refused(SilenceSource):
        async def start(self) -> None:
            raise OSError("the microphone is busy")

    peers: list[object] = []
    monkeypatch.setattr(webrtc, "RTCPeerConnection", lambda *args, **kwargs: peers.append(args) or None)
    async with httpx.AsyncClient(base_url="http://core") as http:
        with pytest.raises(OSError, match="busy"):
            await webrtc.WebRtcAudio().connect(http, "enceinte", "nst1_x", source=Refused(), sink=None)
    assert not peers, "no peer connection is opened for a microphone that failed"
