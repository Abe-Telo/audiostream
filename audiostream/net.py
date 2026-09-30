"""UDP send/receive sockets and the optional receiver discovery beacon."""

from __future__ import annotations

import socket
import struct
import threading
import time

BEACON_MAGIC = b"ASBC"
BEACON_VERSION = 1
DEFAULT_PORT = 45123
DEFAULT_DISCOVERY_PORT = 45124


class NetworkError(RuntimeError):
    """Bind, resolve, or send failed."""


def encode_beacon(stream_port: int, name: str) -> bytes:
    if not 1 <= stream_port <= 65535:
        raise ValueError(f"stream port out of range: {stream_port}")
    raw = name.encode("utf-8")[:64]
    return BEACON_MAGIC + struct.pack("!BH", BEACON_VERSION, stream_port) + raw


def decode_beacon(data: bytes) -> tuple[int, str] | None:
    if len(data) < 7 or not data.startswith(BEACON_MAGIC):
        return None
    version, port = struct.unpack("!BH", data[4:7])
    if version != BEACON_VERSION or not 1 <= port <= 65535:
        return None
    name = data[7:].decode("utf-8", errors="replace")
    return port, name


def check_port(port: int, label: str) -> None:
    if not 1 <= port <= 65535:
        raise NetworkError(f"{label} must be between 1 and 65535 (got {port})")


def lan_ipv4() -> str:
    """IPv4 address this computer uses to reach the local network."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def resolve_ipv4(host: str) -> str:
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    except socket.gaierror as exc:
        raise NetworkError(f"Could not resolve {host!r}: {exc}") from exc
    if not infos:
        raise NetworkError(f"Could not resolve {host!r} to an IPv4 address")
    return infos[0][4][0]


def sender_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
    except OSError:
        pass
    return sock


def receiver_socket(host: str, port: int) -> socket.socket:
    check_port(port, "UDP port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise NetworkError(
            f"Could not bind UDP {host}:{port} ({exc}). "
            "Another receiver may already be using that port."
        ) from exc
    sock.settimeout(0.5)
    return sock


def discover_receivers(discovery_port: int, timeout: float) -> list[tuple[str, int, str]]:
    """Listen for receiver beacons. Returns (ip, stream_port, name)."""
    check_port(discovery_port, "Discovery port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", discovery_port))
    except OSError as exc:
        sock.close()
        raise NetworkError(
            f"Could not listen for receiver beacons on UDP {discovery_port} ({exc}). "
            "Pass --host with the receiver's LAN IP instead."
        ) from exc
    sock.settimeout(0.2)
    found: dict[tuple[str, int], str] = {}
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(256)
            except socket.timeout:
                continue
            decoded = decode_beacon(data)
            if decoded is None:
                continue
            stream_port, name = decoded
            found[(addr[0], stream_port)] = name or addr[0]
    finally:
        sock.close()
    return [(ip, port, name) for (ip, port), name in sorted(found.items())]


class BeaconSender:
    """Broadcast a once-a-second beacon so a sender can find this receiver."""

    def __init__(self, stream_port: int, discovery_port: int, name: str) -> None:
        self._payload = encode_beacon(stream_port, name)
        self._discovery_port = discovery_port
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audiostream-beacon", daemon=True)
        self.error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop.is_set():
                try:
                    sock.sendto(self._payload, ("255.255.255.255", self._discovery_port))
                except OSError as exc:
                    self.error = str(exc)
                self._stop.wait(1.0)
        finally:
            sock.close()
