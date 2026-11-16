# nestor-satellite

Python SDK to write **satellites** for [Nestor](https://nestorhome.dev): devices (a Raspberry Pi
speaker, a robot, a screen with a keyboard) that pair with a Nestor server, hold one WebSocket link
to it, talk with the assistant in writing, aloud or both, carry out its intents and report what they
observe, following the `nestor.satellite/1` protocol. A device with a camera can be watched, and one
with a microphone and a speaker called, in sessions a person opens in Nestor. It does not depend on
the Nestor server; audio (WebRTC) is the `voice` extra and the camera the `video` extra: a device in
writing needs neither.

**Documentation:** [nestorhome.dev/docs/sdks/python](https://nestorhome.dev/docs/sdks/python/)
(pairing options, the command, conformance).

```bash
pip install 'nestor-satellite[voice,sounddevice]'
NESTOR_PAIRING_URI='nestor+http://nestor.local:8471/?code=K7QF2M9X' nestor-satellite run --room salon

pip install nestor-satellite      # in writing only: type on stdin, read the answers
nestor-satellite run --core http://nestor.local:8471 --room bureau --text --no-voice
```

A driver of your own:

```python
import asyncio

from nestor_satellite import EXPRESSION, IDENTIFY, Body, Device, Manifest, Satellite, TokenStore
from nestor_satellite.devices import Microphone, Speaker


class Led(Body):
    def behavior(self, behavior):
        print("attitude:", behavior)  # idle, listening, thinking, speaking

    def expression(self, expression, intensity):
        print("expression:", expression, intensity)

    def identify(self):
        print("blink!")  # a person is telling devices apart


satellite = Satellite(
    "http://nestor.local:8471",
    Manifest.of(Device("enceinte-salon", "raspberry-pi-4", "salon"), EXPRESSION, IDENTIFY),
    tokens=TokenStore("~/.config/nestor-satellite/enceinte-salon.json"),
    body=Led(),
    source=Microphone(),  # audio.capture, with the voice extra
    sink=Speaker(),  # audio.playback
    text_display=lambda text, final: print(text),  # text.display
    text_input=True,  # text.input: satellite.send_text("…")
)
asyncio.run(satellite.run())  # pairs the first time, then stays connected and reconnects
```

The text and audio capabilities follow from what the satellite is given; the manifest declares the
others (body, sensors).

A camera (`pip install 'nestor-satellite[video]'`) is filmed only in a session a person opens in
Nestor, and a microphone with a speaker also takes calls. The SDK keeps the rules itself: it connects
only when its link asks, for what it was allowed, ends the session when told, at its time limit or
when the link drops, steps the assistant's voice aside during a call, and never listens on a port.

```python
from nestor_satellite import PiCamera  # or OpenCvCamera(0), AvCamera(...), PatternCamera() to try

satellite = Satellite(
    "https://nestor.example.org",
    manifest,
    tokens=tokens,
    camera=PiCamera(size=(1280, 720)),  # video.capture, and QR codes while pairing (`qr` extra)
    source=Microphone(),  # a call needs both
    sink=Speaker(),
    on_media=led.set,  # media.indicator: on while something is filmed or heard
)
```

Nestor lets a device film only over TLS (`https://`), or on the core's machine. See
[Media sessions](https://nestorhome.dev/docs/satellites/protocol/) in the protocol.

Updates from Nestor: run the command with `--managed` (under a service manager), or give a driver
`updater=PypiUpdater("my-satellite", module="my_satellite")` and name its package in the manifest
(`software=Software.of("my-satellite")`). The device finds newer versions on PyPI itself, installs
one only when a person asks for it in Nestor, starts again on it, and goes back to the previous one
if it is not welcomed within a minute. See
[Updates](https://nestorhome.dev/docs/satellites/protocol/) in the protocol.

The recorded voice that says device codes is shared under CC BY 4.0: see
[`src/nestor_satellite/voice/LICENSE`](src/nestor_satellite/voice/LICENSE).

## Development

The tests run the SDK against the Nestor server's conformance suite: the server (`nestor-home`) is a
dev dependency, pinned and taken from PyPI. To test against a local checkout of the server instead
(the project's environment wins over `uv run --with`): `uv pip install -e '../nestor[voice]'`, then
`uv run --no-sync pytest`; `uv sync` puts the pinned server back.

```bash
uv sync --all-extras
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uv run ty check && uv run pytest
```

## License

MIT
