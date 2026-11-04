"""`nestor-satellite run`: this machine as a satellite of a Nestor core, by voice, in writing or both.

    nestor-satellite run --core http://nestor.local:8471 --room salon
    nestor-satellite run --pairing-uri "nestor+http://nestor.local:8471/?code=K7QF2M9X" --room salon
    nestor-satellite run --core http://nestor.local:8471 --room bureau --text --no-voice

By default its microphone and speaker (`pip install 'nestor-satellite[voice,sounddevice]'`): useful on
a Raspberry Pi with a USB speakerphone. With `--text`, each line typed on its standard input is sent
to Nestor and the answers are printed; `--no-voice` leaves the microphone and speaker out. Give it
the pairing URI of an enrollment code made in Nestor (flag, or `NESTOR_PAIRING_URI`), or type in
Nestor the pairing code it says on its speaker (and logs); with `--camera`, show it the QR code of a
pairing URI. The token is then kept in `~/.config/nestor-satellite/<id>.json`. With `--managed`, a
person can update it from Nestor: it installs the newer version from PyPI into the environment it
runs from, then starts again with the same command (run it under a service manager all the same).
"""

import argparse
import asyncio
import contextlib
import logging
import re
import socket
import sys
import threading
from collections.abc import Sequence
from typing import Any

from nestor_satellite.client import CapabilityNotAllowed, Satellite
from nestor_satellite.codes import InvalidEnrollment
from nestor_satellite.pairing import PairingFailed, TokenStore
from nestor_satellite.protocol import Device, Manifest, ProtocolError
from nestor_satellite.wire import TYPED_MAX

logger = logging.getLogger("nestor_satellite")


def default_id() -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", socket.gethostname().lower().split(".")[0]).strip("-")
    return (slug or "satellite")[:64]


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nestor-satellite", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a satellite: microphone and speaker, typed text, or both")
    run.add_argument(
        "--core", help="the core's voice server, e.g. http://nestor.local:8471 (default: the pairing URI's)"
    )
    enrollment = run.add_mutually_exclusive_group()
    enrollment.add_argument("--pairing-uri", help="pair with this enrollment code's URI (default: $NESTOR_PAIRING_URI)")
    enrollment.add_argument("--pairing-code", help="pair with this enrollment code (default: $NESTOR_PAIRING_CODE)")
    run.add_argument("--lang", help="the language of what it says, en or fr (default: the environment's)")
    run.add_argument("--room", required=True, help="the room the satellite is in (a slug: salon, cuisine…)")
    run.add_argument("--id", default=default_id(), help="this device's id (default: the host name)")
    run.add_argument("--model", default="python-satellite")
    run.add_argument("--token-file", help="where the token is kept (default: ~/.config/nestor-satellite/<id>.json)")
    run.add_argument("--text", action="store_true", help="send the lines typed on stdin, print the answers")
    run.add_argument(
        "--voice", action=argparse.BooleanOptionalAction, default=True, help="use the microphone and speaker"
    )
    run.add_argument("--input-device", help="PortAudio input device (name or index)")
    run.add_argument("--output-device", help="PortAudio output device (name or index)")
    run.add_argument(
        "--camera",
        help="read QR codes of pairing URIs from this camera (FFmpeg device: 0 on macOS, /dev/video0 on Linux)",
    )
    run.add_argument(
        "--managed", action="store_true", help="a person can update it from Nestor (from PyPI, into this environment)"
    )
    run.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def build(args: argparse.Namespace) -> Satellite:
    options: dict[str, Any] = {}
    if args.voice:
        from nestor_satellite.devices import Microphone, Speaker

        options |= {"source": Microphone(device=_device(args.input_device)),
                    "sink": Speaker(device=_device(args.output_device))}  # fmt: skip
    if args.text:
        options |= {"text_input": True, "text_display": _print}
    if args.managed:
        from nestor_satellite.pypi import PypiUpdater

        extras = "[voice,sounddevice]" if args.voice else ""
        options["updater"] = PypiUpdater(f"nestor-satellite{extras}", module="nestor_satellite")
    tokens = TokenStore(args.token_file or f"~/.config/nestor-satellite/{args.id}.json")
    return Satellite(
        args.core,
        Manifest(Device(args.id, args.model, args.room)),
        tokens=tokens,
        enrollment=args.pairing_uri or args.pairing_code,
        locale=args.lang,
        camera=_camera(args.camera),
        **options,
    )


def _print(text: str, final: bool) -> None:
    print(text, flush=True)


async def serve(satellite: Satellite, *, typing: bool) -> None:
    """Run the satellite; with `typing`, send each line of stdin as typed text."""
    if not typing:
        return await satellite.run()
    reading = asyncio.create_task(_type(satellite))
    try:
        await satellite.run()
    finally:
        reading.cancel()


async def _type(satellite: Satellite) -> None:
    """Lines of stdin, read in a daemon thread (a blocked read never holds the process), sent as they come."""
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def read() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(lines.put_nowait, line)
        loop.call_soon_threadsafe(lines.put_nowait, None)

    threading.Thread(target=read, name="stdin", daemon=True).start()
    while (line := await lines.get()) is not None:
        if text := line.strip():
            try:
                sent = satellite.send_text(text)
            except CapabilityNotAllowed:
                logger.warning("%s", satellite.messages("text-not-allowed"))
            except ProtocolError:
                logger.warning("%s", satellite.messages("text-too-long", max=TYPED_MAX))
            else:
                if not sent:
                    logger.warning("%s", satellite.messages("text-not-sent"))


def _camera(device: str | None) -> Any:
    if device is None:
        return None
    from nestor_satellite.camera import AvCamera

    if sys.platform == "darwin":  # AVFoundation delivers what the camera offers, at its own rate
        return AvCamera(device, format="avfoundation", options={"framerate": "30", "video_size": "1280x720"})
    return AvCamera(device, format="v4l2")


def _device(value: str | None) -> int | str | None:
    return int(value) if value is not None and value.isdigit() else value


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        satellite = build(args)
    except (InvalidEnrollment, ValueError, RuntimeError) as error:  # RuntimeError: a missing extra
        sys.exit(f"nestor-satellite: {error}")
    with contextlib.suppress(KeyboardInterrupt):
        try:
            asyncio.run(serve(satellite, typing=args.text))
        except PairingFailed:
            sys.exit(1)  # already said why, in the log
