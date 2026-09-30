"""Let other PCs on the LAN add themselves to the sender.

PC1 broadcasts a short hello and listens for two kinds of replies:
a join packet from a current receiver, and the older receiver beacon.
PC2 listens for the hello and sends a join with the port it plays on.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

from audiostream.net import DEFAULT_DISCOVERY_PORT, lan_ipv4
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
