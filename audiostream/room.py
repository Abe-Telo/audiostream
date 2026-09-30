"""One room on the LAN. Every computer can hear. Only one sends at a time."""

from __future__ import annotations

import json
import socket
import threading
import time

ROOM_PORT = 45127
AUDIO_PORT = 45123
MAGIC = b"ASRM"
STALE_SECONDS = 8


class RoomError(RuntimeError):
    pass


def encode_message(payload: dict) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(raw) > 1400:
        raise ValueError("room message is too large")
    return MAGIC + raw


def decode_message(data: bytes) -> dict | None:
    if len(data) < 5 or not data.startswith(MAGIC):
        return None
    try:
        payload = json.loads(data[4:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "t" not in payload:
        return None
    return payload


def active_sender(peers: list[dict], now: float) -> dict | None:
    """The one computer currently allowed to send. Highest epoch wins."""
    live = [peer for peer in peers if peer.get("sending") and now - float(peer.get("last_seen", 0)) < STALE_SECONDS]
    if not live:
        return None
    return max(live, key=lambda peer: (int(peer.get("epoch", 0)), str(peer.get("name", ""))))


def takeover_name(peers: list[dict], my_name: str, now: float) -> str | None:
    """Name to put in the confirm dialog. None when nobody else is sending."""
    sender = active_sender(peers, now)
    if sender is None or sender.get("name") == my_name:
        return None
    return str(sender.get("name") or "another computer")


def should_yield(my_name: str, my_epoch: int, other_name: str, other_epoch: int, other_sending: bool) -> bool:
    if not other_sending or other_name == my_name:
        return False
    return (int(other_epoch), other_name) > (int(my_epoch), my_name)


class Room:
    def __init__(self, name: str) -> None:
        self.name = name[:64] or "computer"
        self.role = ""
        self.epoch = 0
        self.last_event = ""
        self._lock = threading.Lock()
        self._peers: dict[str, dict] = {}
        self._devices: dict[str, list[dict]] = {}

    def choose(self, role: str, epoch: int | None = None) -> None:
        if role not in {"sender", "receiver"}:
            raise RoomError(f"Unknown role {role!r}")
        with self._lock:
            self.role = role
            if role == "sender":
                self.epoch = int(time.time() * 1000) & 0x7FFFFFFF if epoch is None else int(epoch)
                self.last_event = "This computer is sending."
            else:
                self.epoch = 0
                self.last_event = "This computer is playing."

    def note(self, ip: str, payload: dict) -> str | None:
        """Record a datagram. Returns 'yield' when a newer sender should replace us."""
        kind = payload.get("t")
        name = str(payload.get("name") or ip)[:64]
        if name == self.name and kind in {"hello", "devices"}:
            return None
        now = time.time()
        yielded = None
        with self._lock:
            if kind == "hello":
                sending = bool(payload.get("sending"))
                epoch = int(payload.get("epoch") or 0)
                self._peers[ip] = {
                    "ip": ip,
                    "name": name,
                    "port": int(payload.get("port") or AUDIO_PORT),
                    "sending": sending,
                    "epoch": epoch,
                    "last_seen": now,
                }
                if self.role == "sender" and should_yield(self.name, self.epoch, name, epoch, sending):
                    self.role = "receiver"
                    self.epoch = 0
                    self.last_event = f"{name} is sending now."
                    yielded = "yield"
            elif kind == "devices" and name != self.name:
                items = payload.get("items")
                if isinstance(items, list):
                    self._devices[name] = [_clean_device(item) for item in items[:16] if isinstance(item, dict)]
            elif kind == "bye" and name != self.name:
                self._peers.pop(ip, None)
        return yielded

    def peers(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [dict(peer) for peer in self._peers.values() if now - peer["last_seen"] < STALE_SECONDS]

    def other_sender_name(self) -> str | None:
        return takeover_name(self.peers(), self.name, time.time())

    def destinations(self) -> list[tuple[str, int]]:
        return [(peer["ip"], int(peer["port"])) for peer in self.peers()]

    def devices_for(self, name: str) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._devices.get(name, [])]

    def device_groups(self, local_devices: list[dict]) -> list[tuple[str, list[dict]]]:
        groups = [(self.name, local_devices)]
        with self._lock:
            for name in sorted(self._devices):
                if name != self.name:
                    groups.append((name, [dict(item) for item in self._devices[name]]))
        return groups

    def hello(self) -> dict:
        with self._lock:
            return {
                "t": "hello",
                "name": self.name,
                "port": AUDIO_PORT,
                "sending": self.role == "sender",
                "epoch": self.epoch if self.role == "sender" else 0,
            }


def _clean_device(item: dict) -> dict:
    try:
        volume = float(item.get("volume", 0))
    except (TypeError, ValueError):
        volume = 0.0
    return {
        "id": str(item.get("id") or "")[:120],
        "name": str(item.get("name") or "Speaker")[:80],
        "volume": max(0.0, min(1.0, volume)),
        "muted": bool(item.get("muted")),
        "bluetooth": bool(item.get("bluetooth")),
    }


class RoomNet:
    """Broadcast presence and accept commands from the other computers."""

    def __init__(self, room: Room, on_yield=None, on_command=None) -> None:
        self.room = room
        self.on_yield = on_yield
        self.on_command = on_command
        self.error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._devices: list[dict] = []

    def set_devices(self, devices: list[dict]) -> None:
        self._devices = devices

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", ROOM_PORT))
            sock.settimeout(0.3)
        except OSError as exc:
            sock.close()
            self.error = f"Could not join the room on UDP {ROOM_PORT} ({exc}). Close the other Audiostream window."
            return
        self._sock = sock
        self._thread = threading.Thread(target=self._run, name="audiostream-room", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._send(encode_message({"t": "bye", "name": self.room.name}))
        except Exception:
            pass
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)

    def _run(self) -> None:
        next_hello = 0.0
        next_devices = 0.0
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_hello:
                self._send(encode_message(self.room.hello()))
                next_hello = now + 1.0
            if now >= next_devices:
                self._send(encode_message({"t": "devices", "name": self.room.name, "items": self._devices[:16]}))
                next_devices = now + 2.0
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            payload = decode_message(data)
            if payload is None:
                continue
            if payload.get("t") == "bt" and payload.get("to") == self.room.name and self.on_command is not None:
                self.on_command(payload)
                continue
            if self.room.note(addr[0], payload) == "yield" and self.on_yield is not None:
                self.on_yield()

    def send_command(self, target: str, op: str, **fields) -> None:
        payload = {"t": "bt", "to": target, "op": op, **fields}
        self._send(encode_message(payload))

    def _send(self, blob: bytes) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            sock.sendto(blob, ("255.255.255.255", ROOM_PORT))
        except OSError as exc:
            self.error = str(exc)
