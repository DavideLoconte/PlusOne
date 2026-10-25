"""The app display's packets: their layout, sealed both ways, replays refused."""
import socket
import struct

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from plusone import appdisplay  # noqa: E402

KEY = bytes(range(32))


def channel_to(peer):
    channel = appdisplay.Channel(KEY)
    channel.app = peer.getsockname()
    return channel


def test_a_video_packet_as_the_app_reads_it():
    app = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app.bind(("127.0.0.1", 0))
    app.settimeout(2)
    channel = channel_to(app)
    try:
        body = appdisplay.VIDEO_HEADER.pack(appdisplay.VIDEO, 1, 7, 0, 3, 123456789) + b"h264"
        channel.send(body)
        channel.send(body)
        first, second = app.recv(2048), app.recv(2048)
    finally:
        channel.close()
        app.close()
    # DisplaySession.kt: nonce = direction (4 bytes) and count (8), then the sealed body.
    assert struct.unpack("!IQ", first[:12]) == (appdisplay.FROM_PC, 0)
    assert struct.unpack("!IQ", second[:12]) == (appdisplay.FROM_PC, 1)  # never the same nonce twice
    assert AESGCM(KEY).decrypt(first[:12], first[12:], None) == body
    assert len(appdisplay.VIDEO_HEADER.pack(1, 0, 0, 0, 1, 0)) + appdisplay.PAYLOAD + 12 + 16 <= 1472


def test_only_the_apps_fresh_packets_are_taken():
    app = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app.bind(("127.0.0.1", 0))
    channel = channel_to(app)
    pc = ("127.0.0.1", channel.port)

    def from_app(count, body, key=KEY, direction=appdisplay.FROM_APP):
        nonce = struct.pack("!IQ", direction, count)
        app.sendto(nonce + AESGCM(key).encrypt(nonce, body, None), pc)

    try:
        from_app(5, b"\x02")
        assert channel.receive(1) == b"\x02"
        from_app(5, b"\x02")                       # replayed
        from_app(4, b"\x02")                       # older
        from_app(6, b"\x02", key=bytes(32))        # another key
        from_app(7, b"\x02", direction=appdisplay.FROM_PC)  # its own packets sent back
        assert channel.receive(0.3) is None
        assert channel.receive(0.3) is None
        assert channel.receive(0.3) is None
        assert channel.receive(0.3) is None
        from_app(8, b"\x03" + bytes(8))
        assert channel.receive(1) == b"\x03" + bytes(8)
    finally:
        channel.close()
        app.close()
