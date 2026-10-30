"""Pairing with the core, as `nestor.satellite/2` defines it (rules P1 to P8).

One decision. With an enrollment code (configured, typed or scanned), the device sends it with its
request and is paired at once. Otherwise it asks for a device code and presents it through every
output it has: a screen, a voice, and always the log. While it waits, an enrollment code handed in
by the driver is sent too: the first proof the core accepts wins. A refused enrollment code is never
sent again, and the device falls back to a device code so a person can still pair it.

The token is the device's only credential: it is written once, readable by its owner only (mode
600), and forgotten when the core refuses it (revoked, or paired again elsewhere).
"""

import asyncio
import contextlib
import json
import logging
import os
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from nestor_satellite.i18n import Messages
from nestor_satellite.protocol import Manifest, PairingMethod

logger = logging.getLogger(__name__)

PAIRING_URI = "NESTOR_PAIRING_URI"  # nestor+http://host:port/?code=K7QF2M9X: the core and the code
PAIRING_CODE = "NESTOR_PAIRING_CODE"  # the code alone, the core's address given separately
RETRY_S = 5.0  # how long to wait when the core is busy and does not say


class TokenStore:
    """One device's token for one core, in a JSON file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path).expanduser()

    def load(self, core: str, device_id: str) -> str | None:
        try:
            saved = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if saved.get("core") != core or saved.get("device_id") != device_id:
            return None  # a token for another core or device is useless here
        return saved.get("token")

    def save(self, core: str, device_id: str, token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump({"core": core, "device_id": device_id, "token": token}, file)
        os.chmod(self.path, 0o600)  # an older file may have been more open

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


class Display(ABC):
    """A screen the device code can be read on (`device_code.display`). Calls return at once."""

    @abstractmethod
    def show(self, text: str, code: str | None = None) -> None:
        """Show a pairing message, with the device code to read when there is one (replaces the last)."""

    @abstractmethod
    def clear(self) -> None:
        """Pairing is over: stop showing it."""


type Voice = Callable[[str], None]  # says a sentence in the device's language (`device_code.speech`); returns at once


class PairingFailed(Exception):
    """Pairing cannot succeed until a person changes something (update the device or the core)."""


class EnrollmentOutcome(StrEnum):
    ACCEPTED = "accepted"  # the core paired the device with this code
    REFUSED = "refused"  # the code pairs nothing (wrong, expired, used, cancelled), or was refused before
    NOT_PENDING = "not_pending"  # the device was paired otherwise (its device code), the code is not spent
    INVALID = "invalid"  # not an enrollment code


@dataclass(slots=True)
class _Submission:
    code: str
    method: PairingMethod
    outcome: asyncio.Future[EnrollmentOutcome] | None = None  # None: the configured code, nobody waits

    def settle(self, outcome: EnrollmentOutcome) -> None:
        if self.outcome is not None and not self.outcome.done():
            self.outcome.set_result(outcome)

    def stop(self, error: BaseException) -> None:
        """The pairing stopped before the core answered: cancelled, or failed for good."""
        if self.outcome is not None and not self.outcome.done():
            if isinstance(error, asyncio.CancelledError):
                self.outcome.cancel()
            else:
                self.outcome.set_exception(error)


@dataclass(frozen=True, slots=True)
class _Error:
    code: str | None
    detail: str
    reason: str | None
    retry_after_s: float | None


class _Presenter:
    """Every output the device has, and always the log."""

    def __init__(self, messages: Messages, display: Display | None, voice: Voice | None) -> None:
        self._t, self._display, self._voice = messages, display, voice
        self._code: str | None = None

    def code(self, code: str, *, note: str | None = None) -> None:
        self._code = code
        lead = f"{note} " if note else ""
        logger.warning("%s", self._t("device-code-logged", code=code))
        if self._display is None and self._voice is None:
            logger.warning("%s", self._t("device-code-headless"))
        if self._display is not None:
            self._display.show(lead + self._t("device-code-shown"), code)
        if self._voice is not None:
            self._voice(lead + self._t("device-code-spoken", digits=", ".join(code)))

    def again(self, note: str) -> None:
        """The device code being presented, after a note (an enrollment code refused meanwhile)."""
        if self._code is not None:
            self.code(self._code, note=note)

    def done(self) -> None:
        self._code = None
        logger.info("%s", self._t("paired"))
        if self._display is not None:
            self._display.clear()

    def failed(self, text: str) -> PairingFailed:
        logger.error("%s", text)
        if self._display is not None:
            self._display.show(text)
        if self._voice is not None:
            self._voice(text)
        return PairingFailed(text)


class Pairing:
    """Pairs a device with the core, as many times as it is asked to; enrollment codes handed in
    with `enroll` are sent by the pairing in progress, or the next one."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        messages: Messages,
        display: Display | None = None,
        voice: Voice | None = None,
        enrollment: str | None = None,
        on_identify: Callable[[], None] = lambda: None,
        poll_s: float = 1.0,
        retry_s: float = RETRY_S,
    ) -> None:
        """`enrollment`: a canonical enrollment code from the device's configuration."""
        self.manifest = manifest
        self._t = messages
        self._presenter = _Presenter(messages, display, voice)
        self._configured = enrollment
        self._on_identify = on_identify
        self._poll_s, self._retry_s = poll_s, retry_s
        self._refused: dict[str, str] = {}  # code → why the core refused it
        self._inbox: deque[_Submission] = deque()
        self._late: list[_Submission] = []  # answered not_pending: spent only if the pairing really ended
        self._arrived = asyncio.Event()
        self._hold_until = 0.0
        self._note: str | None = None
        self.active = False

    async def enroll(self, code: str, method: PairingMethod) -> EnrollmentOutcome:
        """Have the pairing send this canonical code; its outcome once the core answered."""
        if code in self._refused:  # never presented again (P5)
            logger.warning("%s", self._t("enrollment-refused", reason=self._refused[code]))
            return EnrollmentOutcome.REFUSED
        submission = _Submission(code, method, asyncio.get_running_loop().create_future())
        self._inbox.append(submission)
        self._arrived.set()
        assert submission.outcome is not None
        return await submission.outcome

    async def pair(self, http: httpx.AsyncClient) -> str:
        """Pair until a proof is accepted; the token."""
        self.active = True
        try:
            while True:
                submission = self._next()
                ticket = await self._request(http, submission)
                if ticket is None:
                    continue  # refused (fall back to a device code), or asked to wait: ask again
                token = await self._wait(http, ticket)
                if token is not None:
                    self._presenter.done()
                    self._settle_all(EnrollmentOutcome.NOT_PENDING)
                    return token
        except (asyncio.CancelledError, PairingFailed) as error:
            self._settle_all(error)
            raise
        finally:
            self.active = False

    def _next(self) -> _Submission | None:
        if self._inbox:
            return self._inbox.popleft()
        if self._configured is not None and self._configured not in self._refused:
            return _Submission(self._configured, PairingMethod.PROVISIONING)
        return None

    async def _request(self, http: httpx.AsyncClient, submission: _Submission | None) -> dict[str, Any] | None:
        body: dict[str, Any] = {"manifest": self.manifest.to_json()}
        if submission is not None:
            body |= {"enrollment_code": submission.code, "method": submission.method.value}
        response = await self._post(http, "/satellites/pairings", body, submission)
        if response.status_code == 201:
            ticket = response.json()
            if submission is not None and ticket.get("status") == "approved":
                logger.info("%s", self._t("enrollment-accepted"))
                submission.settle(EnrollmentOutcome.ACCEPTED)
            elif submission is not None:  # not taken with the request: offered again while pending
                self._inbox.appendleft(submission)
            return ticket
        error = _error(response)
        if response.status_code == 429:
            if submission is not None:
                self._inbox.appendleft(submission)  # the same code, once the core lets it
            await asyncio.sleep(self._wait_after(response, error))
            return None
        if error.code == "enrollment_refused" and submission is not None:
            self._refuse(submission, error.reason)
            return None
        raise self._failure(http, response, error, submission)

    async def _wait(self, http: httpx.AsyncClient, ticket: dict[str, Any]) -> str | None:
        """Poll a pairing until it ends: the token, or `None` to ask again."""
        if code := ticket.get("device_code"):
            self._presenter.code(code, note=self._note)
            self._note = None
        pairing_id = ticket["pairing_id"]
        while True:
            state = (await http.get(f"/satellites/pairings/{pairing_id}")).raise_for_status().json()
            if state.get("identify"):
                self._on_identify()
            status = state.get("status")
            if status == "approved" and state.get("token"):
                return state["token"]
            if status != "pending":  # expired (or claimed without us): ask again
                logger.warning("%s", self._t("device-code-expired"))
                self._inbox.extendleft(reversed(self._late))  # their codes were not spent
                self._late.clear()
                return None
            await self._between_polls(http, pairing_id)

    async def _between_polls(self, http: httpx.AsyncClient, pairing_id: str) -> None:
        """Wait for the next poll, sending the enrollment codes handed in meanwhile."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._poll_s
        while (now := loop.time()) < deadline:
            if self._inbox and now >= self._hold_until:
                if await self._send(http, pairing_id, self._inbox.popleft()):
                    return  # approved: poll at once for the token
                continue
            wake = min(deadline, self._hold_until) if self._inbox and self._hold_until > now else deadline
            self._arrived.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._arrived.wait(), wake - now)

    async def _send(self, http: httpx.AsyncClient, pairing_id: str, submission: _Submission) -> bool:
        body = {"enrollment_code": submission.code, "method": submission.method.value}
        response = await self._post(http, f"/satellites/pairings/{pairing_id}/enrollment", body, submission)
        if response.status_code == 200:
            logger.info("%s", self._t("enrollment-accepted"))
            submission.settle(EnrollmentOutcome.ACCEPTED)
            return True
        error = _error(response)
        if response.status_code == 429:
            self._hold_until = asyncio.get_running_loop().time() + self._wait_after(response, error)
            self._inbox.appendleft(submission)
            return False
        match error.code:
            case "not_pending":
                logger.info("%s", self._t("enrollment-not-pending"))
                self._late.append(submission)
            case "enrollment_refused":
                self._refuse(submission, error.reason)
                self._presenter.again(self._note or "")
                self._note = None
            case _:
                raise self._failure(http, response, error, submission)
        return False

    async def _post(
        self, http: httpx.AsyncClient, path: str, body: dict[str, Any], submission: _Submission | None
    ) -> httpx.Response:
        try:
            return await http.post(path, json=body)
        except asyncio.CancelledError as error:
            if submission is not None:
                submission.stop(error)
            raise
        except Exception:
            if submission is not None:
                self._inbox.appendleft(submission)  # sent again once the core is back
            raise

    def _refuse(self, submission: _Submission, reason: str | None) -> None:
        self._refused[submission.code] = reason = reason or "unknown"
        self._note = self._t("enrollment-refused", reason=reason)
        logger.warning("%s", self._note)
        submission.settle(EnrollmentOutcome.REFUSED)

    def _wait_after(self, response: httpx.Response, error: _Error) -> float:
        header = response.headers.get("Retry-After", "")
        wait = float(header) if header.isdigit() else error.retry_after_s or self._retry_s
        key = "pairing-rate-limited" if error.code == "too_many_attempts" else "pairing-busy"
        logger.warning("%s", self._t(key, seconds=round(wait)))
        return wait

    def _failure(
        self, http: httpx.AsyncClient, response: httpx.Response, error: _Error, submission: _Submission | None
    ) -> Exception:
        """A refusal that waiting cannot fix fails for good; anything else is the core being unwell (retried)."""
        if error.code == "protocol_unsupported":
            failure: Exception = self._presenter.failed(self._t("protocol-unsupported"))
        elif error.code == "invalid_request":
            failure = self._presenter.failed(self._t("pairing-rejected", detail=error.detail))
        elif response.status_code == 404:
            failure = self._presenter.failed(self._t("pairing-not-found", core=str(http.base_url).rstrip("/")))
        else:
            failure = httpx.HTTPStatusError(f"pairing answered {response.status_code}", request=response.request,
                                            response=response)  # fmt: skip
        if submission is not None:
            if isinstance(failure, PairingFailed):
                submission.stop(failure)
            else:
                self._inbox.appendleft(submission)
        return failure

    def _settle_all(self, ended: EnrollmentOutcome | BaseException) -> None:
        """The pairing ended: those still waiting learn how (paired otherwise, cancelled, failed)."""
        if isinstance(ended, EnrollmentOutcome) and self._inbox:
            logger.info("%s", self._t("enrollment-not-pending"))
        for submission in (*self._inbox, *self._late):
            if isinstance(ended, EnrollmentOutcome):
                submission.settle(ended)
            else:
                submission.stop(ended)
        self._inbox.clear()
        self._late.clear()


def _error(response: httpx.Response) -> _Error:
    try:
        data = response.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _Error(None, response.text[:200], None, None)
    retry = data.get("retry_after_s")
    return _Error(
        data.get("error") if isinstance(data.get("error"), str) else None,
        str(data.get("detail", "")),
        data.get("reason") if isinstance(data.get("reason"), str) else None,
        float(retry) if isinstance(retry, int | float) and not isinstance(retry, bool) else None,
    )
