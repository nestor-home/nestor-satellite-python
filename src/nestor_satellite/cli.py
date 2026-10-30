"""`nestor-satellite run`: this machine's microphone and speaker as a satellite of a Nestor core.

    nestor-satellite run --core http://nestor.local:7860 --room salon
    nestor-satellite run --pairing-uri "nestor+http://nestor.local:7860/?code=K7QF2M9X" --room salon

Useful on a Raspberry Pi with a USB speakerphone. It has no screen and no voice of its own: give it
the pairing URI of an enrollment code made in Nestor (flag, or `NESTOR_PAIRING_URI`), or type the
pairing code it logs in Nestor. The token is then kept in `~/.config/nestor-satellite/<id>.json`.
"""

import argparse
import asyncio
import contextlib
import logging
import re
import socket
import sys
from collections.abc import Sequence

from nestor_satellite.client import Satellite
from nestor_satellite.codes import InvalidEnrollment
from nestor_satellite.pairing import PairingFailed, TokenStore
from nestor_satellite.protocol import AUDIO_CAPTURE, AUDIO_PLAYBACK, Device, Manifest


def default_id() -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", socket.gethostname().lower().split(".")[0]).strip("-")
    return (slug or "satellite")[:64]


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nestor-satellite", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a microphone + speaker satellite")
    run.add_argument(
        "--core", help="the core's voice server, e.g. http://nestor.local:7860 (default: the pairing URI's)"
    )
    enrollment = run.add_mutually_exclusive_group()
    enrollment.add_argument("--pairing-uri", help="pair with this enrollment code's URI (default: $NESTOR_PAIRING_URI)")
    enrollment.add_argument("--pairing-code", help="pair with this enrollment code (default: $NESTOR_PAIRING_CODE)")
    run.add_argument("--lang", help="the language of what it says, en or fr (default: the environment's)")
    run.add_argument("--room", required=True, help="the room the satellite is in (a slug: salon, cuisine…)")
    run.add_argument("--id", default=default_id(), help="this device's id (default: the host name)")
    run.add_argument("--model", default="python-satellite")
    run.add_argument("--token-file", help="where the token is kept (default: ~/.config/nestor-satellite/<id>.json)")
    run.add_argument("--input-device", help="PortAudio input device (name or index)")
    run.add_argument("--output-device", help="PortAudio output device (name or index)")
    run.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def build(args: argparse.Namespace) -> Satellite:
    from nestor_satellite.devices import Microphone, Speaker

    manifest = Manifest.of(Device(args.id, args.model, args.room), AUDIO_CAPTURE, AUDIO_PLAYBACK)
    tokens = TokenStore(args.token_file or f"~/.config/nestor-satellite/{args.id}.json")
    return Satellite(
        args.core,
        manifest,
        tokens=tokens,
        enrollment=args.pairing_uri or args.pairing_code,
        locale=args.lang,
        source=Microphone(device=_device(args.input_device)),
        sink=Speaker(device=_device(args.output_device)),
    )


def _device(value: str | None) -> int | str | None:
    return int(value) if value is not None and value.isdigit() else value


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        satellite = build(args)
    except (InvalidEnrollment, ValueError) as error:
        sys.exit(f"nestor-satellite: {error}")
    with contextlib.suppress(KeyboardInterrupt):
        try:
            asyncio.run(satellite.run())
        except PairingFailed:
            sys.exit(1)  # already said why, in the log
