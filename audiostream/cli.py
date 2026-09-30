"""Command line for the sender and receiver."""

from __future__ import annotations

import argparse
import sys

from audiostream import __version__
from audiostream.audio import AudioError
from audiostream.net import DEFAULT_DISCOVERY_PORT, DEFAULT_PORT, NetworkError


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _validate(args)
        if args.cmd == "sender":
            from audiostream.sender import run_sender

            run_sender(args)
        else:
            from audiostream.receiver import run_receiver

            run_receiver(args)
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        return 130
    except (AudioError, NetworkError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiostream",
        description=(
            "Stream all system audio from this PC to another PC on the LAN, "
            "and play it on a selected output (including a paired Bluetooth device)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"audiostream {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sender = sub.add_parser("sender", help="Capture the system mix and send it over UDP")
    sender.add_argument("--host", help="Receiver LAN IP. Required unless --discover is set.")
    sender.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Receiver UDP port (default {DEFAULT_PORT})")
    sender.add_argument(
        "--discover",
        action="store_true",
        help="Find a receiver by its beacon instead of typing an IP",
    )
    sender.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    sender.add_argument("--discover-timeout", type=float, default=3.0, help="Seconds to wait for a beacon")
    sender.add_argument("--sample-rate", type=int, default=48000)
    sender.add_argument("--channels", type=int, default=2)
    sender.add_argument(
        "--chunk-ms",
        type=int,
        default=5,
        help="Capture chunk length. Packets are capped to stay under a typical LAN MTU.",
    )
    sender.add_argument(
        "--device",
        help="Loopback device number (1 is first) or name. Default: monitor of the default output.",
    )
    sender.add_argument("--list-devices", action="store_true", help="List loopback/monitor devices and exit")

    receiver = sub.add_parser("receiver", help="Play a stream on a selected output device")
    receiver.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port to listen on (default {DEFAULT_PORT})")
    receiver.add_argument("--bind", default="0.0.0.0", help="Local address to bind (default 0.0.0.0)")
    receiver.add_argument(
        "--buffer-ms",
        type=int,
        default=120,
        help="Jitter buffer in milliseconds (default 120, useful range 50–200)",
    )
    receiver.add_argument(
        "--device",
        help="Output device number (1 is first) or name. Prompts when omitted.",
    )
    receiver.add_argument("--list-devices", action="store_true", help="List output devices and exit")
    receiver.add_argument(
        "--test-tone",
        action="store_true",
        help="Play a short tone on the selected device, then listen for the stream",
    )
    receiver.add_argument("--tone-seconds", type=float, default=2.0)
    receiver.add_argument("--sample-rate", type=int, default=48000, help="Sample rate used for the test tone")
    receiver.add_argument("--channels", type=int, default=2, help="Channels used for the test tone")
    receiver.add_argument("--no-discover", action="store_true", help="Do not broadcast a discovery beacon")
    receiver.add_argument("--discovery-port", type=int, default=DEFAULT_DISCOVERY_PORT)
    return parser


def _validate(args) -> None:
    if args.list_devices:
        return
    if args.cmd == "sender":
        if args.sample_rate < 8000 or args.sample_rate > 192000:
            raise NetworkError("--sample-rate must be between 8000 and 192000")
        if args.channels < 1 or args.channels > 8:
            raise NetworkError("--channels must be between 1 and 8")
        if args.chunk_ms < 1 or args.chunk_ms > 500:
            raise NetworkError("--chunk-ms must be between 1 and 500")
        if args.discover_timeout <= 0:
            raise NetworkError("--discover-timeout must be positive")
        return
    if args.sample_rate < 8000 or args.sample_rate > 192000:
        raise NetworkError("--sample-rate must be between 8000 and 192000")
    if args.channels < 1 or args.channels > 8:
        raise NetworkError("--channels must be between 1 and 8")
    if args.tone_seconds <= 0 or args.tone_seconds > 30:
        raise NetworkError("--tone-seconds must be between 0 and 30")
    if args.buffer_ms < 1 or args.buffer_ms > 5000:
        raise NetworkError("--buffer-ms must be between 1 and 5000")
