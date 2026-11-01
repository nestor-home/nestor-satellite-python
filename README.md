# nestor-satellite

Python SDK to write **satellites** for [Nestor](https://nestorhome.dev): devices (a Raspberry Pi
speaker, a robot, a screen) that pair with a Nestor server, stream audio over WebRTC, carry out its
intents and report what they observe, following the `nestor.satellite/1` protocol. It does not
depend on the Nestor server.

**Documentation:** [nestorhome.dev/docs/sdks/python](https://nestorhome.dev/docs/sdks/python/)
(pairing options, the command, conformance).

```bash
pip install 'nestor-satellite[sounddevice]'
NESTOR_PAIRING_URI='nestor+http://nestor.local:7860/?code=K7QF2M9X' nestor-satellite run --room salon
```

A driver of your own:

```python
import asyncio

from nestor_satellite import AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, IDENTIFY, Body, Device, Manifest
from nestor_satellite import Satellite, TokenStore
from nestor_satellite.devices import Microphone, Speaker


class Led(Body):
    def behavior(self, behavior):
        print("attitude:", behavior)  # idle, listening, thinking, speaking

    def expression(self, expression, intensity):
        print("expression:", expression, intensity)

    def identify(self):
        print("blink!")  # a person is telling devices apart


manifest = Manifest.of(
    Device("enceinte-salon", "raspberry-pi-4", "salon"), AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, IDENTIFY
)
satellite = Satellite(
    "http://nestor.local:7860",
    manifest,
    tokens=TokenStore("~/.config/nestor-satellite/enceinte-salon.json"),
    body=Led(),
    source=Microphone(),
    sink=Speaker(),
)
asyncio.run(satellite.run())  # pairs the first time, then stays connected and reconnects
```

The recorded voice that says device codes is shared under CC BY 4.0: see
[`src/nestor_satellite/voice/LICENSE`](src/nestor_satellite/voice/LICENSE).

## Development

The tests run the SDK against the Nestor server's conformance suite: the server (`nestor-home`) is a
dev dependency, pinned and taken from PyPI. To test against a local checkout of the server instead:
`uv run --with-editable ../nestor pytest`.

```bash
uv sync --all-extras
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uv run ty check && uv run pytest
```

## License

MIT
