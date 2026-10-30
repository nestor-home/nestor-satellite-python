import asyncio
import fractions
import time

import pytest
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack
from av import AudioFrame

from nestor_satellite import RecordingSink, SilenceSource
from nestor_satellite.audio import SourceTrack, play


async def test_the_microphone_track_sends_20_ms_frames_in_real_time() -> None:
    track = SourceTrack(SilenceSource())
    started = time.monotonic()
    frames = [await track.recv() for _ in range(10)]
    assert [f.samples for f in frames] == [960] * 10 and frames[-1].pts == 9 * 960
    assert time.monotonic() - started >= 0.17, "ten frames take about 180 ms, not an instant"
    track.stop()
    with pytest.raises(MediaStreamError):
        await track.recv()


class Voice(MediaStreamTrack):
    """The core's voice: `count` frames of 20 ms, 48 kHz stereo as aiortc decodes Opus, then the end."""

    kind = "audio"

    def __init__(self, count: int) -> None:
        super().__init__()
        self.left = count

    async def recv(self) -> AudioFrame:
        if not self.left:
            raise MediaStreamError
        self.left -= 1
        await asyncio.sleep(0)
        frame = AudioFrame(format="s16", layout="stereo", samples=960)
        frame.planes[0].update(bytes(frame.planes[0].buffer_size))
        frame.sample_rate, frame.time_base, frame.pts = 48000, fractions.Fraction(1, 48000), (5 - self.left) * 960
        return frame


async def test_the_cores_voice_is_played_as_mono_at_the_speakers_rate() -> None:
    sink, heard = RecordingSink(sample_rate=16000), []
    await play(Voice(5), sink, on_frame=lambda: heard.append(1))
    assert len(heard) == 5
    assert abs(len(sink.played) - 5 * 320 * 2) <= 2 * 320, "about 100 ms of 16 kHz mono"


class SlowMicrophone(SilenceSource):
    async def read(self) -> bytes:
        await asyncio.sleep(0.03)  # a device that delivers late
        return await super().read()


async def test_a_late_microphone_is_not_delayed_further() -> None:
    track = SourceTrack(SlowMicrophone())
    started = time.monotonic()
    for _ in range(5):
        await track.recv()
    assert time.monotonic() - started < 0.25, "only the microphone's own delay"


async def test_the_voice_plays_without_anyone_counting_frames() -> None:
    sink = RecordingSink()
    await play(Voice(2), sink)
    assert len(sink.played) == 2 * 960 * 2
