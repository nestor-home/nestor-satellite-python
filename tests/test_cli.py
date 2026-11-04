import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import av
import pytest

from nestor_satellite import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    PAIRING_CODE,
    PAIRING_URI,
    TEXT_DISPLAY,
    TEXT_INPUT,
    CapabilityNotAllowed,
    PairingFailed,
    PairingMethod,
    Satellite,
    cli,
    wire,
)


@pytest.fixture(autouse=True)
def no_sound_card_and_no_pairing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.delenv(PAIRING_URI, raising=False)
    monkeypatch.delenv(PAIRING_CODE, raising=False)


def test_the_command_runs_a_microphone_and_speaker_satellite_in_its_room(tmp_path: Path) -> None:
    args = cli.parse(
        [
            "run",
            "--core",
            "http://nestor.local:8471/",
            "--room",
            "cuisine",
            "--id",
            "pi-cuisine",
            "--token-file",
            str(tmp_path / "t.json"),
            "--input-device",
            "2",
            "--output-device",
            "USB",
        ]
    )
    satellite = cli.build(args)  # fmt: skip
    assert satellite.core == "http://nestor.local:8471"
    assert (satellite.manifest.device.room, satellite.manifest.names) == ("cuisine", {AUDIO_CAPTURE, AUDIO_PLAYBACK})
    assert satellite.tokens.path == tmp_path / "t.json"
    assert satellite.manifest.pairing_methods == (PairingMethod.SPEECH, PairingMethod.PROVISIONING), "it says its code"


@pytest.mark.parametrize(("platform", "format"), [("darwin", "avfoundation"), ("linux", "v4l2")])
async def test_a_camera_reads_qr_codes_through_the_platforms_video_input(
    monkeypatch: pytest.MonkeyPatch, platform: str, format: str
) -> None:
    monkeypatch.setattr(cli.sys, "platform", platform)
    satellite = cli.build(cli.parse(["run", "--core", "http://n:8471", "--room", "salon", "--camera", "0"]))
    assert PairingMethod.QR in satellite.manifest.pairing_methods
    opened: list[str | None] = []
    monkeypatch.setattr(av, "open", lambda _device, format, options: opened.append(format) or SimpleNamespace(
        decode=lambda video: iter(())))  # fmt: skip
    await cli._camera("0").start()
    assert opened == [format]


def test_a_headless_satellite_pairs_with_the_uri_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    uri = "nestor+http://nestor.local:8471/?code=K7QF2M9X"
    flagged = cli.build(cli.parse(["run", "--room", "salon", "--pairing-uri", uri, "--lang", "fr"]))
    assert (flagged.core, flagged.messages.language) == ("http://nestor.local:8471", "fr")
    monkeypatch.setenv(PAIRING_URI, uri.replace("nestor.local", "nestor.lan"))
    assert cli.build(cli.parse(["run", "--room", "salon"])).core == "http://nestor.lan:8471"


def test_a_code_alone_needs_the_cores_address(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="NESTOR_PAIRING_URI"):
        cli.main(["run", "--room", "salon", "--pairing-code", "K7QF-2M9X", "--lang", "en"])
    with pytest.raises(SystemExit):
        cli.parse(["run", "--room", "salon", "--pairing-code", "K7QF2M9X", "--pairing-uri", "nestor+http://n/?code=x"])
    assert "not allowed with argument" in capsys.readouterr().err


def test_the_device_id_defaults_to_a_slug_of_the_host_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "Raspberry Pi.local")
    assert cli.default_id() == "raspberry-pi"
    monkeypatch.setattr(cli.socket, "gethostname", lambda: "...")
    assert cli.default_id() == "satellite"


def test_main_runs_the_satellite_until_interrupted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ran: list[Any] = []

    async def run(self: Satellite) -> None:
        ran.append(self.manifest.device.id)
        raise KeyboardInterrupt

    monkeypatch.setattr(Satellite, "run", run)
    cli.main(["run", "--core", "http://h:8471", "--room", "salon", "--id", "pi", "--token-file", str(tmp_path / "t")])
    assert ran == ["pi"]


def test_a_satellite_that_cannot_be_paired_exits_with_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run(self: Satellite) -> None:
        raise PairingFailed("update the device")

    monkeypatch.setattr(Satellite, "run", run)
    with pytest.raises(SystemExit) as exited:
        cli.main(["run", "--core", "http://h:8471", "--room", "salon", "--token-file", str(tmp_path / "t")])
    assert exited.value.code == 1


@pytest.mark.parametrize(
    ("flags", "names"),
    [(["--text"], {AUDIO_CAPTURE, AUDIO_PLAYBACK, TEXT_INPUT, TEXT_DISPLAY}),
     (["--text", "--no-voice"], {TEXT_INPUT, TEXT_DISPLAY}),
     (["--no-voice"], set())],
    ids=["voice and text", "text only", "neither"],
)  # fmt: skip
def test_the_command_runs_in_writing_aloud_or_both(tmp_path: Path, flags: list[str], names: set[str]) -> None:
    args = cli.parse(
        ["run", "--core", "http://n:8471", "--room", "bureau", "--token-file", str(tmp_path / "t"), *flags]
    )
    assert cli.build(args).manifest.names == names


class Lines:
    """Standard input: the lines a person typed, then the end."""

    def __init__(self, *lines: str) -> None:
        self.lines = [f"{line}\n" for line in lines]

    def __iter__(self) -> Any:
        return iter(self.lines)


async def test_lines_typed_on_stdin_are_sent_and_answers_printed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    typed = ["Bonjour", "  ", "x" * 5000, "hors ligne", "refusé", "Merci"]
    monkeypatch.setattr(cli.sys, "stdin", Lines(*typed))
    args = cli.parse(["run", "--core", "http://n:8471", "--room", "bureau", "--token-file", str(tmp_path / "t"),
                      "--text", "--no-voice"])  # fmt: skip
    sent: list[str] = []

    def send_text(self: Satellite, text: str) -> bool:
        wire.text_input(text)  # too long: refused before it is sent
        if text == "refusé":
            raise CapabilityNotAllowed("the household did not allow typing")
        sent.append(text)
        return text != "hors ligne"

    async def run(self: Satellite) -> None:
        while sent[-1:] != ["Merci"]:
            await asyncio.sleep(0.01)

    monkeypatch.setattr(Satellite, "send_text", send_text)
    monkeypatch.setattr(Satellite, "run", run)
    await cli.serve(cli.build(args), typing=True)
    assert sent == ["Bonjour", "hors ligne", "Merci"], "blank lines skipped"
    for said in ("Too long to send: 4,000 characters at most.", "the text was not sent", "did not allow typing"):
        assert said in caplog.text
    cli._print("Il est midi.", True)
    assert capsys.readouterr().out == "Il est midi.\n"


def test_a_managed_command_can_be_updated_from_nestor(tmp_path: Path) -> None:
    common = ["run", "--core", "http://nestor.local:8471", "--room", "salon", "--token-file", str(tmp_path / "t.json")]
    managed = cli.build(cli.parse([*common, "--text", "--no-voice", "--managed"]))
    assert "device.update" in managed.manifest.names
    assert managed.manifest.to_json()["software"]["name"] == "nestor-satellite"
    assert "device.update" not in cli.build(cli.parse([*common, "--text", "--no-voice"])).manifest.names
