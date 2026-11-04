"""An `Updater` for a satellite installed from PyPI with pip or uv, in the environment it runs from.

    PypiUpdater("nestor-satellite[voice,sounddevice]", module="nestor_satellite")

Newer versions are read from the package's PyPI page (pre-releases and yanked releases left out), with
the address of their notes: the project's changelog or releases link when it declares one, else the
version's page on PyPI. A
version is installed into the running environment, with `uv pip` when uv is there, else pip; then a
fresh interpreter imports `module` at that version. When it cannot, the running version is installed
back and the update fails.
"""

import asyncio
import importlib.metadata
import shutil
import sys

import httpx
from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

from nestor_satellite.update import Updater

INDEX = "https://pypi.org/pypi"
NOTES_LINKS = ("changelog", "release notes", "releases", "changes")  # project URL labels, lowercased


class UpdateFailed(RuntimeError):
    """A version could not be installed or imported; the running version is installed again."""


class PypiUpdater(Updater):
    def __init__(
        self,
        requirement: str,
        *,
        module: str,
        index: str = INDEX,
        timeout_s: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`requirement`: the package and its extras, no version (`my-satellite[voice]`); `module`: what
        it must import once installed."""
        parsed = Requirement(requirement)
        if parsed.specifier or parsed.url or parsed.marker:
            raise ValueError(f"{requirement!r}: the package and its extras only, no version")
        self.name, self.module = parsed.name, module
        self._extras = f"[{','.join(sorted(parsed.extras))}]" if parsed.extras else ""
        self.running = importlib.metadata.version(self.name)
        self._index, self._timeout_s, self._transport = index.rstrip("/"), timeout_s, transport
        self._notes: dict[str, str] = {}

    async def newer(self) -> str | None:
        async with httpx.AsyncClient(timeout=15, transport=self._transport) as http:
            response = await http.get(f"{self._index}/{self.name}/json")
            response.raise_for_status()
            page = response.json()
        releases = page["releases"]
        versions = [v for v in (_version(k) for k, files in releases.items() if _installable(files)) if v]
        newest = max((v for v in versions if not v.is_prerelease), default=None)
        if newest is None or newest <= Version(self.running):
            return None
        links = {str(k).lower(): v for k, v in ((page.get("info") or {}).get("project_urls") or {}).items()}
        declared = next((links[k] for k in NOTES_LINKS if isinstance(links.get(k), str)), None)
        self._notes[str(newest)] = declared or f"https://pypi.org/project/{self.name}/{newest}/"
        return str(newest)

    def notes(self, version: str) -> str | None:
        return self._notes.get(version)

    async def install(self, version: str) -> None:
        if not await self._pip(version) or not await self._imports(version):
            await self._pip(self.running)
            raise UpdateFailed(f"{self.name} {version} did not install or import")

    async def _pip(self, version: str) -> bool:
        wanted = f"{self.name}{self._extras}=={version}"
        uv = shutil.which("uv")
        command = (
            [uv, "pip", "install", "--python", sys.executable, wanted]
            if uv
            else [sys.executable, "-m", "pip", "install", wanted]
        )
        return await _run(command, self._timeout_s)

    async def _imports(self, version: str) -> bool:
        check = (
            f"import importlib.metadata, {self.module}; assert importlib.metadata.version({self.name!r}) == {version!r}"
        )
        return await _run([sys.executable, "-c", check], self._timeout_s)


async def _run(command: list[str], timeout_s: float) -> bool:
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout_s) == 0
    except TimeoutError:
        process.kill()
        await process.wait()
        return False


def _version(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _installable(files: object) -> bool:
    return isinstance(files, list) and any(isinstance(f, dict) and not f.get("yanked") for f in files)
