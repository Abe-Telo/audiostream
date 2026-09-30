"""PCM s16le helpers that do not need NumPy."""

from __future__ import annotations

import array


def fit_s16le(pcm: bytes, src_channels: int, dst_channels: int) -> bytes:
    """Change the channel count of interleaved s16le audio."""
    if src_channels < 1 or dst_channels < 1:
        raise ValueError("channel counts must be positive")
    if src_channels == dst_channels:
        return pcm
    usable = len(pcm) - (len(pcm) % (src_channels * 2))
    if usable <= 0:
        return b""
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    frames = len(samples) // src_channels
    out = array.array("h")
    if src_channels > dst_channels:
        for frame in range(frames):
            start = frame * src_channels
            out.extend(samples[start : start + dst_channels])
    else:
        for frame in range(frames):
            start = frame * src_channels
            out.extend(samples[start : start + src_channels])
            out.extend([0] * (dst_channels - src_channels))
    return out.tobytes()


def peak_s16le(pcm: bytes) -> float:
    """Peak absolute sample in the range 0..1."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    if not samples:
        return 0.0
    return max(abs(sample) for sample in samples) / 32768.0
