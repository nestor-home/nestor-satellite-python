"""Pairing: ask the core, show the code, wait for a household member to confirm it, keep the token.

The token is the device's only credential: it is written once, readable by its owner only (mode
600), and forgotten when the core refuses it (revoked, or paired again elsewhere).
"""

import asyncio
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path

import httpx

from nestor_satellite.protocol import Manifest

logger = logging.getLogger(__name__)


class TokenStore:
    """One device's token for one core, in a JSON file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def load(self, core: str, device_id: str) -> str | None:
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if saved.get("core") != core or saved.get("device_id") != device_id:
            return None  # a token for another core or device is useless here
        return saved.get("token")

    def save(self, core: str, device_id: str, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump({"core": core, "device_id": device_id, "token": token}, file)
        os.chmod(self.path, 0o600)  # an older file may have been more open

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


def show_code(code: str) -> None:
    print(f"Pairing code: {code} — confirm it in Nestor (admin, or `nestor satellites approve {code}`)", flush=True)


async def pair(
    http: httpx.AsyncClient, manifest: Manifest, *, on_code: Callable[[str], None] = show_code, poll_s: float = 1.0
) -> str:
    """Pair until a household member confirms a code; a code that expires is replaced by a new one."""
    while True:
        response = await http.post("/satellites/pair", json=manifest.to_json())
        response.raise_for_status()
        ticket = response.json()
        on_code(ticket["code"])
        while True:
            state = (await http.get(f"/satellites/pair/{ticket['pairing_id']}")).raise_for_status().json()
            if state["status"] == "approved":
                return state["token"]
            if state["status"] != "pending":  # expired, or claimed by someone else: start again
                logger.info("pairing %s, asking again", state["status"])
                break
            await asyncio.sleep(poll_s)
