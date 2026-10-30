import pytest
from nestor.modules.satellites import codes as server

from nestor_satellite import Enrollment, InvalidEnrollment, normalize_enrollment_code, parse_pairing_uri


@pytest.mark.parametrize(
    ("typed", "canonical"),
    [("K7QF2M9X", "K7QF2M9X"), ("k7qf-2m9x", "K7QF2M9X"), (" K7QF 2M9X ", "K7QF2M9X"), ("O1IL-oiLl", "01110111")],
    ids=["canonical", "grouped, lower case", "spaces", "letters read as digits"],
)
def test_an_enrollment_code_is_read_back_as_the_core_reads_it(typed: str, canonical: str) -> None:
    assert normalize_enrollment_code(typed) == canonical == server.normalize_enrollment_code(typed)


@pytest.mark.parametrize("typed", ["K7QF2M9", "K7QF2M9XY", "K7QF2M9U", "K7QF_2M9X", ""])
def test_what_cannot_be_an_enrollment_code_is_none(typed: str) -> None:
    assert normalize_enrollment_code(typed) is None is server.normalize_enrollment_code(typed)


@pytest.mark.parametrize(
    "core", ["http://nestor.local:7860", "https://nestor.example.org/satellites-proxy", "http://192.168.1.4:7860"]
)
def test_a_pairing_uri_made_by_the_core_gives_back_its_address_and_code(core: str) -> None:
    assert parse_pairing_uri(server.pairing_uri(core, "K7QF2M9X")) == (core, "K7QF2M9X")


def test_a_pairing_uri_forgives_how_its_code_was_typed() -> None:
    assert parse_pairing_uri("NESTOR+HTTP://nestor.local:7860/?code=k7qf-2m9x") == (
        "http://nestor.local:7860",
        "K7QF2M9X",
    )


@pytest.mark.parametrize(
    "uri",
    [
        "http://nestor.local:7860/?code=K7QF2M9X",
        "nestor+ftp://nestor.local/?code=K7QF2M9X",
        "nestor+http:///?code=K7QF2M9X",
        "nestor+http://nestor.local/",
        "nestor+http://nestor.local/?code=K7QF2M9X&code=K7QF2M9Y",
        "nestor+http://nestor.local/?code=nope",
    ],
    ids=["no prefix", "not http", "no host", "no code", "two codes", "not a code"],
)
def test_anything_else_is_not_a_pairing_uri(uri: str) -> None:
    with pytest.raises(InvalidEnrollment):
        parse_pairing_uri(uri)


def test_an_enrollment_is_read_from_a_uri_or_a_bare_code() -> None:
    assert Enrollment.read("nestor+https://n.example/?code=K7QF2M9X") == Enrollment("K7QF2M9X", "https://n.example")
    assert Enrollment.read("k7qf-2m9x") == Enrollment("K7QF2M9X")
    with pytest.raises(InvalidEnrollment):
        Enrollment.read("hello")
