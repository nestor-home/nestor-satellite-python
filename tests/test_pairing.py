"""Pairing, against the real pairing service of the Nestor server (in-process), or a scripted core for
answers the real one cannot be led to give."""

import asyncio
import contextlib
import json
import logging
import re
import stat
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from nestor.adapters.channels.satellites.gateway import SatelliteGateway
from nestor.modules.journal import Journal
from nestor.modules.satellites import AttemptLimiter
from nestor.modules.satellites import Device as ServerDevice
from nestor.modules.satellites import Manifest as ServerManifest
from nestor.modules.satellites import Pairing as Service
from nestor.modules.satellites import PairingMethod as ServerMethod
from nestor.testing.journal import MemoryEventLog

from nestor_satellite import (
    IDENTIFY,
    Camera,
    Device,
    Display,
    EnrollmentOutcome,
    Frame,
    Manifest,
    PairingFailed,
    RecordingSink,
    Satellite,
    TokenStore,
)
from nestor_satellite.i18n import Messages
from nestor_satellite.pairing import Pairing, code_hash
from nestor_satellite.protocol import PairingMethod
from nestor_satellite.speech import SpokenCode

from .qr import blank, qr_frame

CORE = "http://nestor.local:8471"
DEVICE = Device("enceinte-salon", "raspberry-pi-4", "salon")
MANIFEST = Manifest.of(DEVICE, "vision.person_detection")
EVERY_ENROLLMENT = (PairingMethod.INPUT, PairingMethod.PROVISIONING, PairingMethod.QR)


def test_the_token_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "config" / "salon.json"
    path.parent.mkdir()
    path.write_text("{}")
    path.chmod(0o644)  # left more open by someone else
    store = TokenStore(path)
    store.save(CORE, "enceinte-salon", "nst1_secret")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load(CORE, "enceinte-salon") == "nst1_secret"


@pytest.mark.parametrize(("core", "device"), [("http://other:8471", "enceinte-salon"), (CORE, "reachy-salon")])
def test_a_token_for_another_core_or_device_is_not_used(core: str, device: str, tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(CORE, "enceinte-salon", "nst1_secret")
    assert store.load(core, device) is None


def test_a_missing_or_corrupt_token_file_means_no_token(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    assert store.load(CORE, "enceinte-salon") is None
    store.path.write_text("not json")
    assert store.load(CORE, "enceinte-salon") is None
    store.forget()
    store.forget()
    assert not store.path.exists()


class Core(httpx.AsyncBaseTransport):
    """The server's pairing service behind its HTTP gateway; every request the device sends is noted."""

    def __init__(self, **options: Any) -> None:
        self.service = Service(Journal(MemoryEventLog()), **options)
        app = FastAPI()
        SatelliteGateway(self.service, on_link=self._never_connected).install(app)
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._app = httpx.ASGITransport(app)
        self.http = httpx.AsyncClient(transport=self, base_url=CORE)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.sent.append((request.url.path, json.loads(request.content)))
        return await self._app.handle_async_request(request)

    async def _never_connected(self, *_: Any) -> None: ...

    async def code(self, method: ServerMethod = ServerMethod.PROVISIONING, **options: Any) -> str:
        started = await self.service.start_addition(method, **options)
        assert started.code is not None
        return started.code

    def codes_sent(self) -> list[str]:
        return [body["enrollment_code"] for _path, body in self.sent if "enrollment_code" in body]

    async def type_device_code(self, code: str) -> None:
        [pending] = self.service.pending()
        await self.service.approve(code, pairing_id=pending.id)


class Screen(Display):
    def __init__(self) -> None:
        self.shown: list[tuple[str, str | None]] = []
        self.cleared = 0

    def show(self, text: str, code: str | None = None) -> None:
        self.shown.append((text, code))

    def clear(self) -> None:
        self.cleared += 1


def pairing(
    *,
    methods: tuple[PairingMethod, ...] = (PairingMethod.PROVISIONING,),
    manifest: Manifest = MANIFEST,
    locale: str = "en",
    poll_s: float = 0.02,
    **options: Any,
) -> Pairing:
    manifest = Manifest(manifest.device, manifest.capabilities, methods)
    return Pairing(manifest, messages=Messages(locale), poll_s=poll_s, **options)


async def until(condition: Callable[[], object], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.01)


@pytest.fixture
async def core() -> AsyncIterator[Core]:
    core = Core()
    async with core.http:
        yield core


async def test_a_headless_device_logs_its_code_and_how_else_it_can_be_paired(
    core: Core, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, "nestor_satellite")
    device = pairing()
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: "Pairing code" in caplog.text)
    code = caplog.text.split("Pairing code ")[1][:6]
    await core.type_device_code(code)

    assert (await running).startswith("nst1_")
    assert f"Pairing code {code}: enter it in Nestor to pair this device." in caplog.text
    assert "set NESTOR_PAIRING_URI" in caplog.text
    assert "Paired with Nestor." in caplog.text


async def test_the_device_code_is_shown_and_said_twice_digit_by_digit_and_put_away_once_paired(core: Core) -> None:
    screen, said = Screen(), []
    device = pairing(methods=(PairingMethod.DISPLAY, PairingMethod.SPEECH, PairingMethod.PROVISIONING),
                     display=screen, voice=said.append, locale="fr")  # fmt: skip
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)
    [(text, code)] = screen.shown
    assert code is not None and text == "Saisissez ce code dans Nestor pour appairer cet appareil."
    digits = ", ".join(code)
    assert said == [f"Code d'appairage : {digits}. Je répète : {digits}."]

    await core.type_device_code(code)
    await running
    assert screen.cleared == 1


async def test_a_configured_enrollment_code_pairs_at_once_and_no_device_code_is_presented(
    core: Core, caplog: pytest.LogCaptureFixture
) -> None:
    screen, said = Screen(), []
    device = pairing(enrollment=await core.code(), display=screen, voice=said.append,
                     methods=(PairingMethod.DISPLAY, PairingMethod.SPEECH, PairingMethod.PROVISIONING))  # fmt: skip
    assert (await device.pair(core.http)).startswith("nst1_")
    assert not screen.shown and not said and "Pairing code" not in caplog.text
    [(path, body)] = core.sent
    assert (path, body["method"]) == ("/satellites/pairings", "enrollment.provisioning")


@pytest.mark.parametrize("refusal", ["unknown", "used", "cancelled"])
async def test_a_refused_enrollment_code_is_said_never_sent_again_and_a_device_code_takes_over(
    core: Core, refusal: str
) -> None:
    code = "K7QF2M9X"
    if refusal != "unknown":
        code = await core.code()
        if refusal == "used":  # by another device
            other = ServerManifest(ServerDevice("deja-la", "x", "salon"), pairing_methods=(ServerMethod.PROVISIONING,))
            await core.service.request(other, enrollment_code=code, method=ServerMethod.PROVISIONING)
        else:
            [addition] = core.service.additions()
            await core.service.cancel_addition(addition.id)
    screen = Screen()
    device = pairing(enrollment=code, display=screen, methods=(PairingMethod.DISPLAY, PairingMethod.PROVISIONING))
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)

    [(text, device_code)] = screen.shown
    reason = {"unknown": "is not valid", "used": "has already been used", "cancelled": "was cancelled"}[refusal]
    assert text == f"This enrollment code {reason}. Enter this code in Nestor to pair this device."
    assert await device.enroll(code, PairingMethod.PROVISIONING) is EnrollmentOutcome.REFUSED
    assert device_code is not None
    await core.type_device_code(device_code)
    await running
    assert core.codes_sent().count(code) == 1


async def test_an_enrollment_code_typed_while_waiting_pairs_the_device(core: Core) -> None:
    screen = Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, *EVERY_ENROLLMENT))
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)

    assert await device.enroll(await core.code(ServerMethod.INPUT), PairingMethod.INPUT) is EnrollmentOutcome.ACCEPTED
    assert (await running).startswith("nst1_")
    assert screen.cleared == 1, "it stops presenting its device code"
    assert core.sent[-1][0].endswith("/enrollment")


async def test_an_enrollment_code_handed_in_before_pairing_starts_is_sent_with_the_request(core: Core) -> None:
    device = pairing(methods=EVERY_ENROLLMENT)
    code = await core.code(ServerMethod.QR)
    outcome = asyncio.create_task(device.enroll(code, PairingMethod.QR))
    await asyncio.sleep(0)
    assert (await device.pair(core.http)).startswith("nst1_")
    assert await outcome is EnrollmentOutcome.ACCEPTED
    assert [(path, body.get("method")) for path, body in core.sent] == [("/satellites/pairings", "enrollment.qr")]


async def test_when_the_device_code_wins_the_enrollment_code_is_not_spent(core: Core) -> None:
    screen = Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, *EVERY_ENROLLMENT), poll_s=0.5)
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)
    await core.type_device_code(screen.shown[0][1] or "")

    assert (
        await device.enroll(await core.code(ServerMethod.INPUT), PairingMethod.INPUT) is EnrollmentOutcome.NOT_PENDING
    )
    assert (await running).startswith("nst1_")
    assert core.service.additions(), "the enrollment code can still pair another device"


async def test_an_enrollment_code_refused_while_waiting_is_said_and_the_device_code_shown_again(core: Core) -> None:
    screen = Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, *EVERY_ENROLLMENT))
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)

    assert await device.enroll("K7QF2M9X", PairingMethod.INPUT) is EnrollmentOutcome.REFUSED
    first, again = screen.shown
    assert again == (f"This enrollment code is not valid. {first[0]}", first[1])
    await core.type_device_code(first[1] or "")
    await running


async def test_a_pending_device_asked_to_identify_itself_does_and_keeps_waiting(core: Core) -> None:
    screen, noticed = Screen(), []
    device = pairing(display=screen, manifest=Manifest.of(DEVICE, IDENTIFY), on_identify=lambda: noticed.append(1),
                     methods=(PairingMethod.DISPLAY, PairingMethod.PROVISIONING))  # fmt: skip
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)
    [pending] = core.service.pending()
    await core.service.identify_pending(pending.id)
    await until(lambda: noticed)
    await asyncio.sleep(0.1)

    assert not running.done() and noticed == [1], "once per request"
    await core.type_device_code(screen.shown[0][1] or "")
    await running


async def test_an_expired_device_code_is_replaced_by_a_new_one(caplog: pytest.LogCaptureFixture) -> None:
    core, screen = Core(device_code_ttl_s=0.1), Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, PairingMethod.PROVISIONING))
    async with core.http:
        running = asyncio.create_task(device.pair(core.http))
        await until(lambda: len(screen.shown) >= 2)
        await core.type_device_code(screen.shown[-1][1] or "")
        await running
    assert "The pairing code expired: asking for a new one." in caplog.text


async def test_a_rate_limited_device_waits_as_told_then_pairs(caplog: pytest.LogCaptureFixture) -> None:
    limiter = AttemptLimiter(per_source=1, window_s=1.0)
    core = Core(limiter=limiter)
    async with core.http:
        code = await core.code()
        limiter.failed("127.0.0.1", datetime.now(UTC))  # someone just tried a wrong code from here
        started = time.monotonic()
        await pairing(enrollment=code).pair(core.http)
    assert time.monotonic() - started >= 1.0
    assert core.codes_sent() == [code, code]
    assert "Too many attempts: trying again in 1 s." in caplog.text


async def test_a_code_typed_while_rate_limited_is_held_back_then_sent() -> None:
    limiter = AttemptLimiter(per_source=1, window_s=1.0)
    core, screen = Core(limiter=limiter), Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, *EVERY_ENROLLMENT))
    async with core.http:
        running = asyncio.create_task(device.pair(core.http))
        await until(lambda: screen.shown)
        assert await device.enroll("K7QF2M9X", PairingMethod.INPUT) is EnrollmentOutcome.REFUSED
        started = time.monotonic()
        assert await device.enroll(await core.code(), PairingMethod.INPUT) is EnrollmentOutcome.ACCEPTED
        assert time.monotonic() - started >= 1.0
        await running


class Scripted(httpx.AsyncBaseTransport):
    """A core answering pairing requests from a script."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self.answers.pop(0)


def error(status: int, code: str, **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"error": code, "detail": f"{code} (English, for logs)"} | extra)


async def run(transport: httpx.AsyncBaseTransport, device: Pairing) -> str:
    async with httpx.AsyncClient(transport=transport, base_url=CORE) as http:
        return await device.pair(http)


async def test_a_busy_core_is_asked_again_after_a_while(caplog: pytest.LogCaptureFixture) -> None:
    ticket = {"pairing_id": "p1", "status": "approved", "expires_at": "2026-10-30T12:00:00+00:00"}
    core = Scripted(error(429, "too_many_pending"), httpx.Response(201, json=ticket),
                    httpx.Response(200, json={"status": "approved", "token": "nst1_ok"}))  # fmt: skip
    assert await run(core, pairing(retry_s=0.01)) == "nst1_ok"
    assert "Too many devices are waiting to pair: trying again in 0 s." in caplog.text


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (error(422, "protocol_unsupported"), "Nestor ne parle pas la version du protocole de cet appareil"),
        (error(422, "invalid_request"), "Nestor a rejeté la demande d'appairage (invalid_request (English, for logs))"),
        (httpx.Response(404, text="Not Found"), "Pas de service d'appairage Nestor à http://nestor.local:8471"),
    ],
    ids=["another protocol version", "a request it rejects", "no pairing service"],
)
async def test_what_waiting_cannot_fix_fails_clearly_on_every_output(answer: httpx.Response, said: str) -> None:
    screen, voice = Screen(), []
    device = pairing(display=screen, voice=voice.append, locale="fr",
                     methods=(PairingMethod.DISPLAY, PairingMethod.SPEECH, PairingMethod.PROVISIONING))  # fmt: skip
    with pytest.raises(PairingFailed, match=re.escape(said)):
        await run(Scripted(answer), device)
    assert screen.shown[0][0].startswith(said) and voice[0].startswith(said)


async def test_an_unwell_core_is_an_error_to_retry_later() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await run(Scripted(httpx.Response(502, text="Bad Gateway")), pairing())


async def test_an_enrollment_method_the_device_did_not_declare_fails_clearly(core: Core) -> None:
    device = pairing()  # provisioning only
    outcome = asyncio.create_task(device.enroll(await core.code(), PairingMethod.QR))
    await asyncio.sleep(0)
    with pytest.raises(PairingFailed, match="Nestor rejected the pairing request"):
        await device.pair(core.http)
    with pytest.raises(PairingFailed):
        await outcome


async def test_stopping_the_pairing_stops_those_waiting_for_their_code(core: Core) -> None:
    screen = Screen()
    device = pairing(display=screen, methods=(PairingMethod.DISPLAY, *EVERY_ENROLLMENT), poll_s=0.5)
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: screen.shown)
    await core.type_device_code(screen.shown[0][1] or "")
    outcome = asyncio.create_task(device.enroll(await core.code(), PairingMethod.INPUT))
    await until(lambda: core.sent[-1][0].endswith("/enrollment"))
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outcome
    with pytest.raises(asyncio.CancelledError):
        await running
    assert not device.active


PENDING = {"pairing_id": "p1", "status": "pending", "expires_at": "2026-10-30T12:00:00+00:00", "device_code": "042917"}
APPROVED = {"pairing_id": "p2", "status": "approved", "expires_at": "2026-10-30T12:00:00+00:00"}


class Flaky(httpx.AsyncBaseTransport):
    """A core whose enrollment endpoint fails once (`lost`), then pairs whoever sends a code."""

    def __init__(self, lost: Callable[[], httpx.Response]) -> None:
        self.lost, self.failed, self.requests = lost, False, []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/enrollment") and not self.failed:
            self.failed = True
            return self.lost()
        if request.method == "POST":
            self.requests.append(json.loads(request.content))
            return httpx.Response(201, json=APPROVED if "enrollment_code" in self.requests[-1] else PENDING)
        if request.url.path.endswith("/p2"):
            return httpx.Response(200, json={"status": "approved", "token": "nst1_ok"})
        return httpx.Response(200, json={"status": "pending"})


def unreachable() -> httpx.Response:
    raise httpx.ConnectError("the core went away")


@pytest.mark.parametrize(
    "lost", [unreachable, lambda: httpx.Response(502, text="Bad Gateway")], ids=["unreachable", "unwell"]
)
async def test_a_code_handed_in_while_the_core_is_away_is_sent_once_it_is_back(
    lost: Callable[[], httpx.Response],
) -> None:
    core = Flaky(lost)
    device = pairing(methods=EVERY_ENROLLMENT)
    async with httpx.AsyncClient(transport=core, base_url=CORE) as http:
        running = asyncio.create_task(device.pair(http))
        outcome = asyncio.create_task(device.enroll("K7QF2M9X", PairingMethod.INPUT))
        with pytest.raises(httpx.HTTPError):
            await running  # the satellite tries again later
        assert await device.pair(http) == "nst1_ok"
    assert await outcome is EnrollmentOutcome.ACCEPTED
    assert core.requests[-1]["enrollment_code"] == "K7QF2M9X"


async def test_stopping_while_the_core_is_asked_about_a_code_stops_whoever_handed_it_in() -> None:
    asked = asyncio.Event()

    async def core(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/enrollment"):
            asked.set()
            await asyncio.Event().wait()  # never answers
        return (
            httpx.Response(201, json=PENDING)
            if request.method == "POST"
            else httpx.Response(200, json={"status": "pending"})
        )

    device = pairing(methods=EVERY_ENROLLMENT)
    async with httpx.AsyncClient(transport=httpx.MockTransport(core), base_url=CORE) as http:
        running = asyncio.create_task(device.pair(http))
        outcome = asyncio.create_task(device.enroll("K7QF2M9X", PairingMethod.INPUT))
        await asked.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outcome


async def test_a_code_held_back_by_the_rate_limit_is_not_needed_once_the_device_code_is_typed() -> None:
    polls = 0

    def core(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.path.endswith("/enrollment"):
            limited = {"error": "too_many_attempts", "detail": "wait", "retry_after_s": 30}
            return httpx.Response(429, headers={"Retry-After": "30"}, json=limited)
        if request.method == "POST":
            return httpx.Response(201, json=PENDING)
        polls += 1
        return httpx.Response(200, json={"status": "pending"} if polls < 3 else {"status": "approved", "token": "t"})

    device = pairing(methods=EVERY_ENROLLMENT)
    async with httpx.AsyncClient(transport=httpx.MockTransport(core), base_url=CORE) as http:
        running = asyncio.create_task(device.pair(http))
        await until(lambda: polls)
        outcome = asyncio.create_task(device.enroll("K7QF2M9X", PairingMethod.INPUT))
        assert await asyncio.wait_for(running, 2) == "t", "it kept polling while the code was held back"
    assert await outcome is EnrollmentOutcome.NOT_PENDING


async def test_a_code_the_core_did_not_take_with_the_request_is_offered_again_while_pending() -> None:
    sent: list[str] = []

    def core(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            sent.append(request.url.path)
            return httpx.Response(
                201 if len(sent) == 1 else 200, json=PENDING if len(sent) == 1 else {"status": "approved"}
            )
        return httpx.Response(
            200, json={"status": "approved", "token": "t"} if len(sent) > 1 else {"status": "pending"}
        )

    assert await run(httpx.MockTransport(core), pairing(enrollment="K7QF2M9X")) == "t"
    assert sent == ["/satellites/pairings", "/satellites/pairings/p1/enrollment"]


async def test_the_spoken_code_stops_as_soon_as_the_device_is_paired(
    core: Core, caplog: pytest.LogCaptureFixture
) -> None:
    sink = RecordingSink(16_000)
    voice = SpokenCode(sink, Messages("en"))
    device = pairing(speech=voice, methods=(PairingMethod.SPEECH, PairingMethod.PROVISIONING))
    running = asyncio.create_task(device.pair(core.http))
    await until(lambda: len(sink.played) > 16_000)  # half a second in
    await core.type_device_code(caplog.text.split("Pairing code ")[1][:6])
    await running
    played = len(sink.played)
    await asyncio.sleep(0.3)

    assert len(sink.played) == played, "silent once paired"
    assert not voice.speaking
    assert played / 2 / 16_000 < 3, "well before the end of the announcement"


async def test_a_pairing_stopped_while_the_code_is_said_hushes_it(core: Core) -> None:
    voice = SpokenCode(RecordingSink(16_000), Messages("en"))
    running = asyncio.create_task(pairing(speech=voice).pair(core.http))
    await until(lambda: voice.speaking)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert not voice.speaking


def test_refused_codes_are_kept_as_hashes_and_outlive_the_token(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "state.json")
    store.refuse(CORE, "enceinte-salon", "K7QF2M9X", "expired")
    store.save(CORE, "enceinte-salon", "nst1_secret")
    store.forget()

    assert store.load(CORE, "enceinte-salon") is None
    assert store.refused(CORE, "enceinte-salon") == {code_hash("K7QF2M9X"): "expired"}
    assert store.refused(CORE, "reachy-salon") == {}, "another device's state"
    assert "K7QF2M9X" not in store.path.read_text()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


async def test_a_restarted_device_never_sends_a_refused_code_again(
    core: Core, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = TokenStore(tmp_path / "state.json")

    def started(screen: Screen) -> Pairing:  # what a Satellite gives its pairing, with this state file
        return pairing(enrollment="K7QF2M9X", display=screen, refused=store.refused(CORE, DEVICE.id),
                       on_refused=lambda code, reason: store.refuse(CORE, DEVICE.id, code, reason),
                       methods=(PairingMethod.DISPLAY, PairingMethod.PROVISIONING))  # fmt: skip

    for _run in range(2):  # the second time, as after a restart with the same NESTOR_PAIRING_URI
        screen = Screen()
        running = asyncio.create_task(started(screen).pair(core.http))
        await until(lambda: screen.shown)  # noqa: B023
        await core.type_device_code(screen.shown[0][1] or "")
        await running
        assert screen.shown[0][0].startswith("This enrollment code is not valid."), "said on its outputs"

    assert core.codes_sent() == ["K7QF2M9X"]


class Shown(Camera):
    """A camera looking at a QR code, after a few frames of nothing."""

    def __init__(self, text: str) -> None:
        self.frames = [blank(), qr_frame("not a pairing URI"), qr_frame(text)]
        self.opened = self.closed = 0

    async def start(self) -> None:
        self.opened += 1

    async def read(self) -> Frame:
        return self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]

    async def close(self) -> None:
        self.closed += 1


async def test_a_satellite_shown_the_qr_code_of_a_pairing_uri_pairs_with_it(core: Core, tmp_path: Path) -> None:
    code = await core.code(ServerMethod.QR)
    camera = Shown(f"nestor+http://nestor.local:8471/?code={code}")
    device = Satellite(CORE, MANIFEST, tokens=TokenStore(tmp_path / "t.json"), camera=camera, environ={},
                       transport=core, poll_s=0.02)  # fmt: skip
    assert PairingMethod.QR in device.manifest.pairing_methods
    running = asyncio.create_task(device.run())
    await until(lambda: device.tokens.load(CORE, DEVICE.id))
    running.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await running

    assert [body.get("method") for _path, body in core.sent if "enrollment_code" in body] == ["enrollment.qr"]
    assert (camera.opened, camera.closed) == (1, 1), "the camera is on while pairing only"


class Broken(Camera):
    async def start(self) -> None:
        raise OSError("no camera")

    async def read(self) -> Frame:
        raise AssertionError("never read")


async def test_a_camera_that_does_not_open_leaves_the_other_ways_to_pair(
    core: Core, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    screen = Screen()
    device = Satellite(CORE, MANIFEST, tokens=TokenStore(tmp_path / "t.json"), camera=Broken(), display=screen,
                       environ={}, transport=core, poll_s=0.02, locale="fr")  # fmt: skip
    running = asyncio.create_task(device.run())
    await until(lambda: screen.shown)
    await core.type_device_code(screen.shown[0][1] or "")
    await until(lambda: device.tokens.load(CORE, DEVICE.id))
    running.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await running
    assert "La caméra est indisponible (no camera) : appairez cet appareil autrement." in caplog.text
