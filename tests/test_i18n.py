"""The sentences people read and hear: complete in every language, short, picked from the environment."""

from importlib.resources import files

import pytest
from fluent.syntax import FluentParser, ast
from fluent.syntax.visitor import Visitor

from nestor_satellite.i18n import LANGUAGES, Messages, language


def catalogue(lang: str) -> dict[str, set[str]]:
    """Each message of a catalogue, with the variables it uses."""
    text = files("nestor_satellite").joinpath("locales", lang, "satellite.ftl").read_text("utf-8")
    resource = FluentParser().parse(text)
    assert not [e for e in resource.body if isinstance(e, ast.Junk)], f"{lang}: syntax errors"

    class Variables(Visitor):
        def __init__(self) -> None:
            super().__init__()
            self.found: set[str] = set()

        def visit_VariableReference(self, node: ast.VariableReference) -> None:
            self.found.add(node.id.name)

    messages = {}
    for entry in resource.body:
        if isinstance(entry, ast.Message):
            visitor = Variables()
            visitor.visit(entry)
            messages[entry.id.name] = visitor.found
    return messages


def test_every_language_says_everything_with_the_same_variables() -> None:
    english = catalogue("en")
    for lang in LANGUAGES:
        assert catalogue(lang) == english, lang


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_sentences_use_no_em_dash(lang: str) -> None:
    assert "—" not in files("nestor_satellite").joinpath("locales", lang, "satellite.ftl").read_text("utf-8")


@pytest.mark.parametrize(
    ("asked", "environ", "expected"),
    [
        ("fr", {"LANG": "en_US.UTF-8"}, "fr"),
        (None, {"LANG": "fr_FR.UTF-8"}, "fr"),
        (None, {"LC_ALL": "fr-CA", "LANG": "en_GB"}, "fr"),
        (None, {"LC_MESSAGES": "fr_BE@euro"}, "fr"),
        (None, {"LANG": "de_DE.UTF-8"}, "en"),
        (None, {"LANG": "C"}, "en"),
        (None, {}, "en"),
    ],
    ids=["asked", "LANG", "LC_ALL first", "LC_MESSAGES", "no catalogue", "C locale", "nothing set"],
)
def test_the_language_is_the_one_asked_else_the_environments_else_english(
    asked: str | None, environ: dict[str, str], expected: str
) -> None:
    assert language(asked, environ) == expected


def test_a_refusal_is_said_from_its_reason_in_the_devices_language() -> None:
    fr = Messages("fr")
    assert fr("enrollment-refused", reason="used") == "Ce code d'enrôlement a déjà servi."
    assert fr("enrollment-refused", reason="something-new") == "Ce code d'enrôlement n'est pas valide."
    assert Messages("en")("enrollment-refused", reason="expired") == "This enrollment code has expired."


def test_a_missing_message_or_variable_is_a_bug_not_half_a_sentence() -> None:
    messages = Messages("en")
    with pytest.raises(LookupError):
        messages("no-such-message")
    with pytest.raises(KeyError):
        messages("device-code-logged")
