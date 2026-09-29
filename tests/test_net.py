"""Beacon encode/decode and localhost UDP delivery."""

import socket
import threading
import time

from audiostream.net import decode_beacon, discover_receivers, encode_beacon
from audiostream.packet import decode_packet, encode_packet


def test_beacon_roundtrip():
    raw = encode_beacon(45123, "living-room")
    assert decode_beacon(raw) == (45123, "living-room")
    assert decode_beacon(b"nope") is None
    assert decode_beacon(raw[:6]) is None


def test_discover_receives_a_beacon():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    def send() -> None:
        time.sleep(0.05)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(encode_beacon(45123, "pc2"), ("127.0.0.1", port))
        finally:
            sock.close()

    thread = threading.Thread(target=send)
    thread.start()
    found = discover_receivers(port, 1.0)
    thread.join()
    assert ( "127.0.0.1", 45123, "pc2") in found


def test_audio_packet_over_udp_localhost():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    port = receiver.getsockname()[1]
    pcm = b"\x10\x20" * 32
    packet = encode_packet(3, pcm, 48000, 1)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(packet, ("127.0.0.1", port))
        receiver.settimeout(1.0)
        data, _addr = receiver.recvfrom(4096)
    finally:
        sender.close()
        receiver.close()
    decoded = decode_packet(data)
    assert decoded.sequence == 3
    assert decoded.pcm == pcm
