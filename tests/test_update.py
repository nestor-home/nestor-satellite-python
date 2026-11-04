"""Updates of the device's own software: what it reports, what it installs when a person asks, and how
a new version is confirmed or abandoned."""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
from nestor.modules.satellites import wire as server

from nestor_satellite import (
    UPDATE,
    Device,
    Manifest,
    PypiUpdater,
    Satellite,
    Software,
    TokenStore,
    UpdateFailed,
    Updater,
)
from nestor_satellite import pypi as pypi_module
from nestor_satellite.update import Updates

from .core import Core, Link

RUNNING = "0.5.0"
MANIFEST = Manifest(Device("enceinte-salon", "raspberry-pi-4", "salon"), software=Software("my-satellite", RUNNING))


class Source(Updater):
    """A software source with `available` on it; installing `broken` fails."""

    def __init__(self, available: str | None = "0.6.0", *, broken: str | None = None) -> None:
        self.available, self.broken = available, broken
        self.installed: list[str] = []
        self.restarted = asyncio.Event()

    async def newer(self) -> str | None:
        return self.available

    async def install(self, version: str) -> None:
        if version == self.broken:
            raise UpdateFailed(f"{version} does not import")
        self.installed.append(version)

    def restart(self) -> None:
        self.restarted.set()


@contextlib.asynccontextmanager
async def linked(
    tmp_path: Path, source: Source, *, allowed: tuple[str, ...] = (UPDATE,)
) -> AsyncGenerator[tuple[Satellite, Link]]:
    async with Core().serving() as core:
        tokens = TokenStore(tmp_path / "token.json")
        device = Satellite(core.url, MANIFEST, tokens=tokens, updater=source, environ={},
                           enrollment=await core.enrollment(allowed))  # fmt: skip
        running = asyncio.create_task(device.run())
        try:
            yield device, await core.next_link()
        finally:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running


async def statuses(link: Link, count: int = 1) -> list[dict[str, Any]]:
    """The next `update.status` payloads, skipping acks."""
    found = []
    while len(found) < count:
        message = await link.next()
        if message["type"] == "update.status":
            found.append(message["payload"])
    return found


def test_an_updater_declares_device_update(tmp_path: Path) -> None:
    device = Satellite("http://127.0.0.1:9", MANIFEST, tokens=TokenStore(tmp_path / "t.json"), updater=Source())
    assert UPDATE in device.manifest.names
    assert device.manifest.to_json()["software"] == {"name": "my-satellite", "version": RUNNING}


async def test_it_reports_the_newer_version_and_installs_only_that_one(tmp_path: Path) -> None:
    source = Source()
    async with linked(tmp_path, source) as (_, link):
        assert await statuses(link) == [{"state": "available", "version": "0.6.0"}]
        for unreported in ("0.0.0", "0.7.0"):
            await link.send(server.UpdateIntent(version=unreported), id=f"ask-{unreported}")
            assert (await link.next())["payload"] == {"of": f"ask-{unreported}"}
        await link.send(server.UpdateIntent(version="0.6.0"), id="ask")
        assert await statuses(link) == [{"state": "installing", "version": "0.6.0"}]
        await asyncio.wait_for(source.restarted.wait(), 5)
    assert source.installed == ["0.6.0"]
    assert json.loads((tmp_path / "token.update.json").read_text()) == {"from": RUNNING, "to": "0.6.0"}


async def test_a_version_that_does_not_install_is_reported_failed_and_the_device_keeps_running(tmp_path: Path) -> None:
    source = Source(broken="0.6.0")
    async with linked(tmp_path, source) as (device, link):
        await statuses(link)
        await link.send(server.UpdateIntent(version="0.6.0"))
        assert await statuses(link, 2) == [{"state": "installing", "version": "0.6.0"},
                                           {"state": "failed", "version": "0.6.0"}]  # fmt: skip
        assert not source.restarted.is_set()
        assert device.updates is not None
        await device.updates._check()  # the daily check: still the version that failed
        assert await statuses(link) == [{"state": "failed", "version": "0.6.0"}]


async def test_up_to_date_and_not_allowed(tmp_path: Path) -> None:
    async with linked(tmp_path, Source(available=None)) as (_, link):
        assert await statuses(link) == [{"state": "current"}]
    async with linked(tmp_path / "other", Source(), allowed=()) as (device, link):
        await asyncio.sleep(0.3)
        assert link.received.empty(), "no update.status without device.update allowed"
        assert device.welcome is not None


async def test_a_new_version_is_confirmed_once_welcomed(tmp_path: Path) -> None:
    (tmp_path / "token.update.json").write_text(json.dumps({"from": "0.4.0", "to": RUNNING}))
    source = Source(available=None)
    async with linked(tmp_path, source) as (_, link):
        await statuses(link)
        await asyncio.sleep(0.1)
    assert not (tmp_path / "token.update.json").exists()
    assert source.installed == [] and not source.restarted.is_set()


async def test_a_new_version_not_welcomed_in_time_goes_back_and_says_so_later(tmp_path: Path) -> None:
    state = tmp_path / "token.update.json"
    state.write_text(json.dumps({"from": "0.4.0", "to": RUNNING}))
    source = Source(available=None)
    await asyncio.wait_for(Updates(source, RUNNING, state, confirm_s=0.01).watch(), 5)
    assert source.installed == ["0.4.0"] and source.restarted.is_set()
    assert json.loads(state.read_text()) == {"failed": RUNNING}

    back = Source(available=RUNNING)  # started again on 0.4.0, the version that failed still newest
    updates = Updates(back, "0.4.0", state)
    sent: list[dict[str, Any]] = []
    updates.linked(frozenset({UPDATE}), sent.append)
    await asyncio.sleep(0.05)
    assert [m["payload"] for m in sent] == [{"state": "failed", "version": RUNNING}]


async def test_an_install_that_did_not_take_is_reported_failed(tmp_path: Path) -> None:
    state = tmp_path / "update.json"
    state.write_text(json.dumps({"from": "0.4.0", "to": "0.6.0"}))  # restarted, still on 0.5.0
    updates = Updates(Source(), RUNNING, state)
    sent: list[dict[str, Any]] = []
    task = updates.linked(frozenset({UPDATE}), sent.append)
    await asyncio.sleep(0.05)
    assert task is not None
    task.cancel()
    assert [m["payload"] for m in sent] == [{"state": "failed", "version": "0.6.0"}]


def _pypi(releases: dict[str, list[dict[str, Any]]]) -> httpx.MockTransport:
    def answer(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://pypi.org/pypi/nestor-satellite/json"
        return httpx.Response(200, json={"releases": releases})

    return httpx.MockTransport(answer)


@pytest.mark.parametrize(
    ("releases", "expected"),
    [
        ({"0.1.0": [{}], "99.0.0": [{}], "99.1.0rc1": [{}], "99.2.0": [{"yanked": True}], "99.3.0": []}, "99.0.0"),
        ({"0.1.0": [{}]}, None),
    ],
    ids=["newest stable", "nothing newer"],
)
async def test_pypi_finds_the_newest_stable_release(releases: dict[str, Any], expected: str | None) -> None:
    updater = PypiUpdater("nestor-satellite[voice]", module="nestor_satellite", transport=_pypi(releases))
    assert await updater.newer() == expected


async def test_pypi_installs_the_running_version_back_when_the_new_one_does_not_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    async def run(command: list[str], timeout_s: float) -> bool:
        commands.append(command)
        return command[1] != "-c"  # installing works, importing does not

    monkeypatch.setattr(pypi_module, "_run", run)
    monkeypatch.setattr(pypi_module.shutil, "which", lambda _: None)
    updater = PypiUpdater("nestor-satellite[voice,sounddevice]", module="nestor_satellite")
    with pytest.raises(UpdateFailed):
        await updater.install("99.0.0")
    installed = [c[-1] for c in commands if c[1:4] == ["-m", "pip", "install"]]
    assert installed == [
        "nestor-satellite[sounddevice,voice]==99.0.0",
        f"nestor-satellite[sounddevice,voice]=={updater.running}",
    ]


def test_pypi_takes_a_package_without_a_version() -> None:
    with pytest.raises(ValueError, match="no version"):
        PypiUpdater("nestor-satellite==1.0", module="nestor_satellite")
