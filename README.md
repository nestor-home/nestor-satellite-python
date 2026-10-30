# nestor-satellite

Python SDK to write **satellites** for [Nestor](https://github.com/nestor-home/nestor): devices (a
Raspberry Pi speaker, a robot, a screen) that pair with a Nestor core, stream audio over WebRTC,
carry out the core's intents and report what they observe, following the `nestor.satellite/2`
protocol. The SDK does not depend on the Nestor server.

## Pairing

A device is paired one of two ways, whichever comes first:

- **an enrollment code** made in the Nestor admin is given to the device, usually as a pairing URI
  (`nestor+http://nestor.local:7860/?code=K7QF2M9X`): it is paired at once;
- otherwise the device asks for a **device code** and presents it on every output it has (its
  screen, its voice, and always its log), and a person types it in the admin.

A headless device (no screen, no voice) is paired with an enrollment code:

```bash
pip install "nestor-satellite[sounddevice]"
NESTOR_PAIRING_URI="nestor+http://nestor.local:7860/?code=K7QF2M9X" nestor-satellite run --room salon
# or
nestor-satellite run --room salon --pairing-uri "nestor+http://nestor.local:7860/?code=K7QF2M9X"
# or, the core's address given separately
nestor-satellite run --core http://nestor.local:7860 --room salon --pairing-code K7QF-2M9X
```

Without one, it logs its device code and says that an enrollment code is the way to pair it. The
token is kept in `~/.config/nestor-satellite/<id>.json` (mode 600); later runs reconnect with it.

## Writing a driver

```python
import asyncio
from nestor_satellite import AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, IDENTIFY, Body, Device, Display, Manifest
from nestor_satellite import PairingMethod, Satellite, TokenStore
from nestor_satellite.devices import Microphone, Speaker


class Screen(Display):
    def show(self, text, code=None):
        print(text, code or "")  # draw it on the device's screen

    def clear(self):
        print("paired")


class Led(Body):
    def behavior(self, behavior):
        print("attitude:", behavior)

    def expression(self, expression, intensity):
        print("expression:", expression, intensity)

    def identify(self):
        print("blink!")  # a person is telling devices apart


def say(text):
    print("saying:", text)  # hand it to the device's speech synthesis, and return at once


manifest = Manifest.of(
    Device("enceinte-salon", "raspberry-pi-4", "salon"), AUDIO_CAPTURE, AUDIO_PLAYBACK, EXPRESSION, IDENTIFY
)
satellite = Satellite(
    "http://nestor.local:7860",
    manifest,
    tokens=TokenStore("~/.config/salon.json"),
    body=Led(),
    source=Microphone(),
    sink=Speaker(),
    display=Screen(),  # shows the device code: declares device_code.display
    voice=say,  # says it, digit by digit, twice: declares device_code.speech
    enrolls_by=[PairingMethod.INPUT],  # a code can be typed on it: see `enroll` below
    locale="fr",  # what it says to people, in English or French (default: the environment's language)
)
asyncio.run(satellite.run())  # pairs the first time, then stays connected and reconnects
```

- **Pairing methods** are not declared by hand: the manifest lists `enrollment.provisioning` always,
  and `device_code.display`, `device_code.speech`, `enrollment.input`, `enrollment.qr` from the
  `display`, `voice` and `enrolls_by` given.
- **Provisioning**: `enrollment=` takes a pairing URI or an enrollment code; by default it is read
  from `NESTOR_PAIRING_URI`, else `NESTOR_PAIRING_CODE`. With a pairing URI, the core's address may
  be `None`.
- **A code typed or scanned on the device** while it waits:
  `await satellite.enroll("K7QF-2M9X")` or `await satellite.enroll(uri, method=PairingMethod.QR)`
  returns `ACCEPTED`, `REFUSED` (never sent again), `NOT_PENDING` (the device code won) or `INVALID`.
- **Identify**: declare `IDENTIFY` and override `Body.identify`; it is called when a person asks a
  pending or paired device to make itself noticed.
- **Failures** that waiting cannot fix (a core speaking another protocol version, a request it
  rejects) stop `run` with `PairingFailed`, said on every output; a core that is down is retried.

Every sentence for people comes from the Fluent catalogues in `src/nestor_satellite/locales/`.

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
