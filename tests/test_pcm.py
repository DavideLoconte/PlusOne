"""The speakers' sound as it is: 5 ms packets of 16-bit stereo PCM, sealed."""
import json
import socket
import struct

import pytest

crypto = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")

import gi  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from plusone import pcm  # noqa: E402

Gst.init(None)


def test_the_sound_goes_in_whole_5_ms_packets_as_it_is():
    app = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app.bind(("127.0.0.1", 0))
    app.settimeout(3)
    # 2400 frames of a known pattern: exactly ten packets.
    sender = pcm.PcmSender("audiotestsrc wave=ticks num-buffers=10 samplesperbuffer=240 is-live=true")
    offer = json.loads(sender.offer())
    assert offer["rate"] == 48000 and offer["channels"] == 2 and offer["frames"] == 240
    sender.peer = "127.0.0.1"
    sender.answer(json.dumps({"port": app.getsockname()[1]}))
    try:
        packets = [app.recv(2048) for _ in range(10)]
    finally:
        sender.close()
        app.close()
    aead = crypto.AESGCM(bytes.fromhex(offer["key"]))
    numbers = []
    for packet in packets:
        direction, _count = struct.unpack_from("!IQ", packet)
        body = aead.decrypt(packet[:12], packet[12:], None)
        kind, number = pcm.HEADER.unpack_from(body)
        assert direction == 1 and kind == pcm.PCM and len(body) == pcm.HEADER.size + pcm.BYTES
        numbers.append(number)
    assert numbers == list(range(10)) and sender.losses()[0] == 10


def test_an_answer_without_the_app_s_address_or_a_port_is_refused():
    sender = pcm.PcmSender("audiotestsrc num-buffers=1")
    try:
        with pytest.raises(ValueError):
            sender.answer(json.dumps({"port": 4000}))  # no address from the paired link
        sender.peer = "127.0.0.1"
        with pytest.raises(ValueError):
            sender.answer(json.dumps({"port": 70000}))
    finally:
        sender.close()
