import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nestor_satellite import AUDIO_CAPTURE, AUDIO_PLAYBACK, PAIRING_CODE, PAIRING_URI, PairingFailed, Satellite, cli


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
            "http://nestor.local:7860/",
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
    assert satellite.core == "http://nestor.local:7860"
    assert (satellite.manifest.device.room, satellite.manifest.names) == ("cuisine", {AUDIO_CAPTURE, AUDIO_PLAYBACK})
    assert satellite.tokens.path == tmp_path / "t.json"


def test_a_headless_satellite_pairs_with_the_uri_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    uri = "nestor+http://nestor.local:7860/?code=K7QF2M9X"
    flagged = cli.build(cli.parse(["run", "--room", "salon", "--pairing-uri", uri, "--lang", "fr"]))
    assert (flagged.core, flagged.messages.language) == ("http://nestor.local:7860", "fr")
    monkeypatch.setenv(PAIRING_URI, uri.replace("nestor.local", "nestor.lan"))
    assert cli.build(cli.parse(["run", "--room", "salon"])).core == "http://nestor.lan:7860"


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
    cli.main(["run", "--core", "http://h:7860", "--room", "salon", "--id", "pi", "--token-file", str(tmp_path / "t")])
    assert ran == ["pi"]


def test_a_satellite_that_cannot_be_paired_exits_with_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run(self: Satellite) -> None:
        raise PairingFailed("update the device")

    monkeypatch.setattr(Satellite, "run", run)
    with pytest.raises(SystemExit) as exited:
        cli.main(["run", "--core", "http://h:7860", "--room", "salon", "--token-file", str(tmp_path / "t")])
    assert exited.value.code == 1
