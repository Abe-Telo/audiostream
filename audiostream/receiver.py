"""Receive UDP audio and play it on a chosen output device."""

from __future__ import annotations

import socket
import sys
import threading
import time

from audiostream.audio import (
    AudioError,
    fit_channels,
    format_device_list,
    list_output_devices,
    open_output,
    play_tone,
    s16le_to_float,
    select_device,
)
from audiostream.net import BeaconSender, check_port, receiver_socket
from audiostream.packet import JitterBuffer, PacketError, decode_packet


class _StreamState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jitter: JitterBuffer | None = None
        self.generation = 0
        self.bad = 0
        self.source: str | None = None


def run_receiver(args) -> None:
    if args.list_devices:
        devices = list_output_devices()
        if not devices:
            raise AudioError(
                "No output devices found. Pair and connect the Bluetooth device in the OS settings, then try again."
            )
        print("Output devices:")
        print(format_device_list(devices))
        return

    devices = list_output_devices()
    if not devices:
        raise AudioError(
            "No output devices found. Pair and connect the Bluetooth speaker or headphones "
            "in the OS settings first. It then shows up as a normal output."
        )
    if args.device is None and len(devices) > 1:
        print("Output devices:")
        print(format_device_list(devices))
    device = select_device(
        devices,
        args.device,
        interactive=sys.stdin.isatty(),
        kind_label="output",
    )
    speaker = open_output(device)
    print(f"Output: [{device.index}] {device.name}", flush=True)

    if args.test_tone:
        print(f"Playing a {args.tone_seconds:g}s tone on {device.name} ...", flush=True)
        play_tone(speaker, args.tone_seconds, args.sample_rate, args.channels)
        print("Tone finished.", flush=True)

    check_port(args.port, "UDP port")
    if args.buffer_ms < 1:
        raise AudioError("--buffer-ms must be at least 1")
    if args.buffer_ms < 50 or args.buffer_ms > 200:
        print(
            f"note: buffer is {args.buffer_ms} ms. 50–200 ms is the useful range on Wi-Fi "
            "(lower delays more, higher hides dropouts).",
            flush=True,
        )

    sock = receiver_socket(args.bind, args.port)
    stop = threading.Event()
    state = _StreamState()
    beacon: BeaconSender | None = None
    if not args.no_discover:
        beacon = BeaconSender(args.port, args.discovery_port, socket.gethostname())
        beacon.start()
        print(f"Discovery beacon on UDP {args.discovery_port}", flush=True)
    print(
        f"Listening on UDP {args.bind}:{args.port}  buffer {args.buffer_ms} ms",
        flush=True,
    )
    print(
        "Allow this UDP port through the firewall. Ctrl+C to stop.",
        flush=True,
    )

    thread = threading.Thread(
        target=_receive_loop,
        args=(sock, state, args.buffer_ms, stop),
        name="audiostream-recv",
        daemon=True,
    )
    thread.start()
    try:
        _play_loop(speaker, state, stop)
    finally:
        stop.set()
        if beacon is not None:
            beacon.stop()
            if beacon.error:
                print(f"warning: discovery beacon failed: {beacon.error}", flush=True)
        sock.close()


def _receive_loop(sock, state: _StreamState, buffer_ms: int, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            if stop.is_set():
                return
            print("error: UDP receive failed; the socket is no longer usable.", flush=True)
            stop.set()
            return
        try:
            packet = decode_packet(data)
        except PacketError:
            with state.lock:
                state.bad += 1
            continue
        with state.lock:
            if state.source != addr[0]:
                state.source = addr[0]
                print(
                    f"Stream from {addr[0]}:{addr[1]}  "
                    f"{packet.sample_rate} Hz, {packet.channels} ch",
                    flush=True,
                )
            jitter = state.jitter
            if (
                jitter is None
                or jitter.sample_rate != packet.sample_rate
                or jitter.channels != packet.channels
            ):
                state.jitter = JitterBuffer(
                    packet.sample_rate,
                    packet.channels,
                    buffer_ms,
                    packet.frame_count,
                )
                state.generation += 1
                jitter = state.jitter
            status = jitter.push(packet)
            if status == "resync":
                print("Sender sequence jumped; resyncing the buffer.", flush=True)


def _play_loop(speaker, state: _StreamState, stop: threading.Event) -> None:
    player = None
    opened_gen = -1
    block = 480
    actual_channels = 2
    stream_channels = 2
    last_stat = time.monotonic()
    announced_wait = False
    try:
        while not stop.is_set():
            with state.lock:
                jitter = state.jitter
                generation = state.generation
                is_ready = jitter.ready() if jitter is not None else False
            if jitter is None or generation != opened_gen:
                if player is not None:
                    _close_player(player)
                    player = None
                if not is_ready:
                    if not announced_wait:
                        print("Waiting for the sender ...", flush=True)
                        announced_wait = True
                    time.sleep(0.05)
                    continue
                block = max(1, jitter.sample_rate // 100)
                stream_channels = jitter.channels
                device_channels = _speaker_channels(speaker)
                play_channels = stream_channels if device_channels <= 0 else min(stream_channels, device_channels)
                try:
                    player = speaker.player(
                        samplerate=jitter.sample_rate,
                        channels=play_channels,
                        blocksize=block,
                    ).__enter__()
                except Exception as exc:
                    raise AudioError(f"Could not open playback on the selected device: {exc}") from exc
                actual_channels = _player_channels(player, play_channels)
                opened_gen = generation
                announced_wait = False
                continue

            with state.lock:
                if state.generation != opened_gen or state.jitter is None:
                    continue
                pcm = state.jitter.pull(block)
                stream_channels = state.jitter.channels
                stats = state.jitter
                bad = state.bad
            if pcm is None:
                time.sleep(0.005)
                continue
            samples = fit_channels(s16le_to_float(pcm, stream_channels), actual_channels)
            try:
                player.play(samples)
            except Exception as exc:
                raise AudioError(f"Playback failed: {exc}") from exc

            now = time.monotonic()
            if now - last_stat >= 1.0:
                print(
                    f"packets={stats.received} late={stats.late_drops} "
                    f"gaps={stats.missing} underruns={stats.underruns} "
                    f"bad={bad} buffered={stats.buffered_frames()} frames",
                    flush=True,
                )
                last_stat = now
    finally:
        if player is not None:
            _close_player(player)


def _speaker_channels(speaker) -> int:
    try:
        value = speaker.channels
    except Exception:
        return 0
    if isinstance(value, int):
        return value
    try:
        return len(value)
    except TypeError:
        return 0


def _player_channels(player, fallback: int) -> int:
    value = getattr(player, "channels", None)
    if isinstance(value, int) and value > 0:
        return value
    channelmap = getattr(player, "channelmap", None)
    if channelmap:
        return len(set(channelmap))
    return fallback


def _close_player(player) -> None:
    try:
        player.__exit__(None, None, None)
    except Exception:
        pass
