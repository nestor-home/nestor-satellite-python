"""The device code said aloud by the SDK itself (`device_code.speech`), offline: short recorded clips
played one after another through the speaker.

The sentence is the catalogue's `device-code-spoken`. Formatted with a placeholder for the digits, it
splits into pieces of text and digit slots: each piece of text, and each digit, has a clip recorded
once by `scripts/voice.py`, listed with the pauses in `voice/index.json`. The TypeScript and Rust
SDKs ship the same clips and read them the same way.

A language without clips is said in English.
"""

import asyncio
import contextlib
import json
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from av import AudioFrame, AudioResampler

from nestor_satellite.audio import FRAME_S, AudioSink
from nestor_satellite.i18n import DEFAULT, Messages

MESSAGE = "device-code-spoken"
SLOT = "\x00"  # where the digits go, when the sentence is cut into clips
_STRIP = ".,;:!?…⁨⁩"  # around a piece of text: punctuation and Fluent's isolation marks
AHEAD_S = 0.1  # how much audio the sink holds ahead of real time: a stop is heard at once


@dataclass(frozen=True, slots=True)
class Clip:
    text: str  # a digit, or a piece of the sentence: its key in the index


@dataclass(frozen=True, slots=True)
class Pause:
    ms: int


type Step = Clip | Pause


def key(text: str) -> str:
    """A piece of the sentence as the index names its clip: without the spaces and punctuation around it."""
    return text.strip().strip(_STRIP).strip()


@cache
def index() -> dict[str, Any]:
    return json.loads(files("nestor_satellite").joinpath("voice", "index.json").read_text("utf-8"))


def pieces(messages: Messages) -> list[str]:
    """The sentence cut where the digits go: the text before, between and after them."""
    return messages(MESSAGE, digits=SLOT).split(SLOT)


def announcement(messages: Messages, code: str) -> list[Step]:
    """What to play to say `code` in the messages' language: clips and the pauses between them."""
    pauses = index()["pauses_ms"]
    steps: list[Step] = []
    parts = pieces(messages)
    for position, text in enumerate(parts):
        if text := key(text):
            steps += [Clip(text), Pause(pauses["after_text"])]
        if position < len(parts) - 1:
            for digit in code:
                steps += [Clip(digit), Pause(pauses["between_digits"])]
            steps[-1] = Pause(pauses["after_digits"])
    while steps and isinstance(steps[-1], Pause):
        steps.pop()
    return steps


class SpokenCode:
    """Says a device code through `sink`, in real time, in the background; a new code or `stop` cuts it."""

    def __init__(
        self, sink: AudioSink, messages: Messages, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        voiced = messages.language in index()["languages"]
        self._messages = messages if voiced else Messages(DEFAULT)
        self._clips: dict[str, str] = index()["languages"][self._messages.language]
        self._sink, self._sleep = sink, sleep
        self._task: asyncio.Task[None] | None = None

    @property
    def language(self) -> str:
        return self._messages.language

    @property
    def speaking(self) -> bool:
        return self._task is not None and not self._task.done()

    def say(self, code: str) -> None:
        self.stop()
        self._task = asyncio.get_running_loop().create_task(self._play(announcement(self._messages, code)))

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def finished(self) -> None:
        """Wait until the code has been said (or cut)."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._task)

    async def _play(self, steps: list[Step]) -> None:
        rate = self._sink.sample_rate
        chunk = int(rate * FRAME_S) * 2
        loop = asyncio.get_running_loop()
        started, played = loop.time(), 0
        for step in steps:
            pcm = _clip(self._clips[step.text], rate) if isinstance(step, Clip) else bytes(rate * step.ms // 1000 * 2)
            for start in range(0, len(pcm), chunk):
                piece = pcm[start : start + chunk]
                ahead = started + played / rate - loop.time()
                if ahead > AHEAD_S:
                    await self._sleep(ahead - AHEAD_S)
                await self._sink.write(piece)
                played += len(piece) // 2


@cache
def _clip(path: str, rate: int) -> bytes:
    """A clip of the index as 16-bit mono PCM at `rate`."""
    with files("nestor_satellite").joinpath("voice", path).open("rb") as file, wave.open(file) as clip:
        source_rate, pcm = clip.getframerate(), clip.readframes(clip.getnframes())
    if source_rate == rate:
        return pcm
    frame = AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
    frame.planes[0].update(pcm)
    frame.sample_rate = source_rate
    resampler = AudioResampler(format="s16", layout="mono", rate=rate)
    out = [*resampler.resample(frame), *resampler.resample(None)]
    return b"".join(bytes(f.planes[0])[: f.samples * 2] for f in out)
