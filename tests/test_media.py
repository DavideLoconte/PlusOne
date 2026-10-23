"""The streaming pipeline produces MPEG-TS in RTP (payload type 33)."""
import socket
import time

import gi
import pytest

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from plusone import media  # noqa: E402

Gst.init(None)


def test_pick_encoder_finds_one():
    assert media.pick_encoder("auto") in media.ENCODERS


@pytest.mark.skipif(not Gst.ElementFactory.find("x264enc"), reason="x264enc not installed")
def test_test_pattern_reaches_udp():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(5)
    streamer = media.Streamer("test", "127.0.0.1", 320, 200, 30, 1000, "x264",
                              port=rx.getsockname()[1], bind_port=0, preview=False)
    streamer.start()
    try:
        packets = []
        deadline = time.time() + 5
        while len(packets) < 20 and time.time() < deadline:
            packets.append(rx.recv(2048))
        streamer.force_keyframe()
    finally:
        streamer.stop()
        rx.close()
    assert len(packets) >= 20
    for pkt in packets:
        assert pkt[0] >> 6 == 2              # RTP version 2
        assert pkt[1] & 0x7F == 33           # MP2T payload type
        assert pkt[12] == 0x47               # MPEG-TS sync byte right after the header
        assert (len(pkt) - 12) % 188 == 0    # whole TS packets
