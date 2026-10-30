"""The SDK against the Nestor server's black-box conformance suite (realtime profile)."""

from pathlib import Path

import pytest
from nestor.adapters.channels.voice.satellites.conformance import check_satellite

from nestor_satellite import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    EXPRESSION,
    HEAD_MOTION,
    PERSON_DETECTION,
    Body,
    Device,
    Manifest,
    RecordingSink,
    Satellite,
    SilenceSource,
    TokenStore,
)

ROBOT = Manifest.of(
    Device("reachy-salon", "reachy-mini", "salon"), AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, HEAD_MOTION
)
SENSOR = Manifest.of(Device("capteur-entree", "esp32", "entree"), PERSON_DETECTION)


class Watcher(Body):
    """Reports someone at the door as soon as the link is up, as a person detector would."""

    def __init__(self, driver: "Driver") -> None:
        self.driver = driver
        self.sent: list[bool] = []

    def connected(self, up: bool) -> None:
        satellite = self.driver.satellite
        if up and satellite is not None and PERSON_DETECTION in satellite.manifest.names:
            self.sent.append(satellite.person_detected(1, 0.93))


class Driver:
    """A satellite built only with the SDK, as the suite launches it (a coroutine taking the core's URL)."""

    def __init__(self, manifest: Manifest, tokens: Path, *, stale_token: bool = False) -> None:
        self.manifest, self.tokens, self.stale_token = manifest, TokenStore(tokens), stale_token
        self.sink = RecordingSink()
        self.codes: list[str] = []
        self.satellite: Satellite | None = None
        self.body = Watcher(self)

    async def __call__(self, url: str) -> None:
        if self.stale_token:  # paired with a previous core: refused, then paired again
            self.tokens.save(url, self.manifest.device.id, "nst1_from-another-life")
        self.satellite = Satellite(url, self.manifest, tokens=self.tokens, body=self.body, source=SilenceSource(),
                                   sink=self.sink, on_code=self.codes.append, poll_s=0.1)  # fmt: skip
        await self.satellite.run()


@pytest.mark.parametrize("manifest", [ROBOT, SENSOR], ids=["robot", "sensor without audio"])
async def test_a_satellite_written_with_the_sdk_conforms(manifest: Manifest, tmp_path: Path) -> None:
    driver = Driver(manifest, tmp_path / "token.json")
    findings = await check_satellite(driver, deadline_s=10)

    assert findings.passed, findings.failures()
    assert "reconnects with the same token after a hang-up" in {f.check for f in findings.items}
    assert len(driver.codes) == 1, "paired once: the reconnection reused the stored token"
    assert (len(driver.sink.played) > 0) is (AUDIO_PLAYBACK in manifest.names), "the core's audio reached the sink"
    assert driver.body.sent == ([True, True] if PERSON_DETECTION in manifest.names else []), "and it was valid"


async def test_a_refused_token_is_forgotten_and_the_device_pairs_again(tmp_path: Path) -> None:
    driver = Driver(ROBOT, tmp_path / "token.json", stale_token=True)
    findings = await check_satellite(driver, deadline_s=10)
    assert findings.passed, findings.failures()
    assert len(driver.codes) == 1
