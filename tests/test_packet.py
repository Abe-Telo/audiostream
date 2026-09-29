"""Packet encode/decode and jitter-buffer sequence handling."""

from audiostream.packet import (
    JitterBuffer,
    Packet,
    PacketError,
    decode_packet,
    encode_packet,
    frames_per_packet,
    seq_delta,
)


def _packet(sequence: int, payload: bytes, sample_rate: int = 48000, channels: int = 1) -> Packet:
    return Packet(sequence=sequence, sample_rate=sample_rate, channels=channels, pcm=payload)


def _pcm(sequence_byte: int, frames: int = 48) -> bytes:
    return bytes([sequence_byte, 0]) * frames


def _buffer() -> JitterBuffer:
    # 48 frames at 48 kHz is 1 ms, which is also the prebuffer.
    return JitterBuffer(sample_rate=48000, channels=1, buffer_ms=1, frames_per_packet=48)


def test_encode_decode_roundtrip():
    pcm = b"\x01\x02\x03\x04" * 30
    raw = encode_packet(7, pcm, 48000, 2)
    packet = decode_packet(raw)
    assert packet.sequence == 7
    assert packet.sample_rate == 48000
    assert packet.channels == 2
    assert packet.frame_count == 30
    assert packet.pcm == pcm


def test_sequence_number_wraps_in_header():
    raw = encode_packet(0x1_0000_0005, b"\x00\x01", 48000, 1)
    assert decode_packet(raw).sequence == 5


def test_decode_rejects_bad_packets():
    good = encode_packet(1, b"\x00\x01\x00\x02", 48000, 2)
    try:
        decode_packet(b"nope")
        raise AssertionError("short packet should fail")
    except PacketError:
        pass
    try:
        decode_packet(b"XXXX" + good[4:])
        raise AssertionError("bad magic should fail")
    except PacketError:
        pass
    try:
        decode_packet(good + b"\x00")
        raise AssertionError("trailing byte should fail")
    except PacketError:
        pass
    try:
        decode_packet(good[:-1])
        raise AssertionError("truncated payload should fail")
    except PacketError:
        pass


def test_frames_per_packet_stays_under_mtu():
    assert frames_per_packet(48000, 5, 2) == 240
    assert frames_per_packet(48000, 10, 2) == 350
    assert frames_per_packet(48000, 10, 2) * 2 * 2 <= 1400


def test_seq_delta_across_wrap():
    assert seq_delta(0, 0xFFFFFFFF) == 1
    assert seq_delta(0xFFFFFFFF, 0) == -1
    assert seq_delta(5, 3) == 2
    assert seq_delta(3, 5) == -2


def test_reorder_then_play_in_order():
    jb = _buffer()
    jb.push(_packet(2, _pcm(2)))
    jb.push(_packet(0, _pcm(0)))
    jb.push(_packet(1, _pcm(1)))
    assert jb.pull(48) == _pcm(0)
    assert jb.pull(48) == _pcm(1)
    assert jb.pull(48) == _pcm(2)


def test_late_packet_is_dropped():
    jb = _buffer()
    jb.push(_packet(0, _pcm(0)))
    assert jb.pull(48) == _pcm(0)
    assert jb.push(_packet(0, _pcm(0))) == "late"
    assert jb.late_drops == 1
    assert jb.pull(48) is None or jb.primed is False


def test_gap_is_silence_and_playback_continues():
    jb = _buffer()
    jb.push(_packet(0, _pcm(1)))
    assert jb.pull(48) == _pcm(1)
    jb.push(_packet(2, _pcm(2)))
    assert jb.pull(48) == b"\x00" * 96
    assert jb.missing == 1
    assert jb.pull(48) == _pcm(2)


def test_underrun_replays_when_audio_returns():
    jb = _buffer()
    jb.push(_packet(0, _pcm(1)))
    assert jb.pull(48) == _pcm(1)
    assert jb.pull(48) == b"\x00" * 96
    assert jb.underruns == 1
    assert jb.primed is False
    jb.push(_packet(1, _pcm(2)))
    assert jb.pull(48) == _pcm(2)


def test_large_sequence_jump_resyncs():
    jb = _buffer()
    jb.push(_packet(0, _pcm(1)))
    assert jb.pull(48) == _pcm(1)
    assert jb.push(_packet(100_000, _pcm(9))) == "resync"
    assert jb.resyncs == 1
    assert jb.pull(48) == _pcm(9)


def test_sequence_wrap_stays_ordered():
    jb = _buffer()
    jb.push(_packet(0xFFFFFFFF, _pcm(1)))
    jb.push(_packet(0, _pcm(2)))
    assert jb.pull(48) == _pcm(1)
    assert jb.pull(48) == _pcm(2)


def test_duplicate_packet_is_ignored():
    jb = _buffer()
    assert jb.push(_packet(0, _pcm(1))) == "ok"
    assert jb.push(_packet(0, _pcm(1))) == "dup"
    assert jb.pull(48) == _pcm(1)


def test_pull_splits_a_packet_and_keeps_the_tail():
    jb = _buffer()
    pcm = _pcm(4)
    jb.push(_packet(0, pcm))
    assert jb.pull(20) == pcm[:40]
    assert jb.pull(28) == pcm[40:]


def test_format_mismatch_is_rejected():
    jb = _buffer()
    other = _packet(0, b"\x00\x00" * 48, sample_rate=44100)
    assert jb.push(other) == "format"
    assert jb.buffered_frames() == 0
