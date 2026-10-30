"""What the SDK says to people (on a screen, aloud, in the log), from the Fluent catalogues in `locales/`.

The language is the one asked for, else the environment's (`LC_ALL`, `LC_MESSAGES`, `LANG`), else
English. Error codes from the core are translated here; their English `detail` is never shown.
"""

import os
from collections.abc import Mapping
from importlib.resources import files
from typing import Any

from fluent.runtime import FluentBundle, FluentResource

LANGUAGES = ("en", "fr")
DEFAULT = "en"


def language(locale: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    """`fr_FR.UTF-8`, `fr-FR` or `fr` → `fr`; a language without a catalogue → English."""
    if not locale:
        env = os.environ if environ is None else environ
        locale = next((env[k] for k in ("LC_ALL", "LC_MESSAGES", "LANG") if env.get(k)), DEFAULT)
    code = locale.split(".")[0].split("@")[0].replace("-", "_").split("_")[0].lower()
    return code if code in LANGUAGES else DEFAULT


class Messages:
    def __init__(self, locale: str | None = None, environ: Mapping[str, str] | None = None) -> None:
        self.language = language(locale, environ)
        self._bundle = FluentBundle([self.language], use_isolating=False)
        source = files("nestor_satellite").joinpath("locales", self.language, "satellite.ftl").read_text("utf-8")
        self._bundle.add_resource(FluentResource(source))

    def __call__(self, message_id: str, **args: Any) -> str:
        """The message, formatted; a missing message or argument is a bug, not a sentence to half-say."""
        pattern = self._bundle.get_message(message_id).value
        assert pattern is not None, f"{message_id} has no text"  # every catalogue message has one (tested)
        text, errors = self._bundle.format_pattern(pattern, args)
        if errors:
            raise KeyError(f"{message_id}: {errors}")
        return str(text)
