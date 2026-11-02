"""Audio in and out of a satellite: 16-bit mono PCM, whatever the hardware.

`AudioSource` gives 20 ms chunks to send to the core (the microphone); `AudioSink` plays what the
core says (the speaker). `nestor_satellite.devices` implements both with PortAudio; the doubles
here serve tests and devices without one or the other. They travel over WebRTC
(`nestor_satellite.webrtc`, the `voice` extra); these ports need nothing.
"""

from abc import ABC, abstractmethod

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
