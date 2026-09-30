"""Write audiostream/icon.ico. Run once when the icon art changes."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _pixel(x: int, y: int, size: int) -> tuple[int, int, int, int]:
    # Windows-blue rounded square with a white speaker mark.
    margin = max(1, size // 16)
    if x < margin or y < margin or x >= size - margin or y >= size - margin:
        return (0, 0, 0, 0)
    blue = (15, 108, 189, 255)
    cx, cy = size / 2, size / 2
    # Body of the speaker.
    body_left = size * 0.28
    body_right = size * 0.46
    body_top = size * 0.36
    body_bottom = size * 0.64
    if body_left <= x <= body_right and body_top <= y <= body_bottom:
        return (255, 255, 255, 255)
    # Cone to the right of the body.
    rel_x = (x - body_right) / (size * 0.22)
    if 0 <= rel_x <= 1:
        half = (0.10 + 0.16 * rel_x) * size
        if abs(y - cy) <= half:
            return (255, 255, 255, 255)
    return blue


def _png(size: int) -> bytes:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            raw.extend(_pixel(x, y, size))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def _ico(pngs: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = bytearray()
    images = bytearray()
    offset = 6 + 16 * len(pngs)
    for size, png in pngs:
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0, 0, 0, 1, 32, len(png), offset)
        images += png
        offset += len(png)
    return header + bytes(entries) + bytes(images)


def main() -> None:
    pngs = [(size, _png(size)) for size in (16, 32, 48)]
    path = Path(__file__).resolve().parent / "icon.ico"
    path.write_bytes(_ico(pngs))
    print(path, path.stat().st_size)


if __name__ == "__main__":
    main()
