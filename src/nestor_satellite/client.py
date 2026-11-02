"""A satellite's life: pair once, then hold one link to the core, reconnecting when it drops.

    satellite = Satellite("http://nestor.local:8471", manifest, tokens=TokenStore("~/.config/me.json"),
                          body=MyBody(), text_display=screen.show, text_input=True)
    await satellite.run()

Pairing (see `pairing`): an enrollment code given here or in the environment (`NESTOR_PAIRING_URI`,
`NESTOR_PAIRING_CODE`) is sent at once; otherwise the device code is presented on the `display`,
said by the `voice` (a device with a speaker and no screen says it with the SDK's own recorded
voice, see `speech`), and always logged. `enroll` hands in a code typed meanwhile; a `camera` reads
the QR code of a pairing URI. The manifest's `pairing_methods` follow from what was given.

The link is one WebSocket that carries every message: `hello` (manifest and token) then `welcome`,
intents (acknowledged), observations and text both ways. The capabilities for text and audio follow
from what was given too: `text_display` declares `text.display`, `text_input` declares `text.input`,
a `source` `audio.capture` and a `sink` `audio.playback`. Audio travels over WebRTC
(`nestor_satellite.webrtc`, the `voice` extra), offered once the link is welcomed with audio allowed.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import replace
from enum import IntEnum
from typing import TYPE_CHECKING, Any

import httpx
import websockets

from nestor_satellite import wire
from nestor_satellite.audio import AudioSink, AudioSource
from nestor_satellite.body import Body
from nestor_satellite.camera import Camera, read_qr
from nestor_satellite.codes import Enrollment, InvalidEnrollment
from nestor_satellite.dispatch import Dispatcher, TextDisplay
from nestor_satellite.i18n import Messages
from nestor_satellite.pairing import (
    PAIRING_CODE,
    PAIRING_URI,
    Display,
    EnrollmentOutcome,
    Pairing,
    PairingFailed,
    TokenStore,
    Voice,
)
from nestor_satellite.protocol import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    PERSON_DETECTION,
    SPEECH_ACTIVITY,
    TEXT_DISPLAY,
    TEXT_INPUT,
    Capability,
    Manifest,
    PairingMethod,
    ProtocolError,
)

if TYPE_CHECKING:
    from nestor_satellite.webrtc import WebRtcAudio

logger = logging.getLogger(__name__)

PING_S = 20.0  # the core pings this often; so does the satellite
SILENCE_S = 60.0  # nothing heard for this long: the link is lost (L4)
WELCOME_S = 15.0  # the core answers `hello` well within this (it closes a late one at 10 s)
AUDIO = frozenset({AUDIO_CAPTURE, AUDIO_PLAYBACK})
WIRED = frozenset({TEXT_INPUT, TEXT_DISPLAY, AUDIO_CAPTURE, AUDIO_PLAYBACK})  # declared from what is given


class Close(IntEnum):
    """Why the core closed the link (L2, L4, L5)."""

    STOPPING = 1001  # the core stops: reconnect
    OFF_CONTRACT = 4400  # `hello` late, off-contract or for another device: reconnect
    TOKEN_REFUSED = 4401  # forget the token and pair again
    REPLACED = 4409  # a newer link of this device took over: stop


class CapabilityNotAllowed(ProtocolError):
    """A message the device did not declare, or the household did not allow."""


class Satellite:
    def __init__(
        self,
        core: str | None,
        manifest: Manifest,
        *,
        tokens: TokenStore,
        body: Body | None = None,
        text_display: TextDisplay | None = None,
        text_input: bool = False,
        source: AudioSource | None = None,
        sink: AudioSink | None = None,
        display: Display | None = None,
        voice: Voice | None = None,
        camera: Camera | None = None,
        enrolls_by: Iterable[PairingMethod] = (),
        enrollment: str | None = None,
        locale: str | None = None,
        environ: Mapping[str, str] | None = None,
        ice_servers: list[str] | None = None,
        poll_s: float = 1.0,
        backoff_s: tuple[float, float] = (0.5, 30.0),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        `core`: the core's base URL; `None` takes it from the pairing URI.
        `manifest`: the device and the capabilities of its body and sensors; text and audio follow from:
        `text_display(text, final)`: shows the assistant's words (`text.display`);
        `text_input`: a person can type on the device, sent with `send_text` (`text.input`);
        `source`, `sink`: the microphone and the speaker (`audio.capture`, `audio.playback`), over WebRTC:
        they need `pip install nestor-satellite[voice]`. `ice_servers` (e.g.
        `["stun:stun.l.google.com:19302"]`) are only needed beyond the local network.
        `display`, `voice`: where a device code is presented (`device_code.display`, `device_code.speech`).
        Without either, a `sink` says it with the SDK's recorded voice.
        `camera`: QR codes of pairing URIs are read from it while pairing (`enrollment.qr`).
        `enrolls_by`: `PairingMethod.INPUT` when the driver can take a code typed on the device (`enroll`).
        `enrollment`: a pairing URI or an enrollment code from the device's configuration; by default
        `NESTOR_PAIRING_URI`, else `NESTOR_PAIRING_CODE`, from `environ` (the process environment).
        `locale`: the language people are addressed in; by default the environment's, else English.
        `transport`: how HTTP reaches the core (httpx's default, unless a test or a custom TLS setup needs another).
        """
        if declared := sorted(manifest.names & WIRED):
            raise ValueError(
                f"{', '.join(declared)} follow from what the satellite is given (text_display, text_input, "
                "source, sink): do not declare them"
            )
        env = os.environ if environ is None else environ
        self.messages = Messages(locale, env)
        provisioned = self._provisioned(enrollment, env)
        core = core or (provisioned.core if provisioned else None)
        if not core:
            raise ValueError(self.messages("no-core"))
        self.core, self.tokens = core.rstrip("/"), tokens
        self._audio: WebRtcAudio | None = _webrtc(ice_servers) if source is not None or sink is not None else None
        speech = None
        if sink is not None and display is None and voice is None:
            from nestor_satellite.speech import SpokenCode  # the voice extra is there: audio needs it

            speech = SpokenCode(sink, self.messages)
        wired = [
            name
            for name, given in ((TEXT_INPUT, text_input), (TEXT_DISPLAY, text_display is not None),
                                (AUDIO_CAPTURE, source is not None), (AUDIO_PLAYBACK, sink is not None))
            if given
        ]  # fmt: skip
        self.manifest = replace(
            manifest,
            capabilities=(*(Capability(name) for name in wired), *manifest.capabilities),
            pairing_methods=_methods(display, voice or speech, camera, enrolls_by),
        )
        self.body = body or Body()
        self._text_display = text_display
        self._source, self._sink = source, sink
        self._camera = camera
        device = self.manifest.device.id
        self._pairing = Pairing(
            self.manifest,
            messages=self.messages,
            display=display,
            voice=voice,
            speech=speech,
            enrollment=provisioned.code if provisioned else None,
            refused=tokens.refused(self.core, device),
            on_refused=lambda code, reason: tokens.refuse(self.core, device, code, reason),
            on_identify=self.body.identify,
            poll_s=poll_s,
        )
        self._transport = transport
        self._backoff = backoff_s
        self._outbox: asyncio.Queue[dict[str, Any]] | None = None
        self.welcome: wire.Welcome | None = None
        self.connections = 0  # links opened

    async def run(self) -> None:
        """Run until cancelled, or until a newer link of this device replaces this one: pair when
        needed, hold the link, and reconnect with a growing delay."""
        if self._sink is not None:
            await self._sink.start()
        delay = self._backoff[0]
        try:
            async with httpx.AsyncClient(base_url=self.core, timeout=10, transport=self._transport) as http:
                while True:
                    failure, closed, welcomed = None, None, False
                    try:
                        token = await self._token(http)
                        closed, welcomed = await self._link_once(http, token)
                    except PairingFailed:
                        raise  # waiting will not fix it: a person must update the device or the core
                    except Exception as error:  # the network, the core restarting: try again later
                        failure = str(error) or type(error).__name__
                    if closed is Close.TOKEN_REFUSED:
                        logger.warning("%s", self.messages("token-refused"))
                        self.tokens.forget()
                        continue
                    if closed is Close.REPLACED:
                        logger.warning("%s", self.messages("link-replaced"))
                        return
                    delay = self._backoff[0] if welcomed else min(delay * 2, self._backoff[1])
                    seconds = round(delay, 1)
                    if failure is not None:
                        logger.warning("%s", self.messages("link-failed", error=failure, seconds=seconds))
                    elif closed is Close.OFF_CONTRACT:
                        logger.warning("%s", self.messages("link-off-contract", seconds=seconds))
                    else:
                        logger.info("%s", self.messages("link-closed", seconds=seconds))
                    await asyncio.sleep(delay)
        finally:
            if self._sink is not None:
                await self._sink.close()

    async def enroll(self, code_or_uri: str, *, method: PairingMethod = PairingMethod.INPUT) -> EnrollmentOutcome:
        """Hand in an enrollment code (or a pairing URI) typed on the device, at any time (QR codes
        read by the camera come here too). It is sent by the pairing in progress, or the next one;
        the outcome once the core answered."""
        if not method.enrollment or method not in self.manifest.pairing_methods:
            raise ValueError(f"{method} is not among this satellite's enrollment methods")
        try:
            code = Enrollment.read(code_or_uri).code
        except InvalidEnrollment:
            logger.warning("%s", self.messages("enrollment-invalid"))
            return EnrollmentOutcome.INVALID
        if not self._pairing.active and self.tokens.load(self.core, self.manifest.device.id):
            logger.info("%s", self.messages("enrollment-not-pending"))
            return EnrollmentOutcome.NOT_PENDING
        return await self._pairing.enroll(code, method)

    def send_text(self, text: str) -> bool:
        """Send what a person typed, a turn of the room's conversation; `False` when not connected
        (nothing is queued)."""
        return self._send(TEXT_INPUT, wire.text_input(text))

    def person_detected(self, count: int, confidence: float) -> bool:
        """Report people seen now; `False` when not connected (observations are not queued)."""
        return self._send(PERSON_DETECTION, wire.person_detected(count, confidence))

    def speech_activity(self, active: bool) -> bool:
        return self._send(SPEECH_ACTIVITY, wire.speech_activity(active))

    def _send(self, capability: str, message: dict[str, Any]) -> bool:
        allowed = self.welcome.capabilities if self.welcome else self.manifest.names
        if capability not in self.manifest.names or capability not in allowed:
            raise CapabilityNotAllowed(f"{message['type']} needs the {capability} capability, declared and allowed")
        if self._outbox is None:
            return False
        self._outbox.put_nowait(message)
        return True

    async def _token(self, http: httpx.AsyncClient) -> str:
        device = self.manifest.device.id
        if token := self.tokens.load(self.core, device):
            return token
        scanning = asyncio.ensure_future(self._scan()) if self._camera is not None else None
        try:
            token = await self._pairing.pair(http)
        finally:
            if scanning is not None:
                scanning.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scanning
        self.tokens.save(self.core, device, token)
        return token

    async def _scan(self, interval_s: float = 0.2) -> None:
        """While pairing: the camera on, each new QR code handed in as an enrollment (`enrollment.qr`)."""
        assert self._camera is not None
        seen: set[str] = set()
        sent: set[asyncio.Task[EnrollmentOutcome]] = set()
        try:
            await self._camera.start()
        except Exception as error:
            logger.warning("%s", self.messages("camera-failed", error=str(error) or type(error).__name__))
            return
        try:
            while True:
                for text in read_qr(await self._camera.read()):
                    if text not in seen:
                        seen.add(text)
                        task = asyncio.ensure_future(self.enroll(text, method=PairingMethod.QR))
                        sent.add(task)
                        task.add_done_callback(sent.discard)
                await asyncio.sleep(interval_s)
        except EOFError as error:
            logger.warning("%s", self.messages("camera-failed", error=str(error)))
        finally:
            await self._camera.close()

    async def _link_once(self, http: httpx.AsyncClient, token: str) -> tuple[int | None, bool]:
        """One link, until the core closes it or it is lost: how it was closed, and whether it was welcomed."""
        url = "ws" + self.core.removeprefix("http") + f"/satellites/{self.manifest.device.id}/link"
        outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        dispatcher = Dispatcher(self.body, outbox.put_nowait, text_display=self._text_display)
        tasks: list[asyncio.Task[Any]] = []
        refused = asyncio.Event()  # the core refused the token on the audio side
        welcomed = False
        async with websockets.connect(
            url, open_timeout=10, ping_interval=PING_S, ping_timeout=SILENCE_S - PING_S
        ) as link:
            await link.send(json.dumps(wire.hello(self.manifest, token)))
            self.connections += 1
            try:
                async with asyncio.timeout(WELCOME_S) as waiting:
                    async for raw in link:
                        if (welcome := dispatcher.handle(raw)) is None or welcomed:
                            continue
                        waiting.reschedule(None)
                        welcomed, self.welcome, self._outbox = True, welcome, outbox
                        tasks.append(asyncio.create_task(_write(link, outbox)))
                        if self._audio is not None and welcome.capabilities & AUDIO:
                            hearing = self._hear(http, token, welcome.capabilities, link, refused)
                            tasks.append(asyncio.create_task(hearing))
                        self.body.connected(True)
            except websockets.ConnectionClosed:
                pass
            finally:
                self._outbox = None
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if welcomed:
                    self.body.connected(False)
        return (Close.TOKEN_REFUSED if refused.is_set() else _close(link.close_code)), welcomed

    async def _hear(
        self, http: httpx.AsyncClient, token: str, allowed: frozenset[str], link: Any, refused: asyncio.Event
    ) -> None:
        """While the link is up: the audio connection, offered again with a growing delay when it fails."""
        from nestor_satellite.webrtc import TokenRefused

        assert self._audio is not None
        delay = self._backoff[0]
        while True:
            try:
                await self._audio.connect(
                    http,
                    self.manifest.device.id,
                    token,
                    source=self._source if AUDIO_CAPTURE in allowed else None,
                    sink=self._sink if AUDIO_PLAYBACK in allowed else None,
                    on_frame=self.body.heartbeat,
                )
                delay = self._backoff[0]
            except TokenRefused:
                refused.set()
                await link.close()
                return
            except Exception as error:
                delay = min(delay * 2, self._backoff[1])
                failure = str(error) or type(error).__name__
                logger.warning("%s", self.messages("audio-failed", error=failure, seconds=round(delay, 1)))
            await asyncio.sleep(delay)

    def _provisioned(self, enrollment: str | None, env: Mapping[str, str]) -> Enrollment | None:
        source, text = next(
            ((name, value) for name, value in (("enrollment", enrollment), (PAIRING_URI, env.get(PAIRING_URI)),
                                               (PAIRING_CODE, env.get(PAIRING_CODE))) if value),
            (None, None),
        )  # fmt: skip
        if text is None:
            return None
        try:
            return Enrollment.read(text)
        except InvalidEnrollment:
            raise InvalidEnrollment(self.messages("enrollment-misconfigured", source=source)) from None


async def _write(link: Any, outbox: asyncio.Queue[dict[str, Any]]) -> None:
    """Send what was queued, in order, until the link closes."""
    with contextlib.suppress(websockets.ConnectionClosed):
        while True:
            await link.send(json.dumps(await outbox.get()))


def _close(code: int | None) -> int | None:
    try:
        return Close(code)
    except ValueError:
        return code


def _webrtc(ice_servers: list[str] | None) -> "WebRtcAudio":
    try:
        from nestor_satellite.webrtc import WebRtcAudio
    except ImportError as error:
        raise RuntimeError("a microphone or a speaker needs `pip install nestor-satellite[voice]`") from error
    return WebRtcAudio(ice_servers)


def _methods(
    display: Display | None, voice: object | None, camera: Camera | None, enrolls_by: Iterable[PairingMethod]
) -> tuple[PairingMethod, ...]:
    """What a person can pair this device with: provisioning always, and what its outputs and inputs allow.
    A QR code is declared only with a camera to read it: the SDK reads it, a driver cannot claim it."""
    inputs = set(enrolls_by)
    if not inputs <= {PairingMethod.INPUT}:
        raise ValueError("enrolls_by takes PairingMethod.INPUT only: the others follow from the outputs and the camera")
    offered = inputs | {PairingMethod.PROVISIONING}
    offered |= {PairingMethod.DISPLAY} if display is not None else set()
    offered |= {PairingMethod.SPEECH} if voice is not None else set()
    offered |= {PairingMethod.QR} if camera is not None else set()
    return tuple(m for m in PairingMethod if m in offered)
