"""PC2 playback: receive the LAN stream and play it on a Windows output."""

from __future__ import annotations

import socket
import sys
import threading
import time

from audiostream.net import DEFAULT_PORT, BeaconSender, receiver_socket
from audiostream.packet import JitterBuffer, PacketError, decode_packet


class ListenStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.phase = "stopped"
        self.source = ""
        self.detail = ""
        self.error = ""


class Listener:
    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.stats = ListenStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._player = None
        self._live: dict | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, device: str | None | list = None) -> None:
        if self.running:
            return
        if device is None or isinstance(device, str):
            devices: list[str | None] = [device]
        else:
            devices = list(device) or [None]
        self._stop.clear()
        with self.stats.lock:
            self.stats.phase = "starting"
            self.stats.error = ""
            self.stats.source = ""
            self.stats.detail = ""
        self._thread = threading.Thread(target=self._run, args=(devices,), name="audiostream-listen", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # The playback thread closes the speaker itself. Closing it from the
        # button thread while write() is in progress crashes Windows audio.
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if thread is not None and not thread.is_alive():
            self._thread = None
        with self.stats.lock:
            if self.stats.phase != "error":
                self.stats.phase = "stopped"

    def _run(self, devices: list[str | None]) -> None:
        if sys.platform != "win32":
            with self.stats.lock:
                self.stats.phase = "error"
                self.stats.error = "Playback runs in the Windows app."
            return
        from audiostream.presence import SenderJoiner
        from audiostream.win_audio import open_output

        outputs = []
        errors = []
        for device in devices:
            try:
                outputs.append({"device": device, "player": open_output(device, 48000, 2, 480)})
            except Exception as exc:
                errors.append(str(exc))
        if not outputs:
            with self.stats.lock:
                self.stats.phase = "error"
                self.stats.error = errors[0] if errors else "No playback device found."
            return
        self._player = outputs[0]["player"]
        live = {"outputs": outputs}
        self._live = live
        try:
            sock = receiver_socket("0.0.0.0", self.port)
        except Exception as exc:
            for item in outputs:
                item["player"].close()
            self._player = None
            self._live = None
            with self.stats.lock:
                self.stats.phase = "error"
                self.stats.error = str(exc)
            return
        beacon = BeaconSender(self.port, 45124, socket.gethostname())
        joiner = SenderJoiner(self.port, socket.gethostname())
        beacon.start()
        joiner.start()
        state_lock = threading.Lock()
        holder: dict = {"jitter": None, "generation": 0, "source": ""}
        recv = threading.Thread(
            target=_receive,
            args=(sock, holder, state_lock, self.stats, self._stop),
            name="audiostream-pc2-recv",
            daemon=True,
        )
        recv.start()
        with self.stats.lock:
            self.stats.phase = "waiting"
            self.stats.detail = ", ".join(item["player"].name for item in outputs)
        try:
            _play(live, holder, state_lock, self.stats, self._stop)
        except Exception as exc:
            if not self._stop.is_set():
                with self.stats.lock:
                    self.stats.phase = "error"
                    self.stats.error = str(exc)
        finally:
            self._stop.set()
            beacon.stop()
            joiner.stop()
            sock.close()
            for item in live.get("outputs", []):
                try:
                    item["player"].close()
                except Exception:
                    pass
            self._player = None
            self._live = None
            recv.join(timeout=1.0)


def _receive(sock, holder: dict, lock: threading.Lock, stats: ListenStats, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            return
        try:
            packet = decode_packet(data)
        except PacketError:
            continue
        with lock:
            jitter = holder["jitter"]
            if jitter is None or jitter.sample_rate != packet.sample_rate or jitter.channels != packet.channels:
                holder["jitter"] = JitterBuffer(packet.sample_rate, packet.channels, 120, packet.frame_count)
                holder["generation"] += 1
                jitter = holder["jitter"]
            if holder["source"] != addr[0]:
                holder["source"] = addr[0]
            jitter.push(packet)
        with stats.lock:
            stats.source = addr[0]
            if stats.phase != "error":
                stats.phase = "playing"


def _play(live: dict, holder: dict, lock: threading.Lock, stats: ListenStats, stop: threading.Event) -> None:
    opened_gen = -1
    while not stop.is_set():
        with lock:
            jitter = holder["jitter"]
            generation = holder["generation"]
            ready = jitter.ready() if jitter is not None else False
        if jitter is None or not ready:
            time.sleep(0.05)
            continue
        outputs = live["outputs"]
        if generation != opened_gen:
            from audiostream.win_audio import open_output

            refreshed = []
            for item in outputs:
                current = item["player"]
                if jitter.sample_rate != current.sample_rate or jitter.channels != current.channels:
                    device = item["device"]
                    try:
                        current.close()
                    except Exception:
                        pass
                    current = open_output(
                        device, jitter.sample_rate, jitter.channels, max(1, jitter.sample_rate // 100)
                    )
                refreshed.append({"device": item["device"], "player": current})
            live["outputs"] = refreshed
            outputs = refreshed
            opened_gen = generation
        block = max(1, jitter.sample_rate // 100)
        with lock:
            if holder["generation"] != opened_gen or holder["jitter"] is None:
                continue
            pcm = holder["jitter"].pull(block)
            channels = holder["jitter"].channels
        if pcm is None:
            time.sleep(0.005)
            continue
        for item in outputs:
            item["player"].write(pcm, src_channels=channels)
        with stats.lock:
            stats.phase = "playing"
            stats.detail = ", ".join(item["player"].name for item in outputs)
