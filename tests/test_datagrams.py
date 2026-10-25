"""The camera's and the microphone's datagrams: sealed, put together, in order."""
import os
import socket
import struct
import threading

from plusone import datagrams
from plusone.aead import Key

KEY, SALT = os.urandom(32), os.urandom(4)


class App:
    """What the app sends (Capture.kt UdpOut)."""

    def __init__(self, key=KEY, salt=SALT):
        self.key, self.salt, self.sequence = Key(key), salt, 0

    def datagrams(self, kind, frame, payload, flags=0, size=1200, pts=0):
        parts = [payload[i:i + size] for i in range(0, len(payload), size)] or [b""]
        out = []
        for i, part in enumerate(parts):
            header = datagrams.HEADER.pack(datagrams.MAGIC, ord(kind), flags, self.sequence, frame, i,
                                           len(parts), pts)
            out.append(header + self.key.seal(datagrams.nonce(self.salt, self.sequence), part, header))
            self.sequence += 1
        return out


def test_a_frame_in_parts_comes_whole():
    pieces, app = datagrams.Pieces(KEY, SALT), App()
    frame = os.urandom(5000)
    sent = app.datagrams("v", 0, frame, flags=datagrams.KEY_FRAME, pts=33)
    assert [pieces.add(d) for d in sent[:-1]] == [[]] * (len(sent) - 1)
    [(header, payload)] = pieces.add(sent[-1])
    assert payload == frame and header == {"type": "video", "pts": 33, "key": True, "config": False}


def test_parts_out_of_order_and_twice():
    pieces, app = datagrams.Pieces(KEY, SALT), App()
    sent = app.datagrams("v", 0, b"x" * 3000)
    assert pieces.add(sent[2]) == [] and pieces.add(sent[0]) == [] and pieces.add(sent[0]) == []
    assert pieces.add(sent[1])[0][1] == b"x" * 3000


def test_what_was_not_sealed_with_the_key_is_dropped():
    pieces = datagrams.Pieces(KEY, SALT)
    [good] = App().datagrams("a", 0, b"opus")
    assert pieces.add(App(key=os.urandom(32)).datagrams("a", 0, b"opus")[0]) == []
    tampered = good[:-1] + bytes([good[-1] ^ 1])
    assert pieces.add(tampered) == []
    header = bytearray(good)
    header[3] |= datagrams.KEY_FRAME  # the flags, changed on the way
    assert pieces.add(bytes(header)) == []
    assert pieces.add(good) == [({"type": "audio", "pts": 0, "key": False, "config": False}, b"opus")]
    assert pieces.add(good) == []  # the same again: a replay


def test_a_video_frame_lost_is_counted_audio_is_not():
    video, app = datagrams.Pieces(KEY, SALT), App()
    for d in app.datagrams("v", 0, b"a"):
        video.add(d)
    half = app.datagrams("v", 1, b"b" * 2500)
    video.add(half[0])  # its other parts never come
    app.datagrams("v", 2, b"c")  # never comes at all
    for d in app.datagrams("v", 3, b"d"):
        out = video.add(d)
    assert out[0][1] == b"d" and video.lost == 2 and not video.frames
    assert video.add(half[1]) == []  # too late
    audio, app = datagrams.Pieces(KEY, SALT), App()
    audio.add(app.datagrams("a", 0, b"1")[0])
    app.datagrams("a", 1, b"2")
    audio.add(app.datagrams("a", 2, b"3")[0])
    assert audio.lost == 0


def test_the_link_punches_and_asks_for_key_frames():
    app_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app_sock.bind(("127.0.0.1", 0))
    tls = types_tls(app_sock.getsockname()[0])
    sent = []
    wire = type("Wire", (), {"send": lambda self, m: sent.append(m)})()
    token = os.urandom(16)
    info = {"udp": app_sock.getsockname()[1], "token": token.hex(), "key": KEY.hex(), "salt": SALT.hex()}
    pc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    link = datagrams.Link(tls, wire, info, pc)
    app = App()

    def tablet():
        punch, where = app_sock.recvfrom(100)
        assert punch == token
        for d in app.datagrams("v", 0, b"k", flags=datagrams.KEY_FRAME):
            app_sock.sendto(d, where)
        app.datagrams("v", 1, b"lost")
        for d in app.datagrams("v", 2, b"p"):
            app_sock.sendto(d, where)
    thread = threading.Thread(target=tablet)
    thread.start()
    assert link.receive_piece()[1] == b"k"
    assert link.receive_piece()[1] == b"p"
    thread.join(5)
    assert sent == [{"type": "key"}]
    link.close()
    app_sock.close()


def types_tls(address):
    return type("Tls", (), {"getpeername": lambda self: (address, 1), "close": lambda self: None})()


def test_nonce_is_salt_and_sequence():
    assert datagrams.nonce(b"abcd", 1) == b"abcd" + struct.pack(">Q", 1)
