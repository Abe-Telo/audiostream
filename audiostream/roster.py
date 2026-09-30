"""The list of computers PC1 sends audio to."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from audiostream.net import check_port, resolve_ipv4


@dataclass
class Destination:
    ip: str
    port: int
    name: str
    source: str  # "manual" or "network"
    last_seen: float = 0.0
    send_error: str = ""


class Roster:
    """Thread-safe destination list. The same IP and port is one computer."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[tuple[str, int], Destination] = {}
        self.last_event = ""

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("devices", []):
            try:
                self.upsert(
                    str(item["ip"]),
                    int(item["port"]),
                    str(item.get("name") or ""),
                    str(item.get("source") or "manual"),
                    persist=False,
                    announce=False,
                )
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            payload = {
                "devices": [
                    {
                        "ip": item.ip,
                        "port": item.port,
                        "name": item.name,
                        "source": item.source,
                    }
                    for item in self._items.values()
                ]
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def upsert(
        self,
        host: str,
        port: int,
        name: str,
        source: str,
        persist: bool = True,
        announce: bool = True,
    ) -> Destination:
        check_port(port, "UDP port")
        ip = _ipv4(host)
        if ip == "255.255.255.255":
            raise ValueError("Enter that computer's own IP address, not the broadcast address.")
        label = (name or "").strip()[:64] or ip
        source = "manual" if source == "manual" else "network"
        created = False
        with self._lock:
            key = (ip, port)
            existing = self._items.get(key)
            now = time.time()
            if existing is None:
                existing = Destination(ip=ip, port=port, name=label, source=source, last_seen=now)
                self._items[key] = existing
                created = True
            else:
                existing.last_seen = now
                existing.send_error = ""
                if source == "manual":
                    existing.name = label
                    existing.source = "manual"
                elif label != ip and (existing.source == "network" or existing.name == existing.ip):
                    existing.name = label
            if announce and created and source == "network":
                self.last_event = f"{existing.name} ({existing.ip}) added itself."
            elif announce and created:
                self.last_event = f"Added {existing.name}."
            snapshot = existing
        if persist and (created or source == "manual"):
            self.save()
        return snapshot

    def remove(self, ip: str, port: int) -> None:
        with self._lock:
            self._items.pop((ip, port), None)
            self.last_event = f"Removed {ip}."
        self.save()

    def mark_send_error(self, ip: str, port: int, message: str) -> None:
        with self._lock:
            item = self._items.get((ip, port))
            if item is not None:
                item.send_error = message

    def clear_send_error(self, ip: str, port: int) -> None:
        with self._lock:
            item = self._items.get((ip, port))
            if item is not None:
                item.send_error = ""

    def destinations(self) -> list[Destination]:
        with self._lock:
            return [
                Destination(
                    ip=item.ip,
                    port=item.port,
                    name=item.name,
                    source=item.source,
                    last_seen=item.last_seen,
                    send_error=item.send_error,
                )
                for item in self._items.values()
            ]

    def snapshot(self) -> list[dict]:
        now = time.time()
        rows = []
        for item in self.destinations():
            online = item.last_seen > 0 and (now - item.last_seen) < 8
            rows.append(
                {
                    "ip": item.ip,
                    "port": item.port,
                    "name": item.name,
                    "source": item.source,
                    "online": online,
                    "send_error": item.send_error,
                }
            )
        rows.sort(key=lambda row: (row["name"].lower(), row["ip"], row["port"]))
        return rows


def default_roster_path() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "audiostream" / "pc1-devices.json"
    return Path.home() / ".config" / "audiostream" / "pc1-devices.json"


def _ipv4(host: str) -> str:
    text = host.strip()
    if not text:
        raise ValueError("Enter the other computer's IP address.")
    try:
        return resolve_ipv4(text)
    except Exception as exc:
        raise ValueError(f"Could not use {text!r} as an IP address. {exc}") from exc
