"""Updates of the device's own software, asked for by a person in Nestor (`device.update`, U1 to U3).

The device finds newer versions itself, at its software's own source (its package index): the core
never names a source, and its `intent.update` is carried out only for the newer version the device
found and reported, never an older one. Installing one reports `installing`, then the process starts
again on it, with the same command. The new version must be welcomed by the core within a minute:
otherwise it installs the version it ran before, starts again on it, and reports `failed`. The update
in progress is kept in a small file next to the token, so the new process knows what to confirm.

    satellite = Satellite(core, manifest, tokens=tokens, updater=PypiUpdater("my-satellite", module="my_satellite"))
"""

import asyncio
import contextlib
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from nestor_satellite import wire
from nestor_satellite.protocol import UPDATE, UpdateState

logger = logging.getLogger(__name__)

CONFIRM_S = 60.0  # a new version not welcomed within this goes back to the previous one (U3)
CHECK_EVERY_S = 24 * 3600.0  # while linked, the source is checked again once a day (U1)

type Send = Callable[[dict[str, Any]], None]


class Updater(ABC):
    """How the device's software is updated: where newer versions are found, how one is installed."""

    @abstractmethod
    async def newer(self) -> str | None:
        """The newest version above the running one at the software's own source; `None` when none."""

    @abstractmethod
    async def install(self, version: str) -> None:
        """Install `version` in place of the running one; raise when it fails, the running one kept."""

    def notes(self, version: str) -> str | None:
        """An https address of what `version` changes, found with it at the source; `None` when unknown."""
        return None

    def restart(self) -> None:
        """Start the process again, on the version now installed, with the same command."""
        os.execv(sys.executable, sys.orig_argv)


class Updates:
    """What the satellite does about its software, from `updater`, for the version it runs."""

    def __init__(self, updater: Updater, running: str, state: Path, *, confirm_s: float = CONFIRM_S,
                 every_s: float = CHECK_EVERY_S) -> None:  # fmt: skip
        self.updater, self.running, self.path = updater, running, state
        self.found: str | None = None  # the newer version found at the source, reported to the core
        self._confirm_s, self._every_s = confirm_s, every_s
        self._send: Send | None = None
        self._installing = False
        self._previous: str | None = None  # set while this version, just installed, waits to be welcomed
        saved = self._read()
        if (target := saved.get("to")) is not None:
            if target == self.running:
                self._previous = saved.get("from")
            else:  # it never started on the new version: the install did not take
                self._write({"failed": target})

    async def watch(self) -> None:
        """While the satellite runs: a version just installed goes back to the previous one when it is
        not welcomed in time."""
        if self._previous is None:
            return
        await asyncio.sleep(self._confirm_s)
        if self._previous is None:
            return
        logger.warning("version %s was not welcomed: going back to %s", self.running, self._previous)
        try:
            await self.updater.install(self._previous)
        except Exception:
            logger.exception("could not go back to %s", self._previous)
            return
        self._write({"failed": self.running})
        self.updater.restart()

    def linked(self, allowed: frozenset[str], send: Send) -> asyncio.Task[None] | None:
        """The link is welcomed: confirm a version just installed, and report on the software while the
        device is allowed `device.update` (the task ends with the link)."""
        if self._previous is not None:
            self._previous = None
            self._write({})
            logger.info("now running %s", self.running)
        if UPDATE not in allowed:
            return None
        self._send = send
        return asyncio.create_task(self._report_regularly())

    def unlinked(self) -> None:
        self._send = None

    def request(self, version: str) -> None:
        """A person asked for `version` (`intent.update`)."""
        if self._installing or version != self.found or not _newer(version, self.running):
            logger.warning("ignoring a request to install %s: not the newer version found", version)
            return
        self._installing = True
        task = asyncio.ensure_future(self._install(version))
        task.add_done_callback(lambda _: setattr(self, "_installing", False))

    async def _install(self, version: str) -> None:
        self._report(UpdateState.INSTALLING, version)
        try:
            await self.updater.install(version)
        except Exception as error:
            logger.warning("could not install %s: %s", version, str(error) or type(error).__name__)
            self._write({"failed": version})
            self._report(UpdateState.FAILED, version)
            return
        self._write({"from": self.running, "to": version})
        self.updater.restart()

    async def _report_regularly(self) -> None:
        while True:
            await self._check()
            await asyncio.sleep(self._every_s)

    async def _check(self) -> None:
        try:
            found = await self.updater.newer()
        except Exception as error:
            logger.warning("could not check for a newer version: %s", str(error) or type(error).__name__)
            return
        self.found = found if found is not None and _newer(found, self.running) else None
        if self.found is None:
            self._report(UpdateState.CURRENT)
        elif self._read().get("failed") == self.found:
            self._report(UpdateState.FAILED, self.found)
        else:
            self._report(UpdateState.AVAILABLE, self.found)

    def _report(self, state: UpdateState, version: str | None = None) -> None:
        if self._send is not None:
            notes = self.updater.notes(version) if version else None
            self._send(wire.update_status(state, version, notes if notes and notes.startswith("https://") else None))

    def _read(self) -> dict[str, Any]:
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in saved.items() if isinstance(v, str)} if isinstance(saved, dict) else {}

    def _write(self, state: dict[str, str]) -> None:
        if not state:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with contextlib.suppress(OSError):
            self.path.write_text(json.dumps(state))


def _newer(version: str, than: str) -> bool:
    try:
        return Version(version) > Version(than)
    except InvalidVersion:
        return False
