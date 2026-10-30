"""The machine's microphone and speaker through PortAudio (`pip install nestor-satellite[sounddevice]`).

PortAudio calls back from its own thread: chunks cross into asyncio through a queue for the
microphone, and through a bounded buffer for the speaker (late audio is dropped rather than
letting the voice lag further and further behind).
"""

import asyncio
import threading
from typing import Any

from nestor_satellite.audio import FRAME_S, AudioSink, AudioSource


def _sounddevice() -> Any:
    try:
        import sounddevice
    except ImportError as error:  # also raised by PortAudio missing on the machine
        raise RuntimeError("audio devices need `pip install nestor-satellite[sounddevice]` and PortAudio") from error
    return sounddevice


class Microphone(AudioSource):
    def __init__(self, *, sample_rate: int = 48000, device: int | str | None = None) -> None:
        self.sample_rate = sample_rate
        self._device = device
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)  # one second
        self._stream: Any = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()

        def captured(data: Any, _frames: int, _time: Any, _status: Any) -> None:
            loop.call_soon_threadsafe(self._offer, bytes(data))

        self._stream = _sounddevice().RawInputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", blocksize=int(self.sample_rate * FRAME_S),
            device=self._device, callback=captured,
        )  # fmt: skip
        self._stream.start()

    def _offer(self, chunk: bytes) -> None:
        if self._chunks.full():
            self._chunks.get_nowait()  # nobody reads: keep the freshest second
        self._chunks.put_nowait(chunk)

    async def read(self) -> bytes:
        return await self._chunks.get()

    async def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Speaker(AudioSink):
    def __init__(self, *, sample_rate: int = 48000, device: int | str | None = None, max_buffer_s: float = 0.5):
        self.sample_rate = sample_rate
        self._device = device
        self._max = int(sample_rate * max_buffer_s) * 2
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stream: Any = None

    async def start(self) -> None:
        def playing(out: Any, frames: int, _time: Any, _status: Any) -> None:
            with self._lock:
                chunk, self._buffer[: frames * 2] = bytes(self._buffer[: frames * 2]), b""
            out[:] = chunk + bytes(frames * 2 - len(chunk))  # silence when there is nothing to play

        self._stream = _sounddevice().RawOutputStream(
            samplerate=self.sample_rate, channels=1, dtype="int16", device=self._device, callback=playing
        )
        self._stream.start()

    async def write(self, pcm: bytes) -> None:
        with self._lock:
            self._buffer += pcm
            if len(self._buffer) > self._max:
                del self._buffer[: len(self._buffer) - self._max]

    async def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
