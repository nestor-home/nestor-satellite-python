"""Enrollment codes and pairing URIs, as a person hands them to a device.

An enrollment code is 8 symbols of Crockford's base32, shown grouped by four (`K7QF-2M9X`). Reading
it back forgives case, dashes, spaces, and the letters Crockford maps to digits (O → 0, I and L → 1).

A pairing URI carries the core's address with the code: `nestor+http://nestor.local:7860/?code=K7QF2M9X`.
Removing `nestor+` and the query gives the core's base URL.
"""

import re
import urllib.parse
from dataclasses import dataclass

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford's base32: no I, L, O, U
LENGTH = 8
_ALIASES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_IGNORED = re.compile(r"[\s-]")
_SCHEMES = {"nestor+http": "http", "nestor+https": "https"}


class InvalidEnrollment(ValueError):
    """Neither an enrollment code nor a pairing URI."""


def normalize_enrollment_code(text: str) -> str | None:
    """The canonical form of a typed or scanned code (`k7qf-2m9x` → `K7QF2M9X`); `None` when it cannot be one."""
    code = _IGNORED.sub("", text).upper().translate(_ALIASES)
    if len(code) != LENGTH or any(c not in ALPHABET for c in code):
        return None
    return code


def parse_pairing_uri(uri: str) -> tuple[str, str]:
    """The core's base URL and the canonical enrollment code a pairing URI carries."""
    parts = urllib.parse.urlsplit(uri.strip())
    scheme = _SCHEMES.get(parts.scheme.lower())
    codes = urllib.parse.parse_qs(parts.query).get("code", [])
    code = normalize_enrollment_code(codes[0]) if len(codes) == 1 else None
    if scheme is None or not parts.netloc or code is None:
        raise InvalidEnrollment("a pairing URI looks like nestor+http://host:port/?code=K7QF2M9X")
    return urllib.parse.urlunsplit((scheme, parts.netloc, parts.path.rstrip("/"), "", "")), code


@dataclass(frozen=True, slots=True)
class Enrollment:
    """An enrollment code, canonical, and the core's address when it came in a pairing URI."""

    code: str
    core: str | None = None

    @classmethod
    def read(cls, text: str) -> "Enrollment":
        """A pairing URI or a bare code, as typed, scanned or configured."""
        if text.strip().lower().startswith("nestor+"):
            core, code = parse_pairing_uri(text)
            return cls(code, core)
        code = normalize_enrollment_code(text)
        if code is None:
            raise InvalidEnrollment("an enrollment code is 8 letters and digits, like K7QF-2M9X")
        return cls(code)
