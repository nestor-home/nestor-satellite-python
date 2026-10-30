# nestor-satellite

Python SDK to write **satellites** for [Nestor](https://github.com/nestor-home/nestor): devices —
a Raspberry Pi speaker, a robot, a screen — that pair with a Nestor core, stream audio over WebRTC,
carry out the core's intents and report what they observe, following the `nestor.satellite/1`
protocol. The SDK does not depend on the Nestor server.

```python
import asyncio
from nestor_satellite import AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, Body, Device, Manifest, Satellite, TokenStore
from nestor_satellite.devices import Microphone, Speaker


class Led(Body):
    def behavior(self, behavior):
        print("attitude:", behavior)

    def expression(self, expression, intensity):
        print("expression:", expression, intensity)


manifest = Manifest.of(Device("enceinte-salon", "raspberry-pi-4", "salon"), AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION)
satellite = Satellite(
    "http://nestor.local:7860",
    manifest,
    tokens=TokenStore("~/.config/salon.json"),
    body=Led(),
    source=Microphone(),
    sink=Speaker(),
)
asyncio.run(satellite.run())  # shows a pairing code the first time, then reconnects forever
```

Or, with nothing to write: `pip install "nestor-satellite[sounddevice]"`, then
`nestor-satellite run --core http://nestor.local:7860 --room salon`.

## Development

The tests run the SDK against the Nestor server's conformance suite, so the `nestor` repository is
expected next to this one (`../nestor`, a dev-only dependency).

```bash
uv sync --all-extras
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uv run ty check && uv run pytest
```

## License

MIT
