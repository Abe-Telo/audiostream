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
    volume: int = 100
    name_locked: bool = False
    speaker_ids: tuple[str, ...] = ()


class Roster:
    """Thread-safe destination list. The same IP and port is one computer."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._items: dict[tuple[str, int], Destination] = {}
        self.last_event = ""
        self.master_volume = 100
        self.playback: list[str] = ["default"]

    def load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.master_volume = clamp_volume(raw.get("volume", 100))
        playback = raw.get("playback")
        if isinstance(playback, list) and playback:
            self.playback = [str(item) for item in playback][:16]
        for item in raw.get("devices", []):
            try:
                self.upsert(
                    str(item["ip"]),
                    int(item["port"]),
                    str(item.get("name") or ""),
                    str(item.get("source") or "manual"),
                    persist=False,
                    announce=False,
                    volume=item.get("volume", 100),
                    name_locked=bool(item.get("name_locked", False)),
                    speaker_ids=item.get("speakers") or (),
                )
            except (KeyError, TypeError, ValueError):
                continue

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            payload = {
                "volume": self.master_volume,
                "playback": list(self.playback),
                "devices": [
                    {
                        "ip": item.ip,
                        "port": item.port,
                        "name": item.name,
                        "source": item.source,
                        "volume": item.volume,
                        "name_locked": item.name_locked,
                        "speakers": list(item.speaker_ids),
                    }
                    for item in self._items.values()
                ],
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
        volume: int | None = None,
        name_locked: bool | None = None,
        speaker_ids=(),
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
                existing = Destination(
                    ip=ip,
                    port=port,
                    name=label,
                    source=source,
                    last_seen=now,
                    volume=clamp_volume(100 if volume is None else volume),
                    name_locked=bool(name_locked),
                    speaker_ids=_speaker_ids(speaker_ids),
                )
                self._items[key] = existing
                created = True
            else:
                existing.last_seen = now
                existing.send_error = ""
                if volume is not None and source == "manual":
                    existing.volume = clamp_volume(volume)
                if name_locked:
                    existing.name_locked = True
                if source == "manual":
                    existing.name = label
                    existing.source = "manual"
                elif (
                    not existing.name_locked
                    and label != ip
                    and (existing.source == "network" or existing.name == existing.ip)
                ):
                    existing.name = label
            if announce and created and source == "network":
                self.last_event = f"{existing.name} ({existing.ip}) added itself."
            elif announce and created:
                self.last_event = f"Added {existing.name}."
            snapshot = existing
        if persist and (created or source == "manual"):
            self.save()
        return snapshot

    def rename(self, ip: str, port: int, name: str) -> None:
        label = (name or "").strip()[:64]
        if not label:
            raise ValueError("Enter a name.")
        with self._lock:
            item = self._items.get((ip, port))
            if item is None:
                raise ValueError("That computer is no longer in the list.")
            item.name = label
            item.name_locked = True
            self.last_event = f"Renamed to {label}."
        self.save()

    def set_volume(self, ip: str, port: int, volume: int) -> None:
        level = clamp_volume(volume)
        with self._lock:
            item = self._items.get((ip, port))
            if item is not None:
                item.volume = level

    def set_speakers(self, ip: str, port: int, speaker_ids) -> None:
        ids = _speaker_ids(speaker_ids) or ("default",)
        with self._lock:
            item = self._items.get((ip, port))
            if item is not None:
                item.speaker_ids = ids

    def set_playback(self, speaker_ids) -> None:
        ids = [item for item in _speaker_ids(speaker_ids)]
        with self._lock:
            self.playback = ids or ["default"]

    def set_master_volume(self, volume: int) -> None:
        with self._lock:
            self.master_volume = clamp_volume(volume)

    def master_volume_value(self) -> int:
        with self._lock:
            return self.master_volume

    def drop_network(self, ip: str, port: int) -> bool:
        """Remove a computer that was found on the network. A typed-in computer stays."""
        with self._lock:
            item = self._items.get((ip, port))
            if item is None or item.source != "network":
                return False
            self._items.pop((ip, port), None)
            self.last_event = f"Removed {ip}."
        self.save()
        return True

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
                    volume=item.volume,
                    name_locked=item.name_locked,
                    speaker_ids=item.speaker_ids,
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
                    "volume": item.volume,
                    "speakers": list(item.speaker_ids) or ["default"],
                }
            )
        rows.sort(key=lambda row: (row["name"].lower(), row["ip"], row["port"]))
        return rows


def default_pc2_roster_path() -> Path:
    return default_roster_path().with_name("pc2-devices.json")


def default_roster_path() -> Path:
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "audiostream" / "pc1-devices.json"
    return Path.home() / ".config" / "audiostream" / "pc1-devices.json"


def _speaker_ids(value) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    ids = []
    for item in value:
        text = str(item).strip()
        if text and text not in ids:
            ids.append(text[:32])
    return tuple(ids[:16])


def clamp_volume(value) -> int:
    try:
        level = int(round(float(value)))
    except (TypeError, ValueError):
        return 100
    if level < 0:
        return 0
    if level > 100:
        return 100
    return level


def _ipv4(host: str) -> str:
    text = host.strip()
    if not text:
        raise ValueError("Enter the other computer's IP address.")
    try:
        return resolve_ipv4(text)
    except Exception as exc:
        raise ValueError(f"Could not use {text!r} as an IP address. {exc}") from exc
