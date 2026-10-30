"""What the conformance suite does not see: how a satellite is configured to pair, observation rules,
and a core that cannot be reached or cannot pair it."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from nestor_satellite import (
    AUDIO_CAPTURE,
    PAIRING_CODE,
    PAIRING_URI,
    PERSON_DETECTION,
    SPEECH_ACTIVITY,
    CapabilityNotAllowed,
    Device,
    Display,
    EnrollmentOutcome,
    InvalidEnrollment,
    Manifest,
    PairingFailed,
    PairingMethod,
    Satellite,
    SilenceSource,
    TokenStore,
    wire,
)

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


def test_its_pairing_methods_follow_from_its_outputs_and_inputs(tmp_path: Path) -> None:
    every = satellite(tmp_path, display=Screen(), voice=print, enrolls_by=[PairingMethod.QR, PairingMethod.INPUT])
    assert every.manifest.pairing_methods == tuple(PairingMethod)
    spoken = satellite(tmp_path, voice=print)
    assert spoken.manifest.pairing_methods == (PairingMethod.SPEECH, PairingMethod.PROVISIONING)


def test_a_device_code_method_is_not_an_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="enrolls_by"):
        satellite(tmp_path, enrolls_by=[PairingMethod.DISPLAY])


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
    with pytest.raises(ValueError, match="enrolls_by"):
        await sensor.enroll("K7QF2M9X", method=PairingMethod.QR)
    with pytest.raises(ValueError, match="enrolls_by"):
        await sensor.enroll("K7QF2M9X", method=PairingMethod.DISPLAY)


async def test_what_is_not_an_enrollment_code_is_said_so(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    sensor = satellite(tmp_path, enrolls_by=[PairingMethod.INPUT])
    assert await sensor.enroll("hello") is EnrollmentOutcome.INVALID
    assert "This is not an enrollment code." in caplog.text


async def test_a_paired_satellite_has_nothing_to_enroll(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, enrolls_by=[PairingMethod.QR])
    sensor.tokens.save(sensor.core, DEVICE.id, "nst2_paired")
    assert await sensor.enroll(URI, method=PairingMethod.QR) is EnrollmentOutcome.NOT_PENDING


def test_an_observation_needs_its_capability_declared(tmp_path: Path) -> None:
    sensor = satellite(tmp_path, PERSON_DETECTION)
    with pytest.raises(CapabilityNotAllowed):
        sensor.speech_activity(True)
    assert sensor.person_detected(1, 0.9) is False, "not connected: nothing is queued"


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


async def test_an_unreachable_core_is_retried_until_cancelled_and_audio_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = CountingSource()
    speaker = satellite(tmp_path, AUDIO_CAPTURE, source=source, backoff_s=(0.01, 0.05), locale="fr")
    running = asyncio.create_task(speaker.run())
    async with asyncio.timeout(10):
        while caplog.text.count("La liaison avec Nestor a échoué") < 3:
            await asyncio.sleep(0.01)
    assert not running.done(), "a core that is down is not a reason to stop"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (source.opened, source.closed) == (1, 1)
    assert "Pairing code" not in caplog.text


async def test_a_core_that_cannot_pair_this_device_stops_it_and_audio_is_released(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    answer = {"error": "protocol_unsupported", "detail": "the device speaks nestor.satellite/2, this core only /3"}
    source = CountingSource()
    sensor = satellite(tmp_path, AUDIO_CAPTURE, source=source,
                       transport=httpx.MockTransport(lambda _request: httpx.Response(422, json=answer)))  # fmt: skip
    with pytest.raises(PairingFailed, match="update the device or Nestor"):
        await sensor.run()
    assert (source.opened, source.closed) == (1, 1)
    assert "does not speak this device's protocol version" in caplog.text


async def test_a_code_typed_on_a_waiting_satellite_pairs_it(tmp_path: Path) -> None:
    from nestor.modules.satellites import PairingMethod as ServerMethod

    from .test_pairing import Core

    core = Core()
    screen = Screen()
    keypad = satellite(tmp_path, core="http://nestor.local:7860", transport=core, display=screen,
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
