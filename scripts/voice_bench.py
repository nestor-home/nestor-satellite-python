"""Measure the bundled voice: say random device codes as a satellite would, transcribe them with Whisper,
count the codes heard exactly (first six digits).

    uv run --with faster-whisper==1.2.1 python scripts/voice_bench.py [codes per language, default 20]

Whisper hears isolated French digits worse than people do: a reference voice (macOS Thomas) scores
100 % here, so this is a floor to compare clips with, not an absolute. Seeded: the same clips give
the same score.
"""

import asyncio
import random
import re
import sys

import numpy as np
from faster_whisper import WhisperModel

from nestor_satellite.audio import RecordingSink
from nestor_satellite.i18n import LANGUAGES, Messages
from nestor_satellite.speech import SpokenCode

RATE = 16_000


async def announce(lang: str, code: str) -> np.ndarray:
    sink = RecordingSink(RATE)

    async def no_wait(_s: float) -> None:
        await asyncio.sleep(0)

    voice = SpokenCode(sink, Messages(lang), sleep=no_wait)
    voice.say(code)
    await voice.finished()
    return np.frombuffer(bytes(sink.played), np.int16).astype(np.float32) / 32768


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    whisper = WhisperModel("small", device="cpu", compute_type="int8")
    for lang in LANGUAGES:
        rng, exact = random.Random(1), 0
        for _ in range(count):
            code = "".join(rng.choice("0123456789") for _ in range(6))
            segments, _info = whisper.transcribe(asyncio.run(announce(lang, code)), language=lang, beam_size=5)
            heard = " ".join(s.text for s in segments)
            if "".join(re.findall(r"\d", heard))[:6] == code:
                exact += 1
            else:
                print(f"  {lang} {code}: {heard.strip()}")
        print(f"{lang}: {exact}/{count} codes heard exactly", flush=True)


if __name__ == "__main__":
    main()
