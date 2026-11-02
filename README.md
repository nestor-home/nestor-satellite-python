# nestor-satellite

Python SDK to write **satellites** for [Nestor](https://nestorhome.dev): devices (a Raspberry Pi
speaker, a robot, a screen with a keyboard) that pair with a Nestor server, hold one WebSocket link
to it, talk with the assistant in writing, aloud or both, carry out its intents and report what they
observe, following the `nestor.satellite/1` protocol. It does not depend on the Nestor server, and
audio (WebRTC) is the `voice` extra: a device in writing needs none of it.

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
