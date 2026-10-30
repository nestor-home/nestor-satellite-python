"""`nestor-satellite run`: this machine's microphone and speaker as a satellite of a Nestor core.

    nestor-satellite run --core http://nestor.local:7860 --room salon

Useful on a Raspberry Pi with a USB speakerphone. The first run shows a pairing code to confirm
in Nestor; the token is then kept in `~/.config/nestor-satellite/<id>.json`.
"""

import argparse
import asyncio
import contextlib
import logging
import re
import socket
from collections.abc import Sequence

from nestor_satellite.client import Satellite
from nestor_satellite.pairing import TokenStore
from nestor_satellite.protocol import AUDIO_CAPTURE, AUDIO_PLAYBACK, Device, Manifest


def default_id() -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", socket.gethostname().lower().split(".")[0]).strip("-")
    return (slug or "satellite")[:64]


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nestor-satellite", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a microphone + speaker satellite")
    run.add_argument("--core", required=True, help="the core's voice server, e.g. http://nestor.local:7860")
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
        source=Microphone(device=_device(args.input_device)),
        sink=Speaker(device=_device(args.output_device)),
    )


def _device(value: str | None) -> int | str | None:
    return int(value) if value is not None and value.isdigit() else value


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    satellite = build(args)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(satellite.run())
