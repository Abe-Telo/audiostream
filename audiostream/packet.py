"""UDP packet format and jitter buffer.

Audio I/O does not live here, so this module can be tested without a sound card.
Wire format is PCM signed 16-bit little-endian, with a big-endian header.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"AS01"
VERSION = 1
SAMPLE_BYTES = 2
HEADER = struct.Struct("!4sBBHIII")
HEADER_SIZE = HEADER.size  # 20 bytes
MAX_PAYLOAD_BYTES = 1400
SEQ_MOD = 0x100000000
SEQ_HALF = 0x80000000


class PacketError(ValueError):
    """A datagram is not a valid audio packet."""


@dataclass(frozen=True)
class Packet:
    sequence: int
    sample_rate: int
    channels: int
    pcm: bytes

    @property
    def frame_count(self) -> int:
        return len(self.pcm) // (self.channels * SAMPLE_BYTES)


def seq_delta(a: int, b: int) -> int:
    """Signed distance ``a - b`` in uint32 sequence space."""
    delta = (a - b) & 0xFFFFFFFF
    if delta >= SEQ_HALF:
        delta -= SEQ_MOD
    return delta


def frames_per_packet(sample_rate: int, chunk_ms: int, channels: int) -> int:
    """How many frames to put in one datagram.

    Caps the payload so a packet stays under a typical 1500-byte LAN MTU.
    """
    if sample_rate < 1 or chunk_ms < 1 or channels < 1:
        raise ValueError("sample rate, chunk length, and channels must be positive")
    requested = max(1, sample_rate * chunk_ms // 1000)
    max_frames = max(1, MAX_PAYLOAD_BYTES // (channels * SAMPLE_BYTES))
    return min(requested, max_frames)


def encode_packet(sequence: int, pcm: bytes, sample_rate: int, channels: int) -> bytes:
    if channels < 1 or channels > 8:
        raise ValueError(f"channels must be 1..8, got {channels}")
    if sample_rate < 1:
        raise ValueError("sample rate must be positive")
    frame_bytes = channels * SAMPLE_BYTES
    if len(pcm) % frame_bytes != 0:
        raise ValueError("pcm length is not a whole number of frames")
    frame_count = len(pcm) // frame_bytes
    if frame_count < 1:
        raise ValueError("packet must contain at least one frame")
    header = HEADER.pack(
        MAGIC,
        VERSION,
        channels,
        0,
        sample_rate,
        sequence & 0xFFFFFFFF,
        frame_count,
    )
    return header + pcm


def decode_packet(data: bytes) -> Packet:
    if len(data) < HEADER_SIZE:
        raise PacketError(f"packet too short ({len(data)} bytes)")
    magic, version, channels, _flags, sample_rate, sequence, frame_count = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise PacketError("not an audiostream packet")
    if version != VERSION:
        raise PacketError(f"unsupported packet version {version}")
    if channels < 1 or channels > 8:
        raise PacketError(f"invalid channel count {channels}")
    if sample_rate < 8000 or sample_rate > 192000:
        raise PacketError(f"invalid sample rate {sample_rate}")
    if frame_count < 1 or frame_count > sample_rate:
        raise PacketError(f"invalid frame count {frame_count}")
    pcm = data[HEADER_SIZE:]
    expected = frame_count * channels * SAMPLE_BYTES
    if len(pcm) != expected:
        raise PacketError(f"payload length {len(pcm)} does not match {expected} bytes")
    return Packet(sequence=sequence, sample_rate=sample_rate, channels=channels, pcm=pcm)


class JitterBuffer:
    """Reorder a short window of packets and drop anything that arrives late.

    Playback does not block waiting for a missing packet. A hole with audio
    already behind it is filled with silence. If the buffer runs dry, pull
    returns silence for that request and waits until ``buffer_ms`` of audio
    is contiguous again.
    """

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        buffer_ms: int,
        frames_per_packet: int,
    ) -> None:
        if sample_rate < 1 or channels < 1 or frames_per_packet < 1:
            raise ValueError("sample rate, channels, and frames_per_packet must be positive")
        if buffer_ms < 0:
            raise ValueError("buffer_ms must be >= 0")
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_per_frame = channels * SAMPLE_BYTES
        self.frames_per_packet = frames_per_packet
        self.target_frames = max(frames_per_packet, sample_rate * buffer_ms // 1000)
        # About two seconds. Farther than this means the sender restarted.
        self.max_ahead_packets = max(8, (sample_rate * 2) // frames_per_packet)
        self._packets: dict[int, bytes] = {}
        self._next: int | None = None
        self._carry = b""
        self.primed = False
        self.received = 0
        self.late_drops = 0
        self.missing = 0
        self.underruns = 0
        self.resyncs = 0

    def push(self, packet: Packet) -> str:
        if packet.sample_rate != self.sample_rate or packet.channels != self.channels:
            return "format"
        if packet.frame_count < 1:
            return "empty"
        self.received += 1
        self.frames_per_packet = packet.frame_count
        if self._next is not None:
            delta = seq_delta(packet.sequence, self._next)
            if delta < 0:
                if delta < -self.max_ahead_packets:
                    self._hard_reset(packet)
                    return "resync"
                self.late_drops += 1
                return "late"
            if delta > self.max_ahead_packets:
                self._hard_reset(packet)
                return "resync"
        elif self._packets:
            earliest = _earliest(list(self._packets))
            if abs(seq_delta(packet.sequence, earliest)) > self.max_ahead_packets:
                self._hard_reset(packet)
                return "resync"
        if packet.sequence in self._packets:
            return "dup"
        self._packets[packet.sequence] = packet.pcm
        return "ok"

    def ready(self) -> bool:
        """True once a contiguous prebuffer is available to play."""
        if self.primed:
            return True
        return self._try_prime()

    def pull(self, num_frames: int) -> bytes | None:
        """Return ``num_frames`` of s16le audio, or None if still prebuffering."""
        if num_frames < 1:
            raise ValueError("num_frames must be positive")
        if not self.primed and not self._try_prime():
            return None
        need = num_frames * self.bytes_per_frame
        out = bytearray()
        if self._carry:
            take = min(len(self._carry), need)
            out += self._carry[:take]
            self._carry = self._carry[take:]
        spins = 0
        while len(out) < need:
            spins += 1
            if spins > 100000:
                raise RuntimeError("jitter buffer failed to fill a pull")
            packet = self._packets.pop(self._next, None) if self._next is not None else None
            if packet is None:
                assert self._next is not None
                if self._has_later(self._next):
                    packet = b"\x00" * (self.frames_per_packet * self.bytes_per_frame)
                    self.missing += 1
                    self._next = (self._next + 1) & 0xFFFFFFFF
                else:
                    self.underruns += 1
                    self.primed = False
                    out.extend(b"\x00" * (need - len(out)))
                    return bytes(out)
            else:
                self._next = (self._next + 1) & 0xFFFFFFFF
            out += packet
        if len(out) > need:
            self._carry = bytes(out[need:])
            del out[need:]
        return bytes(out)

    def buffered_frames(self) -> int:
        carry = len(self._carry) // self.bytes_per_frame
        if self._next is not None and self._next in self._packets:
            return carry + self._contiguous_frames(self._next)
        if self._packets:
            start = self._next if self._next is not None else _earliest(list(self._packets))
            if start in self._packets:
                return carry + self._contiguous_frames(start)
        return carry

    def _try_prime(self) -> bool:
        if (
            self._next is not None
            and self._next in self._packets
            and self._contiguous_frames(self._next) >= self.target_frames
        ):
            self.primed = True
            return True
        start = self._best_start(self._next)
        if start is None:
            return False
        if self._next is not None and seq_delta(start, self._next) > 0:
            self.missing += seq_delta(start, self._next)
        self._discard_before(start)
        self._next = start
        self.primed = True
        return True

    def _best_start(self, after: int | None) -> int | None:
        present: set[int] = set()
        for seq in self._packets:
            if after is not None and seq_delta(seq, after) < 0:
                continue
            present.add(seq)
        best: int | None = None
        for seq in present:
            prev = (seq - 1) & 0xFFFFFFFF
            if prev in present:
                continue
            if self._contiguous_frames(seq) < self.target_frames:
                continue
            if best is None or seq_delta(seq, best) < 0:
                best = seq
        return best

    def _contiguous_frames(self, start: int) -> int:
        total = 0
        seq = start
        guard = 0
        while seq in self._packets and guard < 100000:
            total += len(self._packets[seq]) // self.bytes_per_frame
            seq = (seq + 1) & 0xFFFFFFFF
            guard += 1
        return total

    def _has_later(self, seq: int) -> bool:
        for other in self._packets:
            delta = seq_delta(other, seq)
            if 0 < delta <= self.max_ahead_packets:
                return True
        return False

    def _discard_before(self, start: int) -> None:
        for seq in list(self._packets):
            if seq_delta(seq, start) < 0:
                del self._packets[seq]

    def _hard_reset(self, packet: Packet) -> None:
        self._packets = {packet.sequence: packet.pcm}
        self._next = None
        self._carry = b""
        self.primed = False
        self.frames_per_packet = packet.frame_count
        self.resyncs += 1


def _earliest(seqs: list[int]) -> int:
    base = seqs[0]
    for seq in seqs[1:]:
        if seq_delta(seq, base) < 0:
            base = seq
    return base
