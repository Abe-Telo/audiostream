"""Capture the system mix once and send it to every computer in the roster."""

from __future__ import annotations

import sys
import threading
import time

from audiostream.net import sender_socket
from audiostream.packet import encode_packet, frames_per_packet
from audiostream.pcm import peak_s16le, scale_s16le
from audiostream.roster import Roster


class StreamStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.sent = 0
        self.peak = 0.0
        self.capture = ""
        self.rate = 0
        self.error = ""


class StreamHub:
    def __init__(self, roster: Roster, sample_rate: int = 48000, channels: int = 2, chunk_ms: int = 5) -> None:
        self.roster = roster
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.stats = StreamStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture = None

    @property
    def running(self) -> bool:
        with self.stats.lock:
            return self.stats.running

    def start(self, device: str | None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        with self.stats.lock:
            self.stats.error = ""
            self.stats.sent = 0
            self.stats.peak = 0.0
        self._thread = threading.Thread(target=self._run, args=(device,), name="audiostream-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        capture = self._capture
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        with self.stats.lock:
            self.stats.running = False

    def _run(self, device: str | None) -> None:
        frames = frames_per_packet(self.sample_rate, self.chunk_ms, self.channels)
        try:
            capture = open_system_capture(device, self.sample_rate, self.channels, frames)
        except Exception as exc:
            with self.stats.lock:
                self.stats.error = str(exc)
                self.stats.running = False
            return
        self._capture = capture
        sock = sender_socket()
        sequence = 0
        captured = 0
        peak = 0.0
        last = time.monotonic()
        with self.stats.lock:
            self.stats.running = True
            self.stats.capture = capture.name
            self.stats.rate = capture.sample_rate
            self.stats.error = ""
        try:
            while not self._stop.is_set():
                try:
                    pcm = capture.read()
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    raise RuntimeError(exc) from exc
                if not pcm:
                    continue
                captured += 1
                peak = max(peak, peak_s16le(pcm))
                destinations = self.roster.destinations()
                if destinations:
                    master = self.roster.master_volume_value() / 100.0
                    cache: dict[str, bytes] = {}
                    for dest in destinations:
                        gain = master * (dest.volume / 100.0)
                        packet = packet_for_gain(pcm, gain, sequence, capture.sample_rate, self.channels, cache)
                        try:
                            sock.sendto(packet, (dest.ip, dest.port))
                            self.roster.clear_send_error(dest.ip, dest.port)
                        except OSError as exc:
                            self.roster.mark_send_error(dest.ip, dest.port, str(exc))
                    sequence = (sequence + 1) & 0xFFFFFFFF
                now = time.monotonic()
                if now - last >= 0.25:
                    with self.stats.lock:
                        self.stats.sent = captured
                        self.stats.peak = peak
                    if now - last >= 1.0:
                        peak = 0.0
                        last = now
        except Exception as exc:
            if not self._stop.is_set():
                with self.stats.lock:
                    self.stats.error = f"Capture failed on {capture.name}: {exc}"
        finally:
            with self.stats.lock:
                self.stats.running = False
            self._capture = None
            try:
                capture.close()
            except Exception:
                pass
            sock.close()


def device_argument(choice: str) -> str | None:
    """Combobox text to the capture id. "Default playback" follows the Windows default."""
    text = (choice or "").strip()
    if not text or text.lower().startswith("default"):
        return None
    return text.split()[0]


def send_pcm_to_roster(sock, pcm: bytes, roster: Roster, sample_rate: int, channels: int, sequence: int) -> int:
    """Send one already-captured chunk to every destination. Returns how many were sent."""
    destinations = roster.destinations()
    if not destinations or not pcm:
        return 0
    master = roster.master_volume_value() / 100.0
    cache: dict[str, bytes] = {}
    sent = 0
    for dest in destinations:
        gain = master * (dest.volume / 100.0)
        packet = packet_for_gain(pcm, gain, sequence, sample_rate, channels, cache)
        sock.sendto(packet, (dest.ip, dest.port))
        sent += 1
    return sent


def packet_for_gain(
    pcm: bytes,
    gain: float,
    sequence: int,
    sample_rate: int,
    channels: int,
    cache: dict[str, bytes],
) -> bytes:
    """Encode one chunk. Full volume and silence are encoded once and reused."""
    if gain >= 0.999:
        key = "full"
        payload = pcm
    elif gain <= 0.001:
        key = "zero"
        payload = b"\x00" * (len(pcm) - (len(pcm) % 2))
    else:
        key = ""
        payload = scale_s16le(pcm, gain)
    if key and key in cache:
        return cache[key]
    packet = encode_packet(sequence, payload, sample_rate, channels)
    if key:
        cache[key] = packet
    return packet


def open_system_capture(device: str | None, sample_rate: int, channels: int, frames: int):
    if sys.platform == "win32":
        from audiostream.win_audio import open_capture

        return open_capture(device, sample_rate, channels, frames)

    from audiostream.audio import fit_channels, float_to_s16le, import_numpy, open_loopback
    from audiostream.sender import _open_capture

    mic = open_loopback(device)
    recorder_cm, recorder = _open_capture(mic, sample_rate, channels, frames)

    class _LinuxCapture:
        name = getattr(mic, "name", "loopback")
        sample_rate = sample_rate

        def read(self) -> bytes:
            np = import_numpy()
            data = np.asarray(recorder.record(frames), dtype=np.float32)
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            if data.size == 0 or data.shape[0] == 0:
                return b""
            if data.shape[1] != channels:
                data = fit_channels(data, channels)
            return float_to_s16le(data)

        def close(self) -> None:
            try:
                recorder_cm.__exit__(None, None, None)
            except Exception:
                pass

    return _LinuxCapture()
