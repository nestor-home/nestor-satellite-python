"""The sessions a person opens in Nestor to watch through the device's camera or call through it, and the still
pictures they ask for. The rules of `nestor.satellite/1` are kept here, whatever the driver does:

- M1. A connection only for an `intent.media.open` received on the link, and only for what the device was given and
  `welcome` allowed: video with a camera and `video.capture`, a call with a microphone, a speaker and both
  `audio.capture` and `audio.playback`. A session asking for more is refused whole.
- M2. It ends on `intent.media.close`, at `max_s` by the device's own clock, and when the link closes.
- M3. `on_media(True)` before anything is sent, `on_media(False)` once nothing is (sessions and still pictures).
- M4. The device only connects out to the core (`webrtc`): it listens on no port and has no stream address.

During a call, the voice with the assistant steps aside: it stops before the call connects, and starts again after.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nestor_satellite import wire
from nestor_satellite.i18n import Messages
from nestor_satellite.protocol import AUDIO_CAPTURE, AUDIO_PLAYBACK, VIDEO_CAPTURE

logger = logging.getLogger(__name__)

CALL = frozenset({AUDIO_CAPTURE, AUDIO_PLAYBACK})

type Connect = Callable[[wire.MediaOpenIntent], Awaitable[None]]  # carries a session until its connection ends
type Still = Callable[[], Awaitable[bytes]]  # a still picture, as JPEG
type Indicator = Callable[[bool], None]  # shows that something is filmed or heard (M3); returns at once


class VoiceHold:
    """The voice of one link with the assistant, which steps aside during a call: `hold` stops it, `release` starts
    it again; `end` when the link closes, after which it never starts."""

    def __init__(self, hear: Callable[[], Awaitable[None]] | None) -> None:
        self._hear = hear
        self._task: asyncio.Future[None] | None = None
        self._ended = False

    def start(self) -> None:
        if self._hear is not None and self._task is None and not self._ended:
            self._task = asyncio.ensure_future(self._hear())

    async def hold(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def end(self) -> None:
        self._ended = True
        await self.hold()


class MediaSessions:
    """The sessions of one link, welcomed with `allowed`. `films`: the satellite has a camera; `calls`: a microphone
    and a speaker."""

    def __init__(
        self,
        allowed: frozenset[str],
        *,
        films: bool,
        calls: bool,
        connect: Connect,
        still: Still | None,
        reply: Callable[[dict[str, Any]], None],
        voice: VoiceHold,
        messages: Messages,
        on_media: Indicator | None = None,
    ) -> None:
        self._films = films and VIDEO_CAPTURE in allowed
        self._calls = calls and allowed >= CALL
        self._connect, self._still, self._reply = connect, still, reply
        self._voice, self._messages, self._on_media = voice, messages, on_media
        self._live: dict[str, asyncio.Task[None]] = {}  # by session id
        self._pictures: set[asyncio.Task[None]] = set()
        self._calling = False
        self._shown = False  # what the indicator shows

    @property
    def active(self) -> bool:
        return bool(self._live or self._pictures)

    def handle(self, intent_id: str, intent: wire.MediaIntent) -> None:
        """An intent from the link: open or end a session, or take a picture. Returns at once."""
        match intent:
            case wire.MediaOpenIntent() as opening:
                self._open(opening)
            case wire.MediaCloseIntent(session=session):
                if task := self._live.get(session):
                    task.cancel()
            case wire.SnapshotIntent():
                self._snapshot(intent_id)

    async def close(self) -> None:
        """The link closed: everything ends (M2)."""
        tasks = [*self._live.values(), *self._pictures]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _open(self, intent: wire.MediaOpenIntent) -> None:
        if intent.session in self._live:
            return
        wanted = intent.video or intent.audio
        if not wanted or (intent.video and not self._films) or (intent.audio and (not self._calls or self._calling)):
            logger.warning("%s", self._messages("media-refused"))
            return
        self._calling = self._calling or intent.audio
        self._live[intent.session] = task = asyncio.create_task(self._carry(intent))
        task.add_done_callback(lambda _: self._ended(intent))
        self._indicate()

    async def _carry(self, intent: wire.MediaOpenIntent) -> None:
        timer = asyncio.timeout(intent.max_s)  # from the moment it was asked, by the device's own clock
        try:
            async with timer:
                if intent.audio:
                    await self._voice.hold()
                await self._connect(intent)
        except Exception as error:
            if timer.expired():
                logger.info("%s", self._messages("media-timed-out", seconds=intent.max_s))
            else:
                logger.warning("%s", self._messages("media-failed", error=str(error) or type(error).__name__))

    def _ended(self, intent: wire.MediaOpenIntent) -> None:
        del self._live[intent.session]
        if intent.audio:
            self._calling = False
            self._voice.start()
        self._indicate()

    def _snapshot(self, intent_id: str) -> None:
        if not self._films or self._still is None:
            logger.warning("%s", self._messages("media-refused"))
            return
        task = asyncio.create_task(self._picture(intent_id, self._still))
        self._pictures.add(task)

        def taken(_: asyncio.Task[None]) -> None:
            self._pictures.discard(task)
            self._indicate()

        task.add_done_callback(taken)
        self._indicate()

    async def _picture(self, intent_id: str, still: Still) -> None:
        try:
            self._reply(wire.snapshot(intent_id, await still()))
        except Exception as error:
            logger.warning("%s", self._messages("media-failed", error=str(error) or type(error).__name__))

    def _indicate(self) -> None:
        """Tell the driver when something starts or stops being filmed or heard."""
        if self._on_media is not None and self.active != self._shown:
            self._shown = self.active
            self._on_media(self._shown)
