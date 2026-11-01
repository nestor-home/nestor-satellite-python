"""A satellite's life: pair once, then stay connected to the core, reconnecting when the link drops.

    satellite = Satellite("http://nestor.local:8471", manifest, tokens=TokenStore("~/.config/me.json"),
                          body=MyBody(), source=Microphone(), sink=Speaker(), display=MyScreen())
    await satellite.run()

Pairing (see `pairing`): an enrollment code given here or in the environment (`NESTOR_PAIRING_URI`,
`NESTOR_PAIRING_CODE`) is sent at once; otherwise the device code is presented on the `display`,
said by the `voice` (a device with a speaker and no screen says it with the SDK's own recorded
voice, see `speech`), and always logged. `enroll` hands in a code typed meanwhile; a `camera` reads
the QR code of a pairing URI. The manifest's `pairing_methods` follow from what was given: a driver
declares its capabilities only.

Each connection is a WebRTC peer connection: the microphone and the core's voice as audio tracks,
the `nestor` data channel for `hello`/`welcome`, intents (acknowledged) and observations.
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

import httpx
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

from nestor_satellite import wire
from nestor_satellite.audio import AudioSink, AudioSource, SilenceSource, SourceTrack, play
from nestor_satellite.body import Body
from nestor_satellite.camera import Camera, read_qr
from nestor_satellite.codes import Enrollment, InvalidEnrollment
from nestor_satellite.dispatch import Dispatcher, Outcome
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
    Manifest,
    PairingMethod,
    ProtocolError,
)
from nestor_satellite.speech import SpokenCode

logger = logging.getLogger(__name__)


class CapabilityNotAllowed(ProtocolError):
    """An observation the device did not declare, or the household did not allow."""


class _Refused(Exception):
    """The core does not know this token any more: pair again."""


class Satellite:
    def __init__(
        self,
        core: str | None,
        manifest: Manifest,
        *,
        tokens: TokenStore,
        body: Body | None = None,
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
        `display`, `voice`: where a device code is presented (`device_code.display`, `device_code.speech`).
        Without either, a `sink` with `audio.playback` declared says it with the SDK's recorded voice.
        `camera`: QR codes of pairing URIs are read from it while pairing (`enrollment.qr`).
        `enrolls_by`: `PairingMethod.INPUT` when the driver can take a code typed on the device (`enroll`).
        `enrollment`: a pairing URI or an enrollment code from the device's configuration; by default
        `NESTOR_PAIRING_URI`, else `NESTOR_PAIRING_CODE`, from `environ` (the process environment).
        `locale`: the language people are addressed in; by default the environment's, else English.
        `ice_servers` (e.g. `["stun:stun.l.google.com:19302"]`) are only needed beyond the local network.
        `transport`: how HTTP reaches the core (httpx's default, unless a test or a custom TLS setup needs another).
        """
        env = os.environ if environ is None else environ
        self.messages = Messages(locale, env)
        provisioned = self._provisioned(enrollment, env)
        core = core or (provisioned.core if provisioned else None)
        if not core:
            raise ValueError(self.messages("no-core"))
        self.core, self.tokens = core.rstrip("/"), tokens
        speaker = sink is not None and AUDIO_PLAYBACK in manifest.names
        speech = SpokenCode(sink, self.messages) if speaker and display is None and voice is None else None
        self.manifest = replace(manifest, pairing_methods=_methods(display, voice or speech, camera, enrolls_by))
        self.body = body or Body()
        self._source = source if source is not None and AUDIO_CAPTURE in manifest.names else None
        self._sink = sink
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
        self._ice = [RTCIceServer(urls=url) for url in ice_servers or []]
        self._backoff = backoff_s
        self._channel: Any = None
        self.welcome: wire.Welcome | None = None
        self.connections = 0

    async def run(self) -> None:
        """Run until cancelled: pair when needed, connect, and reconnect with a growing delay."""
        audio = [a for a in (self._source, self._sink) if a is not None]
        for device in audio:
            await device.start()
        delay = self._backoff[0]
        try:
            async with httpx.AsyncClient(base_url=self.core, timeout=10, transport=self._transport) as http:
                while True:
                    failure = None
                    try:
                        token = await self._token(http)
                        welcomed = await self._connect_once(http, token)
                    except _Refused:
                        logger.warning("%s", self.messages("token-refused"))
                        self.tokens.forget()
                        continue
                    except PairingFailed:
                        raise  # waiting will not fix it: a person must update the device or the core
                    except Exception as error:  # the network, the core restarting: try again later
                        failure, welcomed = str(error) or type(error).__name__, False
                    delay = self._backoff[0] if welcomed else min(delay * 2, self._backoff[1])
                    if failure is not None:
                        logger.warning("%s", self.messages("link-failed", error=failure, seconds=round(delay, 1)))
                    await asyncio.sleep(delay)
        finally:
            for device in audio:
                await device.close()

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

    def person_detected(self, count: int, confidence: float) -> bool:
        """Report people seen now; `False` when not connected (observations are not queued)."""
        return self._observe(PERSON_DETECTION, wire.person_detected(count, confidence))

    def speech_activity(self, active: bool) -> bool:
        return self._observe(SPEECH_ACTIVITY, wire.speech_activity(active))

    def _observe(self, capability: str, message: dict[str, Any]) -> bool:
        allowed = self.welcome.capabilities if self.welcome else self.manifest.names
        if capability not in self.manifest.names or capability not in allowed:
            raise CapabilityNotAllowed(f"{message['type']} needs the {capability} capability, declared and allowed")
        if self._channel is None or self._channel.readyState != "open":
            return False
        self._channel.send(json.dumps(message))
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

    async def _connect_once(self, http: httpx.AsyncClient, token: str) -> bool:
        """One connection, until the core hangs up or the link fails; whether it was welcomed."""
        peer = RTCPeerConnection(RTCConfiguration(iceServers=self._ice))
        ended = asyncio.Event()
        players: set[asyncio.Task[None]] = set()
        peer.addTrack(SourceTrack(self._source or SilenceSource()))
        channel = peer.createDataChannel("nestor")
        welcomed = False

        @channel.on("open")
        def opened() -> None:
            channel.send(json.dumps(wire.hello(self.manifest)))

        dispatcher = Dispatcher(self.body, lambda message: channel.send(json.dumps(message)))

        @channel.on("message")
        def received(raw: str) -> None:
            nonlocal welcomed
            outcome = dispatcher.handle(raw)
            if outcome is Outcome.WELCOMED:
                welcomed, self.welcome = True, dispatcher.welcome
                self.body.connected(True)
            elif outcome is Outcome.HUNG_UP:
                ended.set()

        @channel.on("close")
        def closed() -> None:
            ended.set()

        @peer.on("track")
        def incoming(track: MediaStreamTrack) -> None:
            sink = self._sink or _Drain()
            players.add(asyncio.ensure_future(play(track, sink, on_frame=self.body.heartbeat)))

        @peer.on("connectionstatechange")
        def state() -> None:
            if peer.connectionState in {"failed", "closed"}:
                ended.set()

        try:
            await peer.setLocalDescription(await peer.createOffer())
            local = peer.localDescription
            response = await http.post(
                f"/satellites/{self.manifest.device.id}/offer",
                json={"sdp": local.sdp, "type": local.type},
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401:
                raise _Refused
            answer = response.raise_for_status().json()
            await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            self._channel = channel
            self.connections += 1
            await ended.wait()
            return welcomed
        finally:
            self._channel = None
            if welcomed:
                self.body.connected(False)
            await peer.close()
            for player in players:
                player.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await player

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


class _Drain(AudioSink):
    """No speaker: received audio is read (so the link stays healthy) and dropped."""

    async def write(self, pcm: bytes) -> None: ...
