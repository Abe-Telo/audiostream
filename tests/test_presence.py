import socket
import time

from audiostream.net import encode_beacon
from audiostream.presence import (
    JoinListener,
    ReceiverWatch,
    SenderBeacon,
    SenderJoiner,
    decode_join,
    decode_sender_beacon,
    encode_join,
    encode_sender_beacon,
)
from audiostream.roster import Roster


def test_join_and_sender_beacons_roundtrip():
    raw = encode_join(45123, "pc2")
    assert decode_join(raw) == (45123, "pc2")
    assert decode_sender_beacon(raw) is None
    beacon = encode_sender_beacon(45125, "pc1")
    assert decode_sender_beacon(beacon) == (45125, "pc1")
    assert decode_join(beacon) is None


def test_receiver_beacon_adds_a_computer():
    roster = Roster(None)
    watch = ReceiverWatch(roster, port=0, ignore_ip="10.255.255.1")
    watch.start()
    assert watch.error is None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(encode_beacon(45123, "Living room"), ("127.0.0.1", watch.bound_port))
        _wait(lambda: any(row["name"] == "Living room" for row in roster.snapshot()))
    finally:
        sock.close()
        watch.stop()
    row = roster.snapshot()[0]
    assert row["ip"] == "127.0.0.1"
    assert row["port"] == 45123
    assert row["source"] == "network"


def test_pc2_join_adds_itself_to_pc1():
    roster = Roster(None)
    listener = JoinListener(roster, port=0)
    listener.start()
    assert listener.error is None
    joiner = SenderJoiner(45123, "Kitchen", beacon_port=0)
    joiner.start()
    assert joiner.error is None
    beacon = SenderBeacon(
        "Office",
        control_port=listener.bound_port,
        beacon_port=joiner.bound_port,
        destination="127.0.0.1",
    )
    beacon.start()
    try:
        _wait(lambda: any(row["name"] == "Kitchen" for row in roster.snapshot()))
    finally:
        beacon.stop()
        joiner.stop()
        listener.stop()
    row = roster.snapshot()[0]
    assert row["ip"] == "127.0.0.1"
    assert row["port"] == 45123


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")
