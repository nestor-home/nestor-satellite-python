"""Record the clips the SDKs play to say a device code (`src/nestor_satellite/voice/`), with Kokoro.

    uv run --with kokoro-onnx==0.6.1 python scripts/voice.py

For each language: the ten digits, and each piece of text of the catalogue's `device-code-spoken`
around the digits. Run it again after changing that sentence, then copy `voice/` to the TypeScript
and Rust SDKs. Same model, same voices, same machine: same clips.

Kokoro's weights are Apache-2.0 and its training data permissive (see `voice/LICENSE`). Piper was
measured first and set aside: its French voices are either unintelligible (fr_FR-mls) or derived
from data under research or non-commercial licences. `scripts/voice_bench.py` measures the clips.
"""

import hashlib
import json
import shutil
import tempfile
import unicodedata
import urllib.request
import wave
from pathlib import Path

import espeakng_loader
import numpy as np
from av import AudioFrame, AudioResampler
from kokoro_onnx import EspeakConfig, Kokoro

from nestor_satellite.i18n import LANGUAGES, Messages
from nestor_satellite.speech import key, pieces

OUT = Path(__file__).parent.parent / "src" / "nestor_satellite" / "voice"
CACHE = Path.home() / ".cache" / "nestor-satellite-voice"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
VOICES = {"en": ("af_heart", "en-us"), "fr": ("ff_siwis", "fr-fr")}
# How each digit is written for the voice, measured with voice_bench.py: a French digit alone
# reads best exclaimed, except seven ("sept !" is heard "six").
DIGITS = {"en": {}, "fr": {d: f"{d} !" for d in "012345689"}}
RATE = 16_000  # mono 16-bit: enough for speech, and every platform reads WAV
PAUSES_MS = {"after_text": 250, "between_digits": 350, "after_digits": 800}
SILENCE = 300  # below this (about -40 dBFS), the start and end of a clip are trimmed
MARGIN_S = 0.02
PEAK = 0.7  # every clip at the same loudness, whatever the voice


def model() -> dict[str, Path]:
    CACHE.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, digest in MODEL.items():
        path = paths[name] = CACHE / name
        if not path.exists():
            urllib.request.urlretrieve(f"{RELEASE}/{name}", path)
        found = hashlib.sha256(path.read_bytes()).hexdigest()
        if found != digest:
            raise SystemExit(f"{name}: sha256 {found}, expected {digest}")
    return paths


def say(kokoro: Kokoro, lang: str, text: str) -> bytes:
    voice, language = VOICES[lang]
    samples, rate = kokoro.create(text, voice=voice, lang=language)
    audio = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    loud = np.flatnonzero(np.abs(audio) > SILENCE)
    margin = int(rate * MARGIN_S)
    audio = audio[max(loud[0] - margin, 0) : loud[-1] + margin]
    audio = (audio.astype(np.float64) * (PEAK * 32767 / np.abs(audio).max())).astype(np.int16)
    frame = AudioFrame(format="s16", layout="mono", samples=len(audio))
    frame.planes[0].update(audio.tobytes())
    frame.sample_rate = rate
    resampler = AudioResampler(format="s16", layout="mono", rate=RATE)
    return b"".join(
        bytes(f.planes[0])[: f.samples * 2] for f in [*resampler.resample(frame), *resampler.resample(None)]
    )


def name(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "-".join("".join(c if c.isalnum() else " " for c in ascii_text.lower()).split())


def main() -> None:
    paths = model()
    # espeak-ng ignores a data folder whose path is long (as in a virtual environment): give it a short one.
    with tempfile.TemporaryDirectory(prefix="esp") as short:
        data = Path(short) / "d"
        data.symlink_to(espeakng_loader.get_data_path())
        espeak = EspeakConfig(lib_path=espeakng_loader.get_library_path(), data_path=str(data))
        kokoro = Kokoro(str(paths["kokoro-v1.0.onnx"]), str(paths["voices-v1.0.bin"]), espeak_config=espeak)
        languages: dict[str, dict[str, str]] = {}
        shutil.rmtree(OUT, ignore_errors=True)
        for lang in LANGUAGES:
            texts = {str(d): DIGITS[lang].get(str(d), f"{d}.") for d in range(10)}
            texts |= {t: f"{t}." for t in map(key, pieces(Messages(lang))) if t}
            clips = {}
            (OUT / lang).mkdir(parents=True)
            for text, spoken in texts.items():
                clips[text] = f"{lang}/{name(text)}.wav"
                with wave.open(str(OUT / clips[text]), "wb") as clip:
                    clip.setnchannels(1)
                    clip.setsampwidth(2)
                    clip.setframerate(RATE)
                    clip.writeframes(say(kokoro, lang, spoken))
            languages[lang] = clips
    index = {"sample_rate": RATE, "pauses_ms": PAUSES_MS, "languages": languages}
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", "utf-8")
    (OUT / "LICENSE").write_text(LICENSE, "utf-8")


LICENSE = """\
The clips in this folder say a satellite's pairing code. They were recorded by scripts/voice.py in
nestor-satellite-python with Kokoro-82M v1.0 (https://huggingface.co/hexgrad/Kokoro-82M, Apache-2.0
weights, trained on permissive or non-copyrighted audio), run by kokoro-onnx 0.6.1 (MIT):

- en: voice af_heart;
- fr: voice ff_siwis, trained on the SIWIS French Speech Synthesis Database
  (https://datashare.ed.ac.uk/handle/10283/2353), CC BY 4.0.

The clips are shared under the Creative Commons Attribution 4.0 International licence
(https://creativecommons.org/licenses/by/4.0/). Attribution: "Voice: Kokoro-82M (hexgrad),
French voice trained on the SIWIS French Speech Synthesis Database (Honnet et al., 2017)."
"""


if __name__ == "__main__":
    main()
