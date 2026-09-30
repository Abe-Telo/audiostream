import socket
import time

from audiostream.net import encode_beacon
from audiostream.presence import (
    JoinListener,
    PresenceService,
    ReceiverWatch,
    SenderBeacon,
    SenderJoiner,
    decode_join,
    decode_presence,
    decode_sender_beacon,
    encode_join,
    encode_presence,
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


def test_presence_hello_roundtrip():
    raw = encode_presence({"kind": "hello", "name": "Kitchen", "port": 45123})
    assert decode_presence(raw) == {"kind": "hello", "name": "Kitchen", "port": 45123}
    assert decode_presence(b"nope") is None


def test_a_shared_computer_shows_up_on_the_other_roster():
    roster = Roster(None)
    service = PresenceService(roster, "Office", port=0, own_ip="10.0.0.1")
    message = {
        "kind": "share",
        "id": "abc",
        "op": "add",
        "ip": "192.168.1.198",
        "port": 45123,
        "name": "Kitchen",
        "volume": 40,
    }
    service._handle(message, ("192.168.1.20", 45127))
    row = roster.snapshot()[0]
    assert row["name"] == "Kitchen"
    assert row["volume"] == 40
    service._handle(message, ("192.168.1.20", 45127))
    assert len(roster.snapshot()) == 1
    service._handle(
        {"kind": "share", "id": "self", "op": "add", "ip": "10.0.0.1", "port": 45123, "name": "Office", "volume": 100},
        ("192.168.1.20", 45127),
    )
    assert [row["ip"] for row in roster.snapshot()] == ["192.168.1.198"]


def test_probe_finds_a_computer_running_audiostream():
    office = PresenceService(Roster(None), "Office", port=0, own_ip="10.1.0.1")
    kitchen = PresenceService(Roster(None), "Kitchen", port=0, own_ip="10.1.0.2")
    office.start()
    kitchen.start()
    try:
        assert office.error is None
        assert kitchen.error is None
        office.probe(("127.0.0.1", kitchen.bound_port))
        _wait(lambda: any(peer["name"] == "Kitchen" for peer in office.peers()))
    finally:
        office.stop()
        kitchen.stop()


def test_adding_here_shows_up_on_the_other_computer():
    here_roster = Roster(None)
    there_roster = Roster(None)
    here = PresenceService(here_roster, "Office", port=0, own_ip="10.2.0.1")
    there = PresenceService(there_roster, "Den", port=0, own_ip="10.2.0.2")
    here.start()
    there.start()
    try:
        here._announce_addr = ("127.0.0.1", there.bound_port)
        there._announce_addr = ("127.0.0.1", here.bound_port)
        here._published.clear()
        here_roster.upsert("192.168.1.50", 45123, "Kitchen", "manual")
        here.publish_local_changes()
        _wait(lambda: any(row["name"] == "Kitchen" for row in there_roster.snapshot()))
        here.publish_volume("192.168.1.50", 45123, 25)
        _wait(lambda: any(row["volume"] == 25 for row in there_roster.snapshot()))
        here_roster.remove("192.168.1.50", 45123)
        here.publish_local_changes()
        _wait(lambda: there_roster.snapshot() == [])
    finally:
        here.stop()
        there.stop()


def _wait(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")
