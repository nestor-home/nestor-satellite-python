import json
from typing import Any

from nestor_satellite import Behavior, Body, Expression, wire
from nestor_satellite.dispatch import Dispatcher


class Recorder(Body):
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def behavior(self, behavior: Behavior) -> None:
        self.calls.append(("behavior", behavior))

    def expression(self, expression: Expression, intensity: float) -> None:
        self.calls.append(("expression", (expression, intensity)))

    def beat(self) -> None:
        self.calls.append(("beat", None))

    def identify(self) -> None:
        self.calls.append(("identify", None))

    def heartbeat(self) -> None:
        self.calls.append(("heartbeat", None))

    def connected(self, up: bool) -> None:
        self.calls.append(("connected", up))


def raw(kind: str, payload: dict[str, Any], id: str = "m-1") -> str:
    return json.dumps({"type": kind, "id": id, "at": "2026-10-30T12:00:00+00:00", "payload": payload})


def dispatcher(**options: Any) -> tuple[Dispatcher, Recorder, list[dict[str, Any]]]:
    body, sent = Recorder(), []
    return Dispatcher(body, sent.append, **options), body, sent


def test_intents_reach_the_body_and_are_acknowledged_by_id() -> None:
    inbox, body, sent = dispatcher()
    inbox.handle(raw("intent.behavior", {"behavior": "speaking"}, "a"))
    inbox.handle(raw("intent.expression", {"expression": "joy", "intensity": 0.5}, "b"))
    inbox.handle(raw("intent.beat", {}, "c"))
    inbox.handle(raw("intent.identify", {}, "d"))

    assert [c for c in body.calls if c[0] != "heartbeat"] == [
        ("behavior", Behavior.SPEAKING), ("expression", (Expression.JOY, 0.5)), ("beat", None), ("identify", None)
    ]  # fmt: skip
    assert [(m["type"], m["payload"]["of"]) for m in sent] == [("ack", "a"), ("ack", "b"), ("ack", "c"), ("ack", "d")]


def test_the_welcome_is_handed_back_without_an_ack() -> None:
    inbox, _, sent = dispatcher()
    welcome = inbox.handle(raw("welcome", {"device_id": "tv", "room": "salon", "capabilities": ["audio.capture"]}))
    assert welcome == wire.Welcome("tv", "salon", frozenset({"audio.capture"}))
    assert not sent


def test_every_message_from_the_core_is_a_heartbeat() -> None:
    inbox, body, _ = dispatcher()
    inbox.handle(raw("intent.dance", {}))
    assert body.calls == [("heartbeat", None)], "unknown, so not acknowledged, but the core is alive"


def test_the_assistants_words_reach_the_text_display_in_order_and_are_acknowledged() -> None:
    shown: list[tuple[str, bool]] = []
    inbox, _, sent = dispatcher(text_display=lambda text, final: shown.append((text, final)))
    inbox.handle(raw("intent.text", {"text": "Je regarde…", "final": False}, "a"))
    inbox.handle(raw("intent.text", {"text": "Il fait 19 °C."}, "b"))
    assert shown == [("Je regarde…", False), ("Il fait 19 °C.", True)]
    assert [m["payload"]["of"] for m in sent] == ["a", "b"]


def test_text_for_a_device_without_a_display_is_still_acknowledged() -> None:
    inbox, _, sent = dispatcher()
    inbox.handle(raw("intent.text", {"text": "Bonjour."}, "a"))
    assert [m["payload"]["of"] for m in sent] == ["a"]


def test_off_contract_messages_are_dropped() -> None:
    inbox, body, sent = dispatcher()
    assert inbox.handle("ping") is None
    assert inbox.handle(raw("intent.behavior", {"behavior": "dancing"})) is None
    assert not body.calls and not sent


def test_the_default_body_ignores_everything() -> None:
    inbox = Dispatcher(Body(), lambda _m: None)
    intents = [("intent.behavior", {"behavior": "idle"}), ("intent.expression", {"expression": "sigh"}),
               ("intent.beat", {}), ("intent.identify", {}), ("intent.text", {"text": "Bonjour."})]  # fmt: skip
    for kind, payload in intents:
        assert inbox.handle(raw(kind, payload)) is None
    Body().connected(True)
