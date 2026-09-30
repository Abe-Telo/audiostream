"""Let other PCs on the LAN add themselves to the sender.

PC1 broadcasts a short hello and listens for two kinds of replies:
a join packet from a current receiver, and the older receiver beacon.
PC2 listens for the hello and sends a join with the port it plays on.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
import uuid

from audiostream.net import DEFAULT_DISCOVERY_PORT, DEFAULT_PORT, lan_ipv4
from audiostream.roster import Roster

JOIN_MAGIC = b"ASJN"
SENDER_MAGIC = b"ASSB"
PROTOCOL_VERSION = 1
CONTROL_PORT = 45125
SENDER_BEACON_PORT = 45126


def encode_join(stream_port: int, name: str) -> bytes:
    return _pack(JOIN_MAGIC, stream_port, name)


def decode_join(data: bytes) -> tuple[int, str] | None:
    return _unpack(JOIN_MAGIC, data)


def encode_sender_beacon(control_port: int, name: str) -> bytes:
    return _pack(SENDER_MAGIC, control_port, name)


def decode_sender_beacon(data: bytes) -> tuple[int, str] | None:
    return _unpack(SENDER_MAGIC, data)


def _is_this_computer(ip: str) -> bool:
    """True for this PC's LAN address. 127.0.0.1 still counts as another socket in tests."""
    own = lan_ipv4()
    return bool(own) and own != "127.0.0.1" and ip == own


def _pack(magic: bytes, port: int, name: str) -> bytes:
    if not 1 <= port <= 65535:
        raise ValueError(f"port out of range: {port}")
    raw = name.encode("utf-8")[:64]
    return magic + struct.pack("!BH", PROTOCOL_VERSION, port) + raw


def _unpack(magic: bytes, data: bytes) -> tuple[int, str] | None:
    if len(data) < 7 or not data.startswith(magic):
        return None
    version, port = struct.unpack("!BH", data[4:7])
    if version != PROTOCOL_VERSION or not 1 <= port <= 65535:
        return None
    return port, data[7:].decode("utf-8", errors="replace")


class JoinListener:
    """PC1 accepts "add me" packets from receivers."""

    def __init__(self, roster: Roster, port: int = CONTROL_PORT) -> None:
        self.roster = roster
        self.port = port
        self.bound_port = port
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audiostream-join", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            self.bound_port = sock.getsockname()[1]
            sock.settimeout(0.3)
        except OSError as exc:
            self.error = (
                f"Could not listen for computers joining this PC on UDP {self.port} ({exc}). "
                "Close the other Audiostream window if one is already open."
            )
            self._ready.set()
            sock.close()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    continue
                decoded = decode_join(data)
                if decoded is None:
                    continue
                stream_port, name = decoded
                if _is_this_computer(addr[0]):
                    continue
                try:
                    self.roster.upsert(addr[0], stream_port, name or addr[0], "network", persist=True)
                except Exception:
                    continue
        finally:
            sock.close()


class ReceiverWatch:
    """PC1 collects receiver beacons already on the LAN and adds those computers."""

    def __init__(self, roster: Roster, port: int = DEFAULT_DISCOVERY_PORT, ignore_ip: str | None = None) -> None:
        self.roster = roster
        self.port = port
        self.ignore_ip = ignore_ip if ignore_ip is not None else lan_ipv4()
        self.bound_port = port
        self.error: str | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audiostream-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from audiostream.net import decode_beacon

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
            self.bound_port = sock.getsockname()[1]
            sock.settimeout(0.3)
        except OSError as exc:
            self.error = (
                f"Could not listen for other PCs on UDP {self.port} ({exc}). "
                "You can still add a computer by its IP address."
            )
            self._ready.set()
            sock.close()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(256)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    continue
                if addr[0] == self.ignore_ip and self.ignore_ip != "127.0.0.1":
                    continue
                decoded = decode_beacon(data)
                if decoded is None:
                    continue
                stream_port, name = decoded
                try:
                    self.roster.upsert(addr[0], stream_port, name or addr[0], "network")
                except Exception:
                    continue
        finally:
            sock.close()


class SenderBeacon:
    """PC1 tells the LAN that a sender is here and where to join it."""

    def __init__(
        self,
        name: str,
        control_port: int = CONTROL_PORT,
        beacon_port: int = SENDER_BEACON_PORT,
        destination: str = "255.255.255.255",
    ) -> None:
        self._payload = encode_sender_beacon(control_port, name)
        self._beacon_port = beacon_port
        self._destination = destination
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audiostream-sender-beacon", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if self._destination == "255.255.255.255":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop.is_set():
                try:
                    sock.sendto(self._payload, (self._destination, self._beacon_port))
                except OSError as exc:
                    self.error = str(exc)
                self._stop.wait(1.0)
        finally:
            sock.close()


class SenderJoiner:
    """PC2 finds the sender and asks to be added."""

    def __init__(self, stream_port: int, name: str, beacon_port: int = SENDER_BEACON_PORT) -> None:
        self.stream_port = stream_port
        self.name = name
        self.beacon_port = beacon_port
        self.bound_port = beacon_port
        self.error: str | None = None
        self.sender: tuple[str, int, str] | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audiostream-joiner", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen.bind(("", self.beacon_port))
            self.bound_port = listen.getsockname()[1]
            listen.settimeout(0.3)
        except OSError as exc:
            self.error = f"Could not listen for a sender on UDP {self.beacon_port} ({exc})."
            self._ready.set()
            listen.close()
            reply.close()
            return
        self._ready.set()
        payload = encode_join(self.stream_port, self.name)
        next_hello = 0.0
        try:
            while not self._stop.is_set():
                try:
                    data, addr = listen.recvfrom(256)
                except socket.timeout:
                    data = b""
                    addr = ("", 0)
                except OSError:
                    if self._stop.is_set():
                        return
                    continue
                decoded = decode_sender_beacon(data) if data else None
                if decoded is not None and _is_this_computer(addr[0]):
                    decoded = None
                if decoded is not None and self.sender is None:
                    control_port, name = decoded
                    self.sender = (addr[0], control_port, name or addr[0])
                    print(
                        f"Added this PC to sender {self.sender[2]} at {self.sender[0]}.",
                        flush=True,
                    )
                    next_hello = 0.0
                if self.sender is not None and time.monotonic() >= next_hello:
                    ip, control_port, _name = self.sender
                    try:
                        reply.sendto(payload, (ip, control_port))
                    except OSError as exc:
                        self.error = str(exc)
                    next_hello = time.monotonic() + 2.0
        finally:
            listen.close()
            reply.close()


PRESENCE_MAGIC = b"ASPR"
PRESENCE_PORT = 45127


def encode_presence(message: dict) -> bytes:
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return PRESENCE_MAGIC + body


def decode_presence(data: bytes) -> dict | None:
    if len(data) < 5 or not data.startswith(PRESENCE_MAGIC):
        return None
    try:
        message = json.loads(data[len(PRESENCE_MAGIC) :].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict) or "kind" not in message:
        return None
    return message


class PresenceService:
    """Tell the LAN this program is running, and keep the computer list in sync.

    Every copy broadcasts a hello. Add computer listens for those hellos.
    Adding, renaming, or removing a computer is forwarded to the others.
    """

    def __init__(
        self,
        roster: Roster | None,
        name: str,
        stream_port: int = DEFAULT_PORT,
        port: int = PRESENCE_PORT,
        own_ip: str | None = None,
    ) -> None:
        self.roster = roster
        self.name = name
        self.stream_port = stream_port
        self.port = port
        self.own_ip = own_ip if own_ip is not None else lan_ipv4()
        self.bound_port = port
        self.error: str | None = None
        self._peers: dict[str, dict] = {}
        self._published: dict[tuple[str, int], str] = {}
        self._seen_ids: set[str] = set()
        self._seen_order: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._sock: socket.socket | None = None
        self._announce_addr: tuple[str, int] | None = ("255.255.255.255", PRESENCE_PORT) if port == PRESENCE_PORT else None
        self._thread = threading.Thread(target=self._run, name="audiostream-presence", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(2.0)

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._thread.join(timeout=1.5)

    def peers(self) -> list[dict]:
        now = time.time()
        with self._lock:
            rows = [
                {"ip": ip, "name": info["name"], "port": info["port"]}
                for ip, info in self._peers.items()
                if now - info["seen"] < 6 and ip != self.own_ip
            ]
        rows.sort(key=lambda row: (row["name"].lower(), row["ip"]))
        return rows

    def probe(self, destination: tuple[str, int] | None = None) -> None:
        self._send({"kind": "probe"}, destination)
        self._send(self._hello(), destination)

    def publish_local_changes(self) -> None:
        """Forward computers added or removed on this PC."""
        if self.roster is None:
            return
        current = self._names()
        with self._lock:
            previous = dict(self._published)
            if current == previous:
                return
            self._published = dict(current)
        for key, name in current.items():
            if key not in previous:
                ip, port = key
                volume = self._volume(ip, port)
                self._emit("add", ip, port, name, volume)
            elif previous[key] != name:
                ip, port = key
                self._emit("rename", ip, port, name, self._volume(ip, port))
        for key in previous:
            if key not in current:
                ip, port = key
                self._emit("remove", ip, port, previous[key], 100)

    def publish_volume(self, ip: str, port: int, volume: int) -> None:
        self._emit("volume", ip, port, "", int(volume))

    def _hello(self) -> dict:
        return {"kind": "hello", "name": self.name, "port": self.stream_port}

    def _names(self) -> dict[tuple[str, int], str]:
        if self.roster is None:
            return {}
        return {(row["ip"], row["port"]): row["name"] for row in self.roster.snapshot()}

    def _volume(self, ip: str, port: int) -> int:
        if self.roster is None:
            return 100
        for row in self.roster.snapshot():
            if row["ip"] == ip and row["port"] == port:
                return int(row["volume"])
        return 100

    def _emit(self, op: str, ip: str, port: int, name: str, volume: int) -> None:
        message = {
            "kind": "share",
            "id": uuid.uuid4().hex,
            "op": op,
            "ip": ip,
            "port": int(port),
            "name": name,
            "volume": int(volume),
        }
        self._remember(message["id"])
        self._send(message, self._announce_addr)

    def _remember(self, message_id: str) -> bool:
        """Return True if this id is new."""
        with self._lock:
            if message_id in self._seen_ids:
                return False
            self._seen_ids.add(message_id)
            self._seen_order.append(message_id)
            if len(self._seen_order) > 200:
                old = self._seen_order.pop(0)
                self._seen_ids.discard(old)
            return True

    def _send(self, message: dict, destination: tuple[str, int] | None = None) -> None:
        dest = self._announce_addr if destination is None else destination
        sock = self._sock
        if dest is None or sock is None:
            return
        try:
            sock.sendto(encode_presence(message), dest)
        except OSError as exc:
            self.error = str(exc)

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", self.port))
            self.bound_port = sock.getsockname()[1]
            sock.settimeout(0.3)
        except OSError as exc:
            self.error = f"Could not announce this PC on UDP {self.port} ({exc})."
            self._ready.set()
            sock.close()
            return
        self._sock = sock
        self._ready.set()
        next_hello = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if self._announce_addr is not None and now >= next_hello:
                    self._send(self._hello())
                    self.publish_local_changes()
                    next_hello = now + 2.0
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    continue
                message = decode_presence(data)
                if message is not None:
                    self._handle(message, addr)
        finally:
            self._sock = None
            try:
                sock.close()
            except OSError:
                pass

    def _handle(self, message: dict, addr: tuple) -> None:
        kind = message.get("kind")
        if kind == "probe":
            if addr[0] != self.own_ip:
                self._send(self._hello(), (addr[0], addr[1]))
            return
        if kind == "hello":
            if addr[0] == self.own_ip:
                return
            try:
                port = int(message.get("port") or DEFAULT_PORT)
            except (TypeError, ValueError):
                return
            name = str(message.get("name") or addr[0])[:64]
            with self._lock:
                self._peers[addr[0]] = {"name": name, "port": port, "seen": time.time()}
            return
        if kind != "share":
            return
        message_id = str(message.get("id") or "")
        if not message_id or not self._remember(message_id):
            return
        if addr[0] == self.own_ip:
            return
        self._apply(message)
        self._send(message, self._announce_addr)

    def _apply(self, message: dict) -> None:
        if self.roster is None:
            return
        op = str(message.get("op") or "")
        ip = str(message.get("ip") or "").strip()
        try:
            port = int(message.get("port") or 0)
        except (TypeError, ValueError):
            return
        if not ip or ip == self.own_ip or not 1 <= port <= 65535:
            return
        name = str(message.get("name") or ip)[:64]
        try:
            volume = int(message.get("volume") if message.get("volume") is not None else 100)
        except (TypeError, ValueError):
            volume = 100
        try:
            if op == "add":
                self.roster.upsert(ip, port, name, "network")
                self.roster.set_volume(ip, port, volume)
            elif op == "rename":
                self.roster.rename(ip, port, name)
            elif op == "remove":
                self.roster.remove(ip, port)
            elif op == "volume":
                self.roster.set_volume(ip, port, volume)
                self.roster.save()
            else:
                return
        except Exception:
            return
        with self._lock:
            self._published = self._names()
