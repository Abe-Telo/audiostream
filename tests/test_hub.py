import socket

from audiostream.hub import device_argument, send_pcm_to_roster
from audiostream.packet import decode_packet
from audiostream.pcm import scale_s16le
from audiostream.roster import Roster


def test_stop_before_start_is_safe():
    from audiostream.hub import StreamHub
    from audiostream.listen import Listener

    StreamHub(Roster(None)).stop()
    Listener().stop()


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


def test_each_computer_can_be_quieter_than_the_others():
    roster = Roster(None)
    roster.set_master_volume(50)
    loud = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        loud.bind(("127.0.0.1", 0))
        quiet.bind(("127.0.0.1", 0))
        loud.settimeout(1.0)
        quiet.settimeout(1.0)
        roster.upsert("127.0.0.1", loud.getsockname()[1], "Loud", "manual")
        roster.upsert("127.0.0.1", quiet.getsockname()[1], "Quiet", "manual")
        roster.set_volume("127.0.0.1", quiet.getsockname()[1], 50)
        pcm = (1000).to_bytes(2, "little", signed=True) * 8
        send_pcm_to_roster(sender, pcm, roster, 48000, 2, 3)
        loud_pcm = decode_packet(loud.recvfrom(4096)[0]).pcm
        quiet_pcm = decode_packet(quiet.recvfrom(4096)[0]).pcm
    finally:
        loud.close()
        quiet.close()
        sender.close()
    assert loud_pcm == scale_s16le(pcm, 0.5)
    assert quiet_pcm == scale_s16le(pcm, 0.25)
