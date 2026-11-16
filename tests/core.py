"""The server's pairing service and satellite gateway, served on a local port: what a satellite talks
to in the link tests. Each accepted link is welcomed with what the device was allowed, and what it
sends is kept; the test closes it as the core would. Media sessions the test opens on a link are
answered by `Relay`, a WebRTC peer of aiortc as the core's relay would be."""

import asyncio
import contextlib
import fractions
import json
import math
import socket
import struct
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass, field
from typing import Any

import av
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
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

    async def next_of(self, message_type: str, timeout_s: float = 5.0) -> dict[str, Any]:
        """The next message of `message_type` the device sends, skipping the others."""
        async with asyncio.timeout(timeout_s):
            while (received := await self.received.get())["type"] != message_type:
                pass
        return received

    async def next(self, timeout_s: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self.received.get(), timeout_s)

    async def close(self, code: int) -> None:
        with contextlib.suppress(RuntimeError):
            await self.socket.close(code)


class Tone(MediaStreamTrack):
    """A 440 Hz tone at 48 kHz: what a person says through the core during a call."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pts = 0

    async def recv(self) -> av.AudioFrame:
        await asyncio.sleep(0.02)
        pcm = b"".join(
            struct.pack("<h", int(12_000 * math.sin(2 * math.pi * 440 * (self._pts + i) / 48_000))) for i in range(960)
        )
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        frame.planes[0].update(pcm)
        frame.sample_rate, frame.pts, frame.time_base = 48_000, self._pts, fractions.Fraction(1, 48_000)
        self._pts += 960
        return frame


@dataclass
class Session:
    """A device's connection for one session, as the core holds it: the tracks the device sends."""

    id: str
    connection: RTCPeerConnection
    tracks: list[MediaStreamTrack] = field(default_factory=list)

    async def frames(self, kind: str, count: int, timeout_s: float = 10.0) -> list[Any]:
        """The first `count` frames the device sends on its track of `kind`."""
        async with asyncio.timeout(timeout_s):
            while not (track := next((t for t in self.tracks if t.kind == kind), None)):
                await asyncio.sleep(0.02)
            return [await track.recv() for _ in range(count)]

    async def ended(self, timeout_s: float = 10.0) -> None:
        """Until the device ends the connection."""
        async with asyncio.timeout(timeout_s):
            while self.connection.connectionState not in ("closed", "failed", "disconnected"):
                await asyncio.sleep(0.05)


class Relay:
    """The core's side of media sessions: it takes the device's connection for a session the test opened, plays a
    tone back in a call, and keeps every offer, asked or not."""

    def __init__(self) -> None:
        self.opened: set[str] = set()  # the sessions the test opened
        self.offers: list[str] = []  # the session of every offer, opened or not
        self.sessions: asyncio.Queue[Session] = asyncio.Queue()
        self._connections: list[RTCPeerConnection] = []

    async def offer(self, _device_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.offers.append(str(body.get("session")))
        if body.get("session") not in self.opened:
            raise PermissionError("no session waits for this connection")
        connection = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # on the same machine: no STUN to wait for
        self._connections.append(connection)
        session = Session(body["session"], connection)
        connection.on("track", session.tracks.append)
        await connection.setRemoteDescription(RTCSessionDescription(sdp=body["sdp"], type=body["type"]))
        if any(t.kind == "audio" for t in connection.getTransceivers()):
            connection.addTrack(Tone())
        await connection.setLocalDescription(await connection.createAnswer())
        self.sessions.put_nowait(session)
        return {"sdp": connection.localDescription.sdp, "type": connection.localDescription.type}

    async def next(self, timeout_s: float = 10.0) -> Session:
        return await asyncio.wait_for(self.sessions.get(), timeout_s)

    async def close(self) -> None:
        for connection in self._connections:
            await connection.close()


class Core:
    def __init__(self) -> None:
        self.service = Service(Journal(MemoryEventLog()))
        self.links: asyncio.Queue[Link] = asyncio.Queue()
        self.audio_offers = 0
        self.relay = Relay()
        self.url = ""

    @contextlib.asynccontextmanager
    async def serving(self) -> AsyncGenerator["Core"]:
        port = _free_port()
        server_ = DeviceServer(
            host="127.0.0.1", port=port,
            gateway=SatelliteGateway(self.service, on_link=self._linked, media=self.relay.offer),
        )  # fmt: skip

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
            await self.relay.close()

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
