import socket

from audiostream.hub import device_argument, send_pcm_to_roster
from audiostream.packet import decode_packet
from audiostream.roster import Roster


def test_device_argument():
    assert device_argument("Default playback") is None
    assert device_argument("") is None
    assert device_argument("1  Speakers") == "1"


def test_one_capture_goes_to_every_computer():
    roster = Roster(None)
    sockets = []
    try:
        for name in ("Living room", "Kitchen"):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            sock.settimeout(1.0)
            sockets.append(sock)
            roster.upsert("127.0.0.1", sock.getsockname()[1], name, "manual")
        pcm = b"\x01\x00\x02\x00" * 8
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sent = send_pcm_to_roster(sender, pcm, roster, 48000, 2, 7)
        finally:
            sender.close()
        assert sent == 2
        for sock in sockets:
            data, _addr = sock.recvfrom(4096)
            packet = decode_packet(data)
            assert packet.sequence == 7
            assert packet.pcm == pcm
    finally:
        for sock in sockets:
            sock.close()
