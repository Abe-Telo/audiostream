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


def beeps_s16le(sample_rate: int = 48000, channels: int = 2) -> bytes:
    """Two short beeps, interleaved s16le. Used as the PC2 test tone."""
    import math

    if sample_rate < 1 or channels < 1:
        raise ValueError("sample rate and channels must be positive")
    tone = int(sample_rate * 0.15)
    gap = int(sample_rate * 0.1)
    pieces = []
    for _beep in range(2):
        for index in range(tone):
            value = int(math.sin(2 * math.pi * 880 * index / sample_rate) * 16000)
            pieces.extend([value] * channels)
        pieces.extend([0] * (gap * channels))
    samples = array.array("h", pieces)
    return samples.tobytes()


def fade_s16le(pcm: bytes, gain: float) -> bytes:
    """Scale interleaved s16le samples. Used to hide a lost packet without a click."""
    usable = len(pcm) - (len(pcm) % 2)
    if usable <= 0:
        return b""
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    faded = array.array("h", (max(-32768, min(32767, int(sample * gain))) for sample in samples))
    return faded.tobytes()


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
