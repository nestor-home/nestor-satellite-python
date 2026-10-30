"""Audio in and out of a satellite: 16-bit mono PCM, whatever the hardware.

`AudioSource` gives 20 ms chunks to send to the core (the microphone); `AudioSink` plays what the
core says (the speaker). `nestor_satellite.devices` implements both with PortAudio; the doubles
here serve tests and devices without one or the other.
"""

import asyncio
import fractions
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import AudioFrame, AudioResampler

FRAME_S = 0.02


class AudioSource(ABC):
    sample_rate: int = 48000

    @property
    def frame_bytes(self) -> int:
        return int(self.sample_rate * FRAME_S) * 2

    async def start(self) -> None:
        """Open the device; nothing to open by default."""
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def read(self) -> bytes:
        """The next 20 ms of audio (`frame_bytes` long); waits for it."""


class AudioSink(ABC):
    sample_rate: int = 48000

    async def start(self) -> None:
        """Open the device; nothing to open by default."""
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def write(self, pcm: bytes) -> None:
        """Play audio as it arrives; must not block for long."""


class SilenceSource(AudioSource):
    """A device without a microphone still sends a (silent) stream: the core's turn taking expects one."""

    async def read(self) -> bytes:
        return bytes(self.frame_bytes)


class RecordingSink(AudioSink):
    """Keeps what was played, e.g. to check a device received the core's voice."""

    def __init__(self, sample_rate: int = 48000) -> None:
        self.sample_rate = sample_rate
        self.played = bytearray()

    async def write(self, pcm: bytes) -> None:
        self.played += pcm


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
