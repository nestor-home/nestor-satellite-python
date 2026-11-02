"""A satellite's audio over WebRTC (`pip install nestor-satellite[voice]`): Opus only, no data channel.

Once its link is welcomed with `audio.capture` or `audio.playback` allowed, the satellite offers a
peer connection (`POST /satellites/{device_id}/audio`, its token as a bearer): the microphone goes
to the core, the core's voice comes back to the speaker. The connection lives with the link; the
satellite offers again when it fails.
"""

import asyncio
import contextlib
import fractions
import time
from collections.abc import Callable

import httpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import AudioFrame, AudioResampler

from nestor_satellite.audio import AudioSink, AudioSource, SilenceSource


class TokenRefused(Exception):
    """The core answered 401: it does not know this token any more."""


class SourceTrack(MediaStreamTrack):
    """An `AudioSource` as an outgoing WebRTC track, paced in real time."""

    kind = "audio"

    def __init__(self, source: AudioSource) -> None:
        super().__init__()
        self._source = source
        self._samples = 0
        self._started: float | None = None

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        rate = self._source.sample_rate
        if self._started is None:
            self._started = time.monotonic()
        elif (wait := self._started + self._samples / rate - time.monotonic()) > 0:
            await asyncio.sleep(wait)
        pcm = await self._source.read()
        frame = AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
        frame.planes[0].update(pcm)
        frame.sample_rate, frame.pts, frame.time_base = rate, self._samples, fractions.Fraction(1, rate)
        self._samples += len(pcm) // 2
        return frame


async def play(track: MediaStreamTrack, sink: AudioSink, *, on_frame: Callable[[], None] | None = None) -> None:
    """Feed an incoming WebRTC track to `sink`, resampled to its rate, until the track ends."""
    resampler = AudioResampler(format="s16", layout="mono", rate=sink.sample_rate)
    while True:
        try:
            frame = await track.recv()
        except MediaStreamError:
            return
        if on_frame is not None:
            on_frame()
        for mono in resampler.resample(frame):  # ty: ignore[invalid-argument-type]
            await sink.write(bytes(mono.planes[0])[: mono.samples * 2])


class WebRtcAudio:
    """The audio of one device: `connect` holds one peer connection until it fails."""

    def __init__(self, ice_servers: list[str] | None = None) -> None:
        """`ice_servers` (e.g. `["stun:stun.l.google.com:19302"]`) are only needed beyond the local network."""
        self._ice = [RTCIceServer(urls=url) for url in ice_servers or []]

    async def connect(
        self,
        http: httpx.AsyncClient,
        device_id: str,
        token: str,
        *,
        source: AudioSource | None,
        sink: AudioSink | None,
        on_frame: Callable[[], None] | None = None,
    ) -> None:
        """Offer, then exchange audio until the connection fails (returns) or is cancelled. `source`: the
        microphone, opened for this connection only (silence without one: the core's turn taking
        expects a stream); `sink`: where the core's voice is played (dropped without one)."""
        peer = RTCPeerConnection(RTCConfiguration(iceServers=self._ice))
        ended = asyncio.Event()
        players: set[asyncio.Task[None]] = set()
        microphone = source or SilenceSource()
        await microphone.start()
        peer.addTrack(SourceTrack(microphone))

        @peer.on("track")
        def incoming(track: MediaStreamTrack) -> None:
            players.add(asyncio.ensure_future(play(track, sink or _Drain(), on_frame=on_frame)))

        @peer.on("connectionstatechange")
        def state() -> None:
            if peer.connectionState in {"failed", "closed"}:
                ended.set()

        try:
            await peer.setLocalDescription(await peer.createOffer())
            local = peer.localDescription
            response = await http.post(
                f"/satellites/{device_id}/audio",
                json={"sdp": local.sdp, "type": local.type},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401:
                raise TokenRefused
            answer = response.raise_for_status().json()
            await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            await ended.wait()
        finally:
            await peer.close()
            for player in players:
                player.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await player
            await microphone.close()


class _Drain(AudioSink):
    """No speaker: received audio is read (so the connection stays healthy) and dropped."""

    async def write(self, pcm: bytes) -> None: ...
