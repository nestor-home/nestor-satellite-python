"""The server's pairing service and satellite gateway, served on a local port: what a satellite talks
to in the link tests. Each accepted link is welcomed with what the device was allowed, and what it
sends is kept; the test closes it as the core would."""

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncGenerator, Iterable
from typing import Any

from fastapi import Request, WebSocket
from nestor.adapters.channels.satellites.gateway import SatelliteGateway
from nestor.adapters.channels.satellites.server import DeviceServer
from nestor.core import Capability
from nestor.modules.journal import Journal
from nestor.modules.satellites import Pairing as Service
from nestor.modules.satellites import PairingMethod as ServerMethod
from nestor.modules.satellites import Satellite as Paired
from nestor.modules.satellites import pairing_uri
from nestor.modules.satellites import wire as server
from nestor.testing.journal import MemoryEventLog


class Link:
    def __init__(self, socket: WebSocket, satellite: Paired, allowed: frozenset[str]) -> None:
        self.socket, self.satellite, self.allowed = socket, satellite, allowed
        self.received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(self, message: server.Message, id: str | None = None) -> None:
        await self.socket.send_text(json.dumps(server.encode(message, id=id)))

    async def next(self, timeout_s: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self.received.get(), timeout_s)

    async def close(self, code: int) -> None:
        with contextlib.suppress(RuntimeError):
            await self.socket.close(code)


class Core:
    def __init__(self) -> None:
        self.service = Service(Journal(MemoryEventLog()))
        self.links: asyncio.Queue[Link] = asyncio.Queue()
        self.audio_offers = 0
        self.url = ""

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncGenerator["Core"]:
        port = _free_port()
        server_ = DeviceServer(
            host="127.0.0.1", port=port, gateway=SatelliteGateway(self.service, on_link=self._linked)
        )

        @server_.app.middleware("http")
        async def count_offers(request: Request, call_next: Any) -> Any:
            if request.method == "POST" and request.url.path.endswith("/audio"):
                self.audio_offers += 1
            return await call_next(request)

        serving = asyncio.create_task(server_.serve())
        while not server_.started:
            await asyncio.sleep(0.01)
        self.url = f"http://127.0.0.1:{port}"
        try:
            yield self
        finally:
            server_.stop()
            await serving

    async def enrollment(self, capabilities: Iterable[str] | None = None) -> str:
        """A pairing URI that pairs a device at once, allowed at most `capabilities`."""
        allowed = None if capabilities is None else [Capability(c) for c in capabilities]
        started = await self.service.start_addition(ServerMethod.PROVISIONING, capabilities=allowed)
        assert started.code is not None
        return pairing_uri(self.url, started.code)

    async def next_link(self, timeout_s: float = 10.0) -> Link:
        return await asyncio.wait_for(self.links.get(), timeout_s)

    async def _linked(self, socket: WebSocket, satellite: Paired, manifest: object, allowed: frozenset[str]) -> None:
        link = Link(socket, satellite, allowed)
        welcome = server.Welcome(device_id=satellite.id, room=satellite.room, capabilities=tuple(sorted(allowed)))
        await link.send(welcome)
        self.links.put_nowait(link)
        with contextlib.suppress(Exception):  # until the device leaves, or the test closes the link
            while True:
                link.received.put_nowait(json.loads(await socket.receive_text()))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
