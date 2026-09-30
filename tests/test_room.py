import time

import pytest

from audiostream.packet import JitterBuffer, repair_copies
from audiostream.pcm import fade_s16le
from audiostream.room import (
    active_sender,
    decode_message,
    encode_message,
    should_yield,
    takeover_name,
)


def test_room_messages_roundtrip():
    raw = encode_message({"t": "hello", "name": "Kitchen", "sending": True, "epoch": 5, "port": 45123})
    assert decode_message(raw)["name"] == "Kitchen"
    assert decode_message(b"nope") is None


def test_only_the_newest_sender_is_active():
    now = time.time()
    peers = [
        {"name": "Office", "sending": True, "epoch": 10, "last_seen": now},
        {"name": "Kitchen", "sending": True, "epoch": 12, "last_seen": now},
        {"name": "Den", "sending": False, "epoch": 99, "last_seen": now},
    ]
    assert active_sender(peers, now)["name"] == "Kitchen"
    assert takeover_name(peers, "Office", now) == "Kitchen"
    assert takeover_name(peers, "Kitchen", now) is None
    assert should_yield("Office", 10, "Kitchen", 12, True)
    assert not should_yield("Kitchen", 12, "Office", 10, True)


def test_a_newer_sender_takes_over():
    from audiostream.room import Room

    room = Room("Office")
    room.choose("sender", epoch=10)
    result = room.note(
        "192.168.1.50",
        {"t": "hello", "name": "Kitchen", "sending": True, "epoch": 11, "port": 45123},
    )
    assert result == "yield"
    assert room.role == "receiver"
    assert "Kitchen" in room.last_event


def test_room_window_can_play():
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()
    from audiostream.room_app import RoomApp

    app = RoomApp(role="receiver", start_network=False)
    try:
        app.root.update()
        assert app.room.role == "receiver"
        assert app.root.title() == "Audiostream Room"
    finally:
        app.quit()


def test_a_quiet_sender_is_not_current():
    peers = [{"name": "Office", "sending": True, "epoch": 10, "last_seen": time.time() - 30}]
    assert active_sender(peers, time.time()) is None


def test_repair_copies_repeat_an_earlier_packet():
    history = []
    sent = []
    for number in range(10):
        sent.append(repair_copies(history, bytes([number])))
    assert sent[0] == [bytes([0])]
    assert bytes([0]) in sent[4]
    assert sent[9][0] == bytes([9])
    assert bytes([5]) in sent[9]
    assert bytes([1]) in sent[9]


def test_a_lost_packet_is_filled_with_quiet_audio_not_a_click():
    frame = b"\x10\x00" * 48
    jb = JitterBuffer(48000, 1, 1, 48, conceal=True)
    jb.push(_packet(0, frame))
    assert jb.pull(48) == frame
    jb.push(_packet(2, frame))
    filled = jb.pull(48)
    assert filled != b"\x00" * 96
    assert max(abs(int.from_bytes(filled[i : i + 2], "little", signed=True)) for i in range(0, len(filled), 2)) < 16
    assert fade_s16le(frame, 0.5) != frame


def _packet(sequence: int, pcm: bytes):
    from audiostream.packet import Packet

    return Packet(sequence=sequence, sample_rate=48000, channels=1, pcm=pcm)
