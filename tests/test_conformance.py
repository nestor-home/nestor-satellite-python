"""The SDK against the Nestor server's black-box conformance suite."""

import sys
from typing import Any

import pytest
from nestor.adapters.channels.satellites.conformance import Launch, check_satellite
from nestor.adapters.i18n import FluentLocalizer, catalogue_dirs

from nestor_satellite import (
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
    Updater,
)

from .test_update import Source

ENGLISH = FluentLocalizer(catalogue_dirs(["nestor"]))  # the suite's checks, as its report names them

ROBOT = Manifest.of(Device("reachy-salon", "reachy-mini", "salon"), EXPRESSION, HEAD_MOTION, IDENTIFY)
SENSOR = Manifest.of(Device("capteur-entree", "esp32", "entree"), PERSON_DETECTION)
SCREEN = Manifest.of(Device("ecran-bureau", "tablet", "bureau"), IDENTIFY)


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
    """A satellite built only with the SDK, as the suite launches it: a fresh device per scenario.
    `voice`: with a microphone and a speaker; `text`: with a text display and typing."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        voice: bool = False,
        text: bool = False,
        stale_token: bool = False,
        updater: Updater | None = None,
    ):
        self.manifest, self.voice, self.text, self.stale_token = manifest, voice, text, stale_token
        self.updater = updater
        self.sink = RecordingSink()
        self.shown: list[str] = []
        self.satellite: Satellite | None = None
        self.body = Watcher(self)
        self.launches: list[Launch] = []

    async def __call__(self, launch: Launch) -> None:
        self.launches.append(launch)
        tokens = TokenStore(launch.state / "token.json")
        if self.stale_token:  # paired with a previous core: refused, then paired again
            tokens.save(launch.url, self.manifest.device.id, "nst1_from-another-life")
        audio: dict[str, Any] = {"source": SilenceSource(), "sink": self.sink} if self.voice else {}
        text: dict[str, Any] = (
            {"text_display": lambda shown, _final: self.shown.append(shown), "text_input": True} if self.text else {}
        )
        self.satellite = Satellite(launch.url, self.manifest, tokens=tokens, body=self.body,
                                   enrollment=launch.pairing_uri, environ={}, poll_s=0.1, updater=self.updater,
                                   **audio, **text)  # fmt: skip
        await self.satellite.run()


@pytest.mark.parametrize(
    "driver",
    [lambda: Driver(ROBOT, voice=True), lambda: Driver(SENSOR), lambda: Driver(SCREEN, text=True),
     lambda: Driver(SCREEN, voice=True, text=True), lambda: Driver(SCREEN, text=True, updater=Source())],
    ids=["robot that can identify", "sensor without audio", "screen and keyboard", "voice and text",
         "updates itself"],
)  # fmt: skip
async def test_a_satellite_written_with_the_sdk_conforms(driver: object) -> None:
    device: Driver = driver()  # ty: ignore[call-non-callable]
    findings = await check_satellite(device, deadline_s=10)

    assert findings.passed, findings.failures()
    checks = {ENGLISH.format(f.check, "en") for f in findings.items}
    names = device.satellite.manifest.names if device.satellite else frozenset()
    assert {
        "reconnects with the same token after the core closes its link (L5)",
        "pairs with the enrollment code of NESTOR_PAIRING_URI, no code typed (P3, P4)",
        "presents a refused enrollment code (used) only once (P5)",
        "waits for Retry-After when rate-limited (P6)",
    } <= checks, checks
    identifies = {"keeps polling after a pending identify (I1)", "acknowledges intent.identify within 1 s"}
    assert (identifies <= checks) is (IDENTIFY in names), checks
    assert (device.body.identified >= 2) is (IDENTIFY in names), "once pending, once paired"
    assert (len(device.sink.played) > 0) is device.voice, "the core's audio reached the sink"
    assert ("Hello." in device.shown) is device.text, "the core's words reached the display"
    if device.voice:
        assert "offers no audio when audio is not allowed (A1)" in checks
    updates = {"reports where its software stands (U1)", "never installs a version it did not report (U2)"}
    assert (updates <= checks) is (device.updater is not None), checks
    assert device.body.sent[:2] == ([True, True] if PERSON_DETECTION in names else []), "and it was valid"


async def test_a_refused_token_is_forgotten_and_the_device_pairs_again() -> None:
    findings = await check_satellite(Driver(ROBOT, voice=True, stale_token=True), deadline_s=10)
    assert findings.passed, findings.failures()


COMMAND = [sys.executable, "-c", "from nestor_satellite.cli import main; main()", "run", "--core", "{url}",
           "--room", "salon", "--id", "pi-salon", "--token-file", "{state}/token.json"]  # fmt: skip


async def test_the_command_line_satellite_conforms_as_a_program() -> None:
    findings = await check_satellite(_without_sound_card(COMMAND), deadline_s=15)
    assert findings.passed, findings.failures()


async def test_the_text_only_command_line_satellite_conforms_without_the_voice_extra() -> None:
    findings = await check_satellite(_without_webrtc([*COMMAND, "--text", "--no-voice"]), deadline_s=15)
    assert findings.passed, findings.failures()
    checks = {ENGLISH.format(f.check, "en") for f in findings.items}
    assert any("intent.text" in check for check in checks), checks


def _without_sound_card(command: list[str]) -> list[str]:
    """The command, with PortAudio replaced by silence: CI machines have no sound card."""
    stub = (
        "import sys, types; from nestor_satellite import SilenceSource, RecordingSink; "
        "import nestor_satellite.devices as d; d.Microphone = lambda **_: SilenceSource(); "
        "d.Speaker = lambda **_: RecordingSink(); "
    )
    return [command[0], "-c", stub + command[2], *command[3:]]


def _without_webrtc(command: list[str]) -> list[str]:
    """The command, as if the `voice` extra were not installed: aiortc and PyAV cannot be imported."""
    stub = "import sys; sys.modules.update(dict.fromkeys(['aiortc', 'av'])); "  # None: the import fails
    return [command[0], "-c", stub + command[2], *command[3:]]
