"""A satellite's life: pair once, then stay connected to the core, reconnecting when the link drops.

    satellite = Satellite("http://nestor.local:7860", manifest, tokens=TokenStore("~/.config/me.json"),
                          body=MyBody(), source=Microphone(), sink=Speaker())
    await satellite.run()

Each connection is a WebRTC peer connection: the microphone and the core's voice as audio tracks,
the `nestor` data channel for `hello`/`welcome`, intents (acknowledged) and observations.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

from nestor_satellite import wire
from nestor_satellite.audio import AudioSink, AudioSource, SilenceSource, SourceTrack, play
from nestor_satellite.body import Body
from nestor_satellite.dispatch import Dispatcher, Outcome
from nestor_satellite.pairing import TokenStore, pair, show_code
from nestor_satellite.protocol import AUDIO_CAPTURE, PERSON_DETECTION, SPEECH_ACTIVITY, Manifest, ProtocolError

logger = logging.getLogger(__name__)


class CapabilityNotAllowed(ProtocolError):
    """An observation the device did not declare, or the household did not allow."""


class _Refused(Exception):
    """The core does not know this token any more: pair again."""


class Satellite:
    def __init__(
        self,
        core: str,
        manifest: Manifest,
        *,
        tokens: TokenStore,
        body: Body | None = None,
        source: AudioSource | None = None,
        sink: AudioSink | None = None,
        on_code: Callable[[str], None] = show_code,
        ice_servers: list[str] | None = None,
        poll_s: float = 1.0,
        backoff_s: tuple[float, float] = (0.5, 30.0),
    ) -> None:
        """`ice_servers` (e.g. `["stun:stun.l.google.com:19302"]`) are only needed beyond the local network."""
        self.core, self.manifest, self.tokens = core.rstrip("/"), manifest, tokens
        self.body = body or Body()
        self._source = source if source is not None and AUDIO_CAPTURE in manifest.names else None
        self._sink = sink
        self._on_code = on_code
        self._ice = [RTCIceServer(urls=url) for url in ice_servers or []]
        self._poll_s = poll_s
        self._backoff = backoff_s
        self._channel: Any = None
        self.welcome: wire.Welcome | None = None
        self.connections = 0

    async def run(self) -> None:
        """Run until cancelled: pair when needed, connect, and reconnect with a growing delay."""
        audio = [a for a in (self._source, self._sink) if a is not None]
        for device in audio:
            await device.start()
        delay = self._backoff[0]
        try:
            async with httpx.AsyncClient(base_url=self.core, timeout=10) as http:
                while True:
                    try:
                        token = await self._token(http)
                        welcomed = await self._connect_once(http, token)
                    except _Refused:
                        logger.warning("the core refused this satellite's token: pairing again")
                        self.tokens.forget()
                        continue
                    except Exception as error:  # the network, the core restarting: try again later
                        logger.warning("satellite link failed (%s), retrying in %.1f s", error, delay)
                        welcomed = False
                    delay = self._backoff[0] if welcomed else min(delay * 2, self._backoff[1])
                    await asyncio.sleep(delay)
        finally:
            for device in audio:
                await device.close()

    def person_detected(self, count: int, confidence: float) -> bool:
        """Report people seen now; `False` when not connected (observations are not queued)."""
        return self._observe(PERSON_DETECTION, wire.person_detected(count, confidence))

    def speech_activity(self, active: bool) -> bool:
        return self._observe(SPEECH_ACTIVITY, wire.speech_activity(active))

    def _observe(self, capability: str, message: dict[str, Any]) -> bool:
        allowed = self.welcome.capabilities if self.welcome else self.manifest.names
        if capability not in self.manifest.names or capability not in allowed:
            raise CapabilityNotAllowed(f"{message['type']} needs the {capability} capability, declared and allowed")
        if self._channel is None or self._channel.readyState != "open":
            return False
        self._channel.send(json.dumps(message))
        return True

    async def _token(self, http: httpx.AsyncClient) -> str:
        device = self.manifest.device.id
        if token := self.tokens.load(self.core, device):
            return token
        token = await pair(http, self.manifest, on_code=self._on_code, poll_s=self._poll_s)
        self.tokens.save(self.core, device, token)
        return token

    async def _connect_once(self, http: httpx.AsyncClient, token: str) -> bool:
        """One connection, until the core hangs up or the link fails; whether it was welcomed."""
        peer = RTCPeerConnection(RTCConfiguration(iceServers=self._ice))
        ended = asyncio.Event()
        players: set[asyncio.Task[None]] = set()
        peer.addTrack(SourceTrack(self._source or SilenceSource()))
        channel = peer.createDataChannel("nestor")
        welcomed = False

        @channel.on("open")
        def opened() -> None:
            channel.send(json.dumps(wire.hello(self.manifest)))

        dispatcher = Dispatcher(self.body, lambda message: channel.send(json.dumps(message)))

        @channel.on("message")
        def received(raw: str) -> None:
            nonlocal welcomed
            outcome = dispatcher.handle(raw)
            if outcome is Outcome.WELCOMED:
                welcomed, self.welcome = True, dispatcher.welcome
                self.body.connected(True)
            elif outcome is Outcome.HUNG_UP:
                ended.set()

        @channel.on("close")
        def closed() -> None:
            ended.set()

        @peer.on("track")
        def incoming(track: MediaStreamTrack) -> None:
            sink = self._sink or _Drain()
            players.add(asyncio.ensure_future(play(track, sink, on_frame=self.body.heartbeat)))

        @peer.on("connectionstatechange")
        def state() -> None:
            if peer.connectionState in {"failed", "closed"}:
                ended.set()

        try:
            await peer.setLocalDescription(await peer.createOffer())
            local = peer.localDescription
            response = await http.post(
                f"/satellites/{self.manifest.device.id}/offer",
                json={"sdp": local.sdp, "type": local.type},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401:
                raise _Refused
            answer = response.raise_for_status().json()
            await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            self._channel = channel
            self.connections += 1
            await ended.wait()
            return welcomed
        finally:
            self._channel = None
            if welcomed:
                self.body.connected(False)
            await peer.close()
            for player in players:
                player.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await player


class _Drain(AudioSink):
    """No speaker: received audio is read (so the link stays healthy) and dropped."""

    async def write(self, pcm: bytes) -> None: ...
