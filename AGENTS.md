# AGENTS.md

`nestor-satellite` is the Python SDK for Nestor satellite drivers. This file is for coding agents:
read it before changing anything.

## Commands

```bash
uv sync --all-extras         # dev group included; needs the nestor repo at ../nestor
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uv run ty check && uv run pytest
```

All four must pass; CI runs them on Python 3.12 to 3.14. `pytest` measures branch coverage and fails
under 95 %. Manage dependencies with `uv add` / `uv remove` only, and commit `uv.lock`.

## Layout

```text
src/nestor_satellite/
├── protocol.py   values of nestor.satellite/1: Device, Capability, Manifest, Behavior, Expression
├── wire.py       the JSON envelope: messages the satellite sends, parsing what the core sends
├── schemas/      JSON Schemas copied from the server (do not edit by hand)
├── pairing.py    pair with a code, TokenStore (file mode 600)
├── client.py     Satellite: WebRTC connection, hello/welcome, acks, observations, reconnection
├── body.py       Body: what a driver overrides to carry out intents
├── audio.py      AudioSource / AudioSink ports, doubles, WebRTC track adapters
├── devices.py    Microphone / Speaker through PortAudio (optional `sounddevice` extra)
└── cli.py        nestor-satellite run
tests/            behaviour tests, including the server's black-box conformance suite
```

## Rules

1. **Never import `nestor`** from `src/`: drivers stay independent of the server. The server is a
   dev dependency, used only by tests (its conformance suite and its schemas).
2. **The server owns the protocol.** `src/nestor_satellite/schemas/` is a copy of
   `nestor/src/nestor/modules/satellites/schemas/`; `tests/test_schemas.py` fails when they drift.
   To refresh: `cp ../nestor/src/nestor/modules/satellites/schemas/*.json src/nestor_satellite/schemas/`,
   then adapt `wire.py`/`protocol.py` until the tests pass. Every message the SDK sends is validated
   against these schemas in the tests.
3. **Be lenient in what you read, strict in what you send.** An unknown message type from a newer
   core is ignored; a malformed known one is logged and dropped; what the SDK sends is checked first.
4. **The network loop never waits for the device.** `Body` methods return at once; a driver animates
   in its own task. Physical safety (limits, watchdogs) lives in the driver and never depends on
   the link being up.
5. **Secrets:** the token is written with mode 600 and forgotten when the core refuses it.

## Code conventions

- Python 3.12+ syntax, fully typed and checked with `ty`; `ruff`, line length 120. English only.
- Values are frozen dataclasses; closed sets are `StrEnum`s. Small classes, one concept per module.
- Async: explicit timeouts, cancellation honoured, resources released in `close`.
- Keep the repository lean: no new Markdown files.

## Tests

- Test behaviour, not implementation: one behaviour per test, named as a sentence.
- Hardware is replaced by doubles (`SilenceSource`, `RecordingSink`, a fake `sounddevice` module).
- Uncovered code is either tested for a real behaviour or deleted — never excluded.
