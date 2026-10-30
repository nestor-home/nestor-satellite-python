"""Microphone and speaker over a fake PortAudio: no sound card needed."""

import asyncio
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from nestor_satellite.devices import Microphone, Speaker


class Stream:
    def __init__(self, **options: Any) -> None:
        self.options, self.running, self.closed = options, False, False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def portaudio(monkeypatch: pytest.MonkeyPatch) -> list[Stream]:
    streams: list[Stream] = []

    def stream(**options: Any) -> Stream:
        streams.append(Stream(**options))
        return streams[-1]

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=stream, RawOutputStream=stream))
    return streams


async def test_the_microphone_delivers_what_portaudio_captures_freshest_first_when_nobody_reads(
    portaudio: list[Stream],
) -> None:
    microphone = Microphone(sample_rate=16000)
    await microphone.start()
    [stream] = portaudio
    assert stream.running and stream.options["blocksize"] == 320
    for i in range(60):  # 1.2 s while nobody reads
        stream.options["callback"](bytes([i]) * 640, 320, None, None)
    await asyncio.sleep(0)
    assert (await microphone.read())[0] == 10, "the oldest 200 ms were dropped"
    await microphone.close()
    await microphone.close()
    assert stream.closed


async def test_the_speaker_plays_what_it_is_given_then_silence_and_never_lags_far_behind(
    portaudio: list[Stream],
) -> None:
    speaker = Speaker(sample_rate=1000, max_buffer_s=0.1)
    await speaker.start()
    [stream] = portaudio
    await speaker.write(b"\x01\x00" * 30)
    out = bytearray(80)
    stream.options["callback"](out, 40, None, None)
    assert bytes(out) == b"\x01\x00" * 30 + bytes(20)

    await speaker.write(b"\x02\x00" * 500)  # half a second at once: only the last 100 ms are kept
    played = bytearray()
    for _ in range(4):
        stream.options["callback"](out, 40, None, None)
        played += out
    assert played.count(b"\x02\x00") == 100 and played.endswith(bytes(80))
    await speaker.close()
    await speaker.close()
    assert stream.closed


def test_without_portaudio_the_error_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    with pytest.raises(RuntimeError, match=r"nestor-satellite\[sounddevice\]"):
        asyncio.run(Microphone().start())
