"""The SDK's own voice: a device with a speaker and no screen says its code from recorded clips."""

import asyncio
import json
import wave
from importlib.resources import files

import pytest

from nestor_satellite import speech
from nestor_satellite.audio import RecordingSink
from nestor_satellite.i18n import LANGUAGES, Messages
from nestor_satellite.speech import Clip, Pause, SpokenCode, announcement, key, pieces

VOICE = files("nestor_satellite").joinpath("voice")
INDEX = json.loads(VOICE.joinpath("index.json").read_text("utf-8"))


def clip(lang: str, text: str) -> bytes:
    with VOICE.joinpath(INDEX["languages"][lang][text]).open("rb") as file, wave.open(file) as recorded:
        return recorded.readframes(recorded.getnframes())


async def no_wait(_s: float) -> None:
    await asyncio.sleep(0)


def test_the_code_is_said_digit_by_digit_twice_after_the_catalogues_sentence() -> None:
    steps = announcement(Messages("en"), "042")
    digits = [Clip("0"), Pause(350), Clip("4"), Pause(350), Clip("2")]
    assert steps == [Clip("Pairing code"), Pause(250), *digits, Pause(800), Clip("I repeat"), Pause(250), *digits]


def test_in_french_it_says_the_french_sentence() -> None:
    texts = [step.text for step in announcement(Messages("fr"), "7") if isinstance(step, Clip)]
    assert texts == ["Code d'appairage", "7", "Je répète", "7"]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_piece_of_every_sentence_and_every_digit_has_a_clip(lang: str) -> None:
    texts = {key(piece) for piece in pieces(Messages(lang))} - {""} | {str(d) for d in range(10)}
    assert set(INDEX["languages"][lang]) == texts
    for text in texts:
        with VOICE.joinpath(INDEX["languages"][lang][text]).open("rb") as file, wave.open(file) as recorded:
            assert (recorded.getnchannels(), recorded.getsampwidth()) == (1, 2)
            assert recorded.getframerate() == INDEX["sample_rate"]


def test_the_clips_say_where_they_come_from_and_under_which_licence() -> None:
    text = VOICE.joinpath("LICENSE").read_text("utf-8")
    assert "Kokoro" in text and "CC BY 4.0" in text and "SIWIS" in text


def test_a_language_without_clips_is_said_in_english(monkeypatch: pytest.MonkeyPatch) -> None:
    english_only = INDEX | {"languages": {"en": INDEX["languages"]["en"]}}
    monkeypatch.setattr(speech, "index", lambda: english_only)
    voice = SpokenCode(RecordingSink(), Messages("fr"))
    assert voice.language == "en"
    assert SpokenCode(RecordingSink(), Messages("de")).language == "en", "no catalogue either"


async def test_the_speaker_plays_the_clips_and_the_pauses_in_order() -> None:
    sink = RecordingSink(16_000)
    voice = SpokenCode(sink, Messages("fr"), sleep=no_wait)
    voice.say("05")
    await voice.finished()

    def pause(ms: int) -> bytes:
        return bytes(16 * ms * 2)

    said = clip("fr", "0") + pause(350) + clip("fr", "5")
    expected = clip("fr", "Code d'appairage") + pause(250) + said + pause(800)
    expected += clip("fr", "Je répète") + pause(250) + said
    assert bytes(sink.played) == expected
    assert not voice.speaking


async def test_the_clips_are_played_at_the_speakers_rate() -> None:
    slow, fast = RecordingSink(16_000), RecordingSink(48_000)
    for sink in (slow, fast):
        voice = SpokenCode(sink, Messages("en"), sleep=no_wait)
        voice.say("8")
        await voice.finished()
    assert len(fast.played) == pytest.approx(3 * len(slow.played), rel=0.01)


class Clocked(RecordingSink):
    """Notes how much audio it had been given when, to compare with real time."""

    def __init__(self) -> None:
        super().__init__(16_000)
        self.loop = asyncio.get_running_loop()
        self.started = self.loop.time()
        self.ahead: list[float] = []

    async def write(self, pcm: bytes) -> None:
        await super().write(pcm)
        self.ahead.append(len(self.played) / 2 / self.sample_rate - (self.loop.time() - self.started))


async def test_it_plays_in_real_time_and_stops_at_once() -> None:
    sink = Clocked()
    voice = SpokenCode(sink, Messages("en"))
    voice.say("123456")
    await asyncio.sleep(0.4)
    voice.stop()
    played = len(sink.played)
    await asyncio.sleep(0.2)

    assert len(sink.played) == played, "nothing is played once stopped"
    assert 0.3 < played / 2 / 16_000 < 0.55
    assert max(sink.ahead) <= speech.AHEAD_S + 0.03, "the speaker never holds more than a moment of audio"


async def test_a_new_code_replaces_the_one_being_said() -> None:
    sink = RecordingSink(16_000)
    voice = SpokenCode(sink, Messages("en"))
    voice.say("111111")
    await asyncio.sleep(0.2)
    voice.say("222222")
    voice.stop()
    await voice.finished()
    assert not voice.speaking
