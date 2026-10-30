import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nestor_satellite import AUDIO_CAPTURE, AUDIO_PLAYBACK, Satellite, cli


def test_the_command_runs_a_microphone_and_speaker_satellite_in_its_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
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

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setattr(Satellite, "run", run)
    cli.main(["run", "--core", "http://h:7860", "--room", "salon", "--id", "pi", "--token-file", str(tmp_path / "t")])
    assert ran == ["pi"]
