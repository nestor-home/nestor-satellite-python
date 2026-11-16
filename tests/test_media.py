"""Watching and calling through a satellite, rules M1 to M3 of `nestor.satellite/1`: the SDK against the core's gateway
where the test plays the core on the link and in its media relay (`core.Relay`), so that it can also ask what a core
never would; then end to end against the core itself, a browser watching and calling as the admin does."""

import asyncio
import base64
import contextlib
import io
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import av
import pytest
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription
from nestor.adapters.channels.satellites.channel import SatellitesChannel
from nestor.core import Capability
from nestor.modules.journal import Journal
from nestor.modules.satellites import MediaEnd, MediaSessions, Purpose, Viewer, pairing_uri
from nestor.modules.satellites import Pairing as Service
from nestor.modules.satellites import PairingMethod as ServerMethod
from nestor.modules.satellites import Satellite as Paired
from nestor.modules.satellites import wire as server
from nestor.testing.channels import ScriptedAssistant
from nestor.testing.journal import MemoryBlobStore, MemoryEventLog

from nestor_satellite import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    MEDIA_INDICATOR,
    VIDEO_CAPTURE,
    Device,
    Manifest,
    PatternCamera,
    RecordingSink,
    Satellite,
    SilenceSource,
    TokenStore,
)

from .core import Core, Tone, _free_port
from .test_client import running

DEVICE = Device("camera-entree", "raspberry-pi-zero", "entree")
CALL = [AUDIO_CAPTURE, AUDIO_PLAYBACK]
EVERYTHING = [VIDEO_CAPTURE, *CALL, MEDIA_INDICATOR]


class Lens(PatternCamera):
    """Colour bars, counting how often the camera is switched on and off (once while pairing: it reads QR codes)."""

    def __init__(self) -> None:
        super().__init__(size=(320, 240), fps=30)
        self.started = self.closed = 0

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1


def device(core: Core, tmp_path: Path, uri: str | None, **options: Any) -> Satellite:
    options.setdefault("backoff_s", (0.05, 0.2))
    return Satellite(core.url, Manifest(DEVICE), tokens=TokenStore(tmp_path / "t.json"), enrollment=uri, environ={},
                     poll_s=0.05, **options)  # fmt: skip


def opening(session: str, *, video: bool, audio: bool, max_s: int = 60) -> server.MediaOpenIntent:
    return server.MediaOpenIntent(session=session, video=video, audio=audio, max_s=max_s)


async def until(condition: Any, timeout_s: float = 10.0) -> None:
    async with asyncio.timeout(timeout_s):
        while not condition():
            await asyncio.sleep(0.02)


@pytest.mark.parametrize(
    ("given", "declared"),
    [({"camera": PatternCamera()}, {VIDEO_CAPTURE}), ({"on_media": print}, {MEDIA_INDICATOR})],
    ids=["a camera", "an indicator"],
)
def test_filming_and_showing_it_follow_from_what_the_satellite_is_given(
    tmp_path: Path, given: dict[str, Any], declared: set[str]
) -> None:
    satellite = Satellite("http://n:8471", Manifest(DEVICE), tokens=TokenStore(tmp_path / "t"), environ={}, **given)
    assert satellite.manifest.names == declared
    for claimed in declared:
        with pytest.raises(ValueError, match="follow from what the satellite is given"):
            Satellite("http://n:8471", Manifest.of(DEVICE, claimed), tokens=TokenStore(tmp_path / "t"), environ={})


async def test_a_device_connects_only_for_a_session_its_link_asks_for_and_with_what_it_asks(tmp_path: Path) -> None:
    async with Core().serving() as core:
        camera = Lens()
        satellite = device(core, tmp_path, await core.enrollment(EVERYTHING), camera=camera, source=SilenceSource(),
                           sink=RecordingSink())  # fmt: skip
        async with running(satellite):
            link = await core.next_link()
            paired = camera.started
            await asyncio.sleep(0.5)
            assert (core.relay.offers, camera.started) == ([], paired), "nothing is offered, the camera is off"
            core.relay.opened.add("watch")
            await link.send(opening("watch", video=True, audio=False))
            session = await core.relay.next()
            [image] = await session.frames("video", 1)
            assert (session.id, image.width, image.height) == ("watch", 320, 240)
            assert {t.kind for t in session.tracks} == {"video"}, "watching carries no sound of the room"


@pytest.mark.parametrize(
    ("granted", "given", "asked"),
    [
        ([*CALL, MEDIA_INDICATOR], "camera and audio", (True, False)),
        (EVERYTHING, "camera", (False, True)),
        ([VIDEO_CAPTURE, AUDIO_CAPTURE, MEDIA_INDICATOR], "camera and audio", (True, True)),
        (EVERYTHING, "camera and audio", (False, False)),
    ],
    ids=["filming not allowed", "a call without microphone", "a call without playback allowed", "nothing asked"],
)
async def test_a_session_asking_more_than_the_device_has_or_was_allowed_is_never_connected(
    tmp_path: Path, granted: list[str], given: str, asked: tuple[bool, bool]
) -> None:
    shown: list[bool] = []
    audio: dict[str, Any] = {"source": SilenceSource(), "sink": RecordingSink()} if "audio" in given else {}
    async with Core().serving() as core:
        camera = Lens()
        satellite = device(core, tmp_path, await core.enrollment(granted), camera=camera, on_media=shown.append,
                           **audio)  # fmt: skip
        async with running(satellite):
            link = await core.next_link()
            paired = camera.started
            core.relay.opened.add("asked")
            await link.send(opening("asked", video=asked[0], audio=asked[1]), id="open-1")
            assert (await link.next_of("ack"))["payload"] == {"of": "open-1"}, "received, and refused"
            await asyncio.sleep(0.5)
    assert (core.relay.offers, shown, camera.started) == ([], [], paired)


async def test_a_session_ends_when_told_when_its_time_is_up_and_when_the_link_closes(tmp_path: Path) -> None:
    async with Core().serving() as core:
        camera = Lens()
        satellite = device(core, tmp_path, await core.enrollment(EVERYTHING), camera=camera)
        async with running(satellite):
            link = await core.next_link()
            core.relay.opened |= {"told", "timed", "linked"}
            await link.send(opening("told", video=True, audio=False))
            told = await core.relay.next()
            await told.frames("video", 1)
            await link.send(server.MediaCloseIntent(session="told"))
            await told.ended()

            await link.send(opening("timed", video=True, audio=False, max_s=2))
            timed = await core.relay.next()
            await timed.frames("video", 1)
            await timed.ended(timeout_s=2 + 3)

            await link.send(opening("linked", video=True, audio=False))
            linked = await core.relay.next()
            await linked.frames("video", 1)
            await link.close(1001)
            await linked.ended()
            await until(lambda: camera.closed == camera.started == 1 + 3)  # pairing, then each session


async def test_the_device_shows_it_films_before_it_sends_anything_and_until_it_stops(tmp_path: Path) -> None:
    shown: list[tuple[bool, int]] = []  # each change, with the offers made by then
    async with Core().serving() as core:
        satellite = device(core, tmp_path, await core.enrollment(EVERYTHING), camera=Lens(),
                           on_media=lambda active: shown.append((active, len(core.relay.offers))))  # fmt: skip
        async with running(satellite):
            link = await core.next_link()
            core.relay.opened.add("watch")
            await link.send(opening("watch", video=True, audio=False))
            await (await core.relay.next()).frames("video", 1)
            await link.send(server.MediaCloseIntent(session="watch"))
            await until(lambda: len(shown) == 2)
            await link.send(server.SnapshotIntent())
            await link.next_of("media.snapshot")
            await until(lambda: len(shown) == 4)
    assert shown == [(True, 0), (False, 1), (True, 1), (False, 1)], "on before the offer, off once ended, a picture too"


async def test_the_voice_with_the_assistant_steps_aside_during_a_call_and_comes_back_after(tmp_path: Path) -> None:
    sink = RecordingSink()
    async with Core().serving() as core:
        satellite = device(core, tmp_path, await core.enrollment(CALL), source=SilenceSource(), sink=sink)
        async with running(satellite):
            link = await core.next_link()
            await until(lambda: core.audio_offers >= 2)  # this core has no voice: the device keeps offering
            core.relay.opened.add("call")
            await link.send(opening("call", video=False, audio=True))
            call = await core.relay.next()
            await call.frames("audio", 3)
            during = core.audio_offers
            await until(lambda: max(sink.played, default=0) > 0)  # the person's voice is played in the room
            await asyncio.sleep(1.0)
            assert core.audio_offers == during, "no voice with the assistant while the call lasts"
            await link.send(server.MediaCloseIntent(session="call"))
            await call.ended()
            await until(lambda: core.audio_offers > during)


@pytest.mark.parametrize("allowed", [True, False], ids=["allowed to film", "not allowed"])
async def test_a_still_picture_is_sent_when_asked_by_a_device_allowed_to_film(tmp_path: Path, allowed: bool) -> None:
    async with Core().serving() as core:
        camera = Lens()
        uri = await core.enrollment(EVERYTHING if allowed else CALL)
        async with running(device(core, tmp_path, uri, camera=camera)):
            link = await core.next_link()
            paired = camera.started
            await link.send(server.SnapshotIntent(), id="picture-1")
            if not allowed:
                with pytest.raises(TimeoutError):
                    await link.next_of("media.snapshot", timeout_s=1.0)
                assert camera.started == paired
                return
            sent = await link.next_of("media.snapshot")
            server.decode(sent)  # what the core reads
    assert sent["payload"]["of"] == "picture-1"
    with av.open(io.BytesIO(base64.b64decode(sent["payload"]["jpeg"])), "r") as picture:
        image = next(picture.decode(video=0))
    assert (image.width, image.height) == (320, 240)
    assert camera.started == camera.closed == paired + 1, "on for the picture only"


class FilmingHome(SatellitesChannel):
    """The satellites channel of a core that serves voice: `welcome` grants a device the audio it was paired with,
    which a call needs. The voice itself is left out: what the device offers for it is refused."""

    def allowed(self, satellite: Paired) -> frozenset[Capability]:
        return satellite.capabilities


class Browser:
    """The admin's page: it receives the video, and in a call sends a tone as its microphone and hears the room."""

    def __init__(self, *, call: bool) -> None:
        self.connection = RTCPeerConnection()
        self.frames: list[str] = []  # the kind of each frame received
        self.connection.addTransceiver("video", direction="recvonly")
        if call:
            self.connection.addTrack(Tone())
        self._receiving: set[asyncio.Future[None]] = set()
        self.connection.on("track", lambda track: self._receiving.add(asyncio.ensure_future(self._receive(track))))

    async def offer(self) -> dict[str, str]:
        await self.connection.setLocalDescription(await self.connection.createOffer())
        return {"sdp": self.connection.localDescription.sdp, "type": "offer"}

    async def answered(self, answer: Any) -> None:
        await self.connection.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    async def _receive(self, track: MediaStreamTrack) -> None:
        with contextlib.suppress(Exception):
            while True:
                await track.recv()
                self.frames.append(track.kind)


@contextlib.asynccontextmanager
async def home() -> AsyncGenerator[tuple[MediaSessions, Service, MemoryBlobStore, str]]:
    journal = Journal(MemoryEventLog())
    pairing, blobs, port = Service(journal), MemoryBlobStore(), _free_port()
    channel = FilmingHome("satellites", host="127.0.0.1", port=port)
    channel.host_satellites(pairing, journal)
    relay = channel.media_relay()
    assert relay is not None, "the core's media extra is installed for the tests"
    media = MediaSessions(journal, pairing, relay, blobs)
    async with pairing, channel:
        serving = asyncio.create_task(channel.serve(ScriptedAssistant(lambda _message: "")))
        try:
            yield media, pairing, blobs, f"http://127.0.0.1:{port}"
        finally:
            await media.close()
            await channel.close()
            await serving


async def test_a_person_watches_calls_and_takes_a_picture_through_the_satellite_on_the_core(tmp_path: Path) -> None:
    owner = Viewer(account_id="a-mathis", person=None, owner=True, signed_in="s-1")
    shown: list[bool] = []
    sink = RecordingSink()
    async with home() as (media, pairing, blobs, url):
        started = await pairing.start_addition(ServerMethod.PROVISIONING)
        assert started.code is not None
        satellite = Satellite(url, Manifest(DEVICE), tokens=TokenStore(tmp_path / "t.json"), camera=Lens(),
                              source=SilenceSource(), sink=sink, on_media=shown.append, environ={},
                              enrollment=pairing_uri(url, started.code), poll_s=0.05)  # fmt: skip
        async with running(satellite):
            await until(lambda: media.linked(DEVICE.id) is not None)
            watching = Browser(call=False)
            watched = await media.open(DEVICE.id, Purpose.VIEW, owner, await watching.offer())
            await watching.answered(watched.answer)
            await until(lambda: watching.frames.count("video") > 5)
            await media.close_session(watched.session_id, MediaEnd.CLOSED)
            await until(lambda: shown == [True, False])

            calling = Browser(call=True)
            called = await media.open(DEVICE.id, Purpose.CALL, owner, await calling.offer())
            await calling.answered(called.answer)
            assert (called.video, called.audio) == (True, True)
            await until(lambda: max(sink.played, default=0) > 0 and "audio" in calling.frames, timeout_s=15)
            await media.close_session(called.session_id, MediaEnd.CLOSED)
            await until(lambda: shown == [True, False] * 2)

            taken = await media.snapshot(DEVICE.id, owner)
            picture = await blobs.get(taken.blob.digest)
    assert picture[:2] == b"\xff\xd8", "a JPEG"
    assert json.dumps(shown) == "[true, false, true, false, true, false]"
