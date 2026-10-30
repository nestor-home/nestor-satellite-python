"""What the conformance suite does not see: observations rules and a core that cannot be reached."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nestor_satellite import (
    AUDIO_CAPTURE,
    PERSON_DETECTION,
    SPEECH_ACTIVITY,
    CapabilityNotAllowed,
    Device,
    Manifest,
    Satellite,
    SilenceSource,
    TokenStore,
    wire,
)

DEVICE = Device("capteur-entree", "esp32", "entree")


def satellite(
    tmp_path: Path,
    *capabilities: str,
    source: SilenceSource | None = None,
    on_code: Any = print,
    backoff_s: tuple[float, float] = (0.5, 30.0),
) -> Satellite:
    return Satellite("http://127.0.0.1:9", Manifest.of(DEVICE, *capabilities), tokens=TokenStore(tmp_path / "t.json"),
                     source=source, on_code=on_code, backoff_s=backoff_s)  # fmt: skip


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


async def test_an_unreachable_core_is_retried_until_cancelled_and_audio_is_released(tmp_path: Path) -> None:
    source, codes = CountingSource(), []
    speaker = satellite(tmp_path, AUDIO_CAPTURE, source=source, on_code=codes.append, backoff_s=(0.01, 0.05))
    running = asyncio.create_task(speaker.run())
    await asyncio.sleep(0.3)
    assert not running.done(), "a core that is down is not a reason to stop"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert (source.opened, source.closed, codes) == (1, 1, [])
