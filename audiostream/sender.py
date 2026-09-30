"""Capture the system mix and send it as UDP packets."""

from __future__ import annotations

import sys
import time

from audiostream.audio import (
    MACOS_LIMITATION,
    AudioError,
    fit_channels,
    float_to_s16le,
    import_numpy,
    format_device_list,
    list_input_devices,
    list_loopback_devices,
    open_loopback,
)
from audiostream.net import (
    NetworkError,
    check_port,
    discover_receivers,
    resolve_ipv4,
    sender_socket,
)
from audiostream.packet import encode_packet, frames_per_packet


def run_sender(args) -> None:
    if args.list_devices:
        _print_capture_devices()
        return

    host, port = _destination(args)
    mic = open_loopback(args.device)
    frames = frames_per_packet(args.sample_rate, args.chunk_ms, args.channels)
    requested = max(1, args.sample_rate * args.chunk_ms // 1000)
    name = getattr(mic, "name", "loopback")
    print(f"Capturing: {name}", flush=True)
    print(
        f"Sending {args.sample_rate} Hz, {args.channels} ch, "
        f"{frames} frames/packet ({frames * args.channels * 2} bytes) to {host}:{port}",
        flush=True,
    )
    if frames < requested:
        print(
            "note: packet size was capped so each datagram stays under a typical LAN MTU. "
            f"Requested {requested} frames, sending {frames}.",
            flush=True,
        )
    print(
        f"If the receiver stays silent, confirm {host} and allow UDP {port} through its firewall.",
        flush=True,
    )
    print("Ctrl+C to stop.", flush=True)

    sock = sender_socket()
    sequence = 0
    sent = 0
    peak = 0.0
    quiet = 0
    last = time.monotonic()
    recorder_cm = None
    try:
        try:
            recorder_cm, recorder = _open_capture(mic, args.sample_rate, args.channels, frames)
            np = import_numpy()
            while True:
                data = np.asarray(recorder.record(frames), dtype=np.float32)
                if data.ndim == 1:
                    data = data.reshape(-1, 1)
                if data.size == 0 or data.shape[0] == 0:
                    continue
                if data.shape[1] != args.channels:
                    data = fit_channels(data, args.channels)
                peak = max(peak, float(np.max(np.abs(data))))
                pcm = float_to_s16le(data)
                packet = encode_packet(sequence, pcm, args.sample_rate, args.channels)
                try:
                    sock.sendto(packet, (host, port))
                except OSError as exc:
                    raise NetworkError(
                        f"UDP send to {host}:{port} failed: {exc}. "
                        "Check that the receiver is on the same LAN and the address is correct."
                    ) from exc
                sequence = (sequence + 1) & 0xFFFFFFFF
                sent += 1
                now = time.monotonic()
                if now - last >= 1.0:
                    print(f"sent {sent} packets  peak {peak:.3f}", flush=True)
                    if peak < 0.001:
                        quiet += 1
                        if quiet == 5:
                            print(
                                "note: the system mix is silent. Play audio on this PC. "
                                "The sender records desktop audio, not the microphone.",
                                flush=True,
                            )
                    else:
                        quiet = 0
                    peak = 0.0
                    last = now
        except (AudioError, NetworkError):
            raise
        except Exception as exc:
            raise AudioError(f"Capture failed on {name}: {exc}") from exc
    finally:
        if recorder_cm is not None:
            try:
                recorder_cm.__exit__(None, None, None)
            except Exception:
                pass
        sock.close()


def _open_capture(mic, sample_rate: int, channels: int, frames: int):
    """Open a loopback recorder, falling back to the device's own channel count."""
    attempts = [channels]
    native = _native_channels(mic)
    if native and native not in attempts:
        attempts.append(native)
    errors: list[str] = []
    for attempt in attempts:
        recorder_cm = None
        try:
            recorder_cm = mic.recorder(samplerate=sample_rate, channels=attempt, blocksize=frames)
            recorder = recorder_cm.__enter__()
        except Exception as exc:
            if recorder_cm is not None:
                try:
                    recorder_cm.__exit__(None, None, None)
                except Exception:
                    pass
            errors.append(f"{attempt} ch: {exc}")
            continue
        if attempt != channels:
            print(
                f"note: opened capture at {attempt} channels and will adapt it to {channels}.",
                flush=True,
            )
        return recorder_cm, recorder
    detail = "; ".join(errors) if errors else "the device rejected the stream format"
    raise AudioError(f"Could not open loopback capture. {detail}")


def _native_channels(device) -> int:
    try:
        value = device.channels
    except Exception:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _destination(args) -> tuple[str, int]:
    check_port(args.port, "UDP port")
    if args.host:
        return resolve_ipv4(args.host), args.port
    if not args.discover:
        raise NetworkError(
            "Pass --host with the receiver's LAN IP, or --discover to listen for its beacon."
        )
    check_port(args.discovery_port, "Discovery port")
    print(
        f"Looking for a receiver beacon on UDP {args.discovery_port} "
        f"for {args.discover_timeout:.0f}s ...",
        flush=True,
    )
    found = discover_receivers(args.discovery_port, args.discover_timeout)
    if not found:
        raise NetworkError(
            "No receiver beacon heard. Start the receiver first, allow inbound UDP "
            f"{args.discovery_port} on this PC, or pass --host RECEIVER_IP."
        )
    if len(found) == 1 or not sys.stdin.isatty():
        ip, port, name = found[0]
        label = f"{name} at " if name else ""
        print(f"Using {label}{ip}:{port}", flush=True)
        return ip, port
    print("Receivers:")
    for index, (ip, port, name) in enumerate(found):
        label = name or ip
        print(f"  [{index}] {label}  {ip}:{port}")
    raw = input(f"Select receiver [0-{len(found) - 1}] (default 0): ").strip() or "0"
    if not raw.isdigit() or not 0 <= int(raw) < len(found):
        raise NetworkError(f"Receiver {raw!r} is not in the list.")
    ip, port, name = found[int(raw)]
    print(f"Using {name or ip} at {ip}:{port}", flush=True)
    return ip, port


def _print_capture_devices() -> None:
    if sys.platform == "darwin":
        print(MACOS_LIMITATION)
        print()
        try:
            inputs = list_input_devices()
        except AudioError as exc:
            raise AudioError(str(exc)) from exc
        if not inputs:
            print("No input devices found.")
            return
        print("Inputs (a virtual loopback, if you installed one, is in this list):")
        print(format_device_list(inputs))
        return
    devices = list_loopback_devices()
    if not devices:
        raise AudioError(
            "No system-audio loopback found. "
            + (
                "On Windows the sender needs a WASAPI playback device."
                if sys.platform == "win32"
                else "On Linux the sender needs a PulseAudio or PipeWire monitor source."
            )
        )
    print("Loopback / monitor devices (system mix, not microphones):")
    print(format_device_list(devices))
