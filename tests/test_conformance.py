"""The SDK against the Nestor server's black-box conformance suite (realtime profile)."""

import sys

import pytest
from nestor.adapters.channels.voice.satellites.conformance import Launch, check_satellite
from nestor.adapters.i18n import FluentLocalizer, catalogue_dirs

from nestor_satellite import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    EXPRESSION,
    HEAD_MOTION,
    IDENTIFY,
    PERSON_DETECTION,
    Body,
    Device,
    Manifest,
    RecordingSink,
    Satellite,
    SilenceSource,
    TokenStore,
)

ENGLISH = FluentLocalizer(catalogue_dirs(["nestor"]))  # the suite's checks, as its report names them

ROBOT = Manifest.of(
    Device("reachy-salon", "reachy-mini", "salon"), AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, HEAD_MOTION, IDENTIFY
)
SENSOR = Manifest.of(Device("capteur-entree", "esp32", "entree"), PERSON_DETECTION)


class Watcher(Body):
    """Reports someone at the door as soon as the link is up, as a person detector would; counts
    the times it was asked to make itself noticed."""

    def __init__(self, driver: "Driver") -> None:
        self.driver = driver
        self.sent: list[bool] = []
        self.identified = 0

    def connected(self, up: bool) -> None:
        satellite = self.driver.satellite
        if up and satellite is not None and PERSON_DETECTION in satellite.manifest.names:
            self.sent.append(satellite.person_detected(1, 0.93))

    def identify(self) -> None:
        self.identified += 1


class Driver:
    """A satellite built only with the SDK, as the suite launches it: a fresh device per scenario."""

    def __init__(self, manifest: Manifest, *, stale_token: bool = False) -> None:
        self.manifest, self.stale_token = manifest, stale_token
        self.sink = RecordingSink()
        self.satellite: Satellite | None = None
        self.body = Watcher(self)
        self.launches: list[Launch] = []

    async def __call__(self, launch: Launch) -> None:
        self.launches.append(launch)
        tokens = TokenStore(launch.state / "token.json")
        if self.stale_token:  # paired with a previous core: refused, then paired again
            tokens.save(launch.url, self.manifest.device.id, "nst1_from-another-life")
        self.satellite = Satellite(launch.url, self.manifest, tokens=tokens, body=self.body, source=SilenceSource(),
                                   sink=self.sink, enrollment=launch.pairing_uri, environ={}, poll_s=0.1)  # fmt: skip
        await self.satellite.run()


@pytest.mark.parametrize("manifest", [ROBOT, SENSOR], ids=["robot that can identify", "sensor without audio"])
async def test_a_satellite_written_with_the_sdk_conforms(manifest: Manifest) -> None:
    driver = Driver(manifest)
    findings = await check_satellite(driver, deadline_s=10)

    assert findings.passed, findings.failures()
    checks = {ENGLISH.format(f.check, "en") for f in findings.items}
    assert {
        "reconnects with the same token after a hang-up (W4)",
        "pairs with the enrollment code of NESTOR_PAIRING_URI, no code typed (P3, P4)",
        "presents a refused enrollment code (used) only once (P5)",
        "waits for Retry-After when rate-limited (P6)",
    } <= checks
    identifies = {"keeps polling after a pending identify (I1)", "acknowledges intent.identify within 1 s"}
    assert (identifies <= checks) is (IDENTIFY in manifest.names)
    assert (driver.body.identified >= 2) is (IDENTIFY in manifest.names), "once pending, once paired"
    assert (len(driver.sink.played) > 0) is (AUDIO_PLAYBACK in manifest.names), "the core's audio reached the sink"
    assert driver.body.sent[:2] == ([True, True] if PERSON_DETECTION in manifest.names else []), "and it was valid"


async def test_a_refused_token_is_forgotten_and_the_device_pairs_again() -> None:
    findings = await check_satellite(Driver(ROBOT, stale_token=True), deadline_s=10)
    assert findings.passed, findings.failures()


async def test_the_command_line_satellite_conforms_as_a_program() -> None:
    command = [sys.executable, "-c", "from nestor_satellite.cli import main; main()", "run", "--core", "{url}",
               "--room", "salon", "--id", "pi-salon", "--token-file", "{state}/token.json"]  # fmt: skip
    findings = await check_satellite(_without_sound_card(command), deadline_s=15)
    assert findings.passed, findings.failures()


def _without_sound_card(command: list[str]) -> list[str]:
    """The command, with PortAudio replaced by silence: CI machines have no sound card."""
    stub = (
        "import sys, types; from nestor_satellite import SilenceSource, RecordingSink; "
        "import nestor_satellite.devices as d; d.Microphone = lambda **_: SilenceSource(); "
        "d.Speaker = lambda **_: RecordingSink(); "
    )
    return [command[0], "-c", stub + command[2], *command[3:]]
