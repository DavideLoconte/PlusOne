"""The app display's packets: their layout, sealed both ways, replays refused."""
import socket
import struct

import pytest

from plusone import appdisplay  # noqa: E402

crypto = pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
AESGCM = crypto.AESGCM

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


class FakeMonitor:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def fingers(*points):
    """An INPUT packet with these fingers (id, x, y in 0..1) and no pen."""
    body = bytes([appdisplay.INPUT, len(points)])
    for finger, x, y in points:
        body += appdisplay.FINGER.pack(finger, int(x * appdisplay.TOUCH_MAX), int(y * appdisplay.TOUCH_MAX))
    return body + b"\x00"


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def touch_input():
    clock, monitor = Clock(), FakeMonitor()
    return appdisplay.Input(monitor, (1000, 1000), lambda: None, clock=clock), monitor, clock


def test_a_finger_tapped_is_the_left_button():
    # Not a touch tapped, which some programs (Qt's) take as hover: a click,
    # where the finger came down.
    touch, monitor, clock = touch_input()
    assert touch.packet(fingers((0, 0.5, 0.5)))
    assert monitor.calls == []  # a drag, or a second finger, may come
    clock.now += 0.08
    touch.packet(fingers((0, 0.505, 0.5)))  # a tap's wobble
    clock.now += 0.03
    touch.packet(fingers())
    assert monitor.calls == [("pointer_move", pytest.approx(500, abs=0.1), pytest.approx(500, abs=0.1)),
                             ("pointer_button", appdisplay.pen.BTN_LEFT, True),
                             ("pointer_button", appdisplay.pen.BTN_LEFT, False)]


def test_a_finger_that_moves_is_touches_from_where_it_began():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.5, 0.5)))
    clock.now += 0.04
    touch.packet(fingers((0, 0.52, 0.5)))  # a drag: at once, nothing of it lost
    assert [c[0] for c in monitor.calls] == ["touch_down", "touch_motion"]
    assert monitor.calls[0][2] == pytest.approx(500, abs=0.1)
    touch.packet(fingers((0, 0.6, 0.5)))
    assert monitor.calls[-1][0] == "touch_motion"


def test_a_finger_held_is_a_touch_held():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.5, 0.5)))
    clock.now += appdisplay.TAP_TIME / 2
    touch.packet(fingers((0, 0.5, 0.5)))  # the app says it again every 0.1 s
    assert monitor.calls == []
    clock.now += appdisplay.TAP_TIME / 2 + 0.01
    touch.check()  # (a long press: the program's own menu, if it has one)
    assert [c[0] for c in monitor.calls] == ["touch_down"]


def test_two_fingers_tapped_are_the_right_button():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.3, 0.4)))
    clock.now += 0.03
    touch.packet(fingers((0, 0.3, 0.4), (1, 0.35, 0.4)))
    clock.now += 0.1
    touch.check()
    touch.packet(fingers((1, 0.35, 0.4)))
    clock.now += 0.05
    touch.packet(fingers())
    assert monitor.calls == [("pointer_move", pytest.approx(300, abs=0.1), pytest.approx(400, abs=0.1)),
                             ("pointer_button", appdisplay.pen.BTN_RIGHT, True),
                             ("pointer_button", appdisplay.pen.BTN_RIGHT, False)]


def test_a_pinch_is_touches():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.3, 0.4)))
    clock.now += 0.03
    touch.packet(fingers((0, 0.3, 0.4), (1, 0.4, 0.4)))
    clock.now += 0.03
    touch.packet(fingers((0, 0.25, 0.4), (1, 0.45, 0.4)))
    assert [c[0] for c in monitor.calls] == ["touch_down", "touch_down", "touch_motion", "touch_motion"]


def test_two_fingers_resting_are_touches():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.3, 0.4), (1, 0.4, 0.4)))
    clock.now += appdisplay.TAP_TIME + 0.01
    touch.check()
    assert [c[0] for c in monitor.calls] == ["touch_down", "touch_down"]


def test_silence_lifts_what_touches():
    touch, monitor, clock = touch_input()
    touch.packet(fingers((0, 0.5, 0.5)))
    clock.now += 0.1
    touch.check()
    clock.now += appdisplay.INPUT_SILENCE
    touch.check()
    assert [c[0] for c in monitor.calls] == ["touch_down", "touch_up"]


def test_the_listener_looks_often_only_while_something_touches():
    touch, _monitor, clock = touch_input()
    assert not touch.busy()
    touch.packet(fingers((0, 0.5, 0.5)))
    assert touch.busy()  # a second finger may come
    clock.now += 0.1
    touch.check()
    assert touch.busy()  # down
    touch.packet(fingers())
    assert not touch.busy()


def test_sizes_keep_the_tablet_s_proportions():
    assert appdisplay.sizes((2880, 1800))[:4] == [(2880, 1800), (2560, 1600), (2304, 1440), (1920, 1200)]
    assert appdisplay.sizes((1800, 2880))[0] == (2880, 1800)  # portrait reported: landscape
    for w, h in appdisplay.sizes((2800, 1752)):
        assert h % 2 == 0 and abs(w / h - 2800 / 1752) < 0.01
    assert appdisplay.auto_size((2880, 1800)) == (1920, 1200)
    assert appdisplay.auto_size((1920, 1080)) == (1920, 1080)


def test_big_sizes_are_hidpi_with_the_bitrate_to_match():
    assert appdisplay.scale_for((2880, 1800)) == 2 and appdisplay.scale_for((1920, 1200)) == 1
    assert appdisplay.bitrate_for(20000, (2880, 1800)) == 45000
    assert appdisplay.bitrate_for(20000, (1280, 800)) == 20000  # never less than asked


def test_the_bitrate_follows_the_network():
    rate = appdisplay.RateControl(20000)
    for _ in range(5):  # clean: stays at the most
        assert rate.report(60, 0, 30, 20) is None
    assert rate.report(55, 3, 30, 20) == 13333  # frames lost: down by a third, at once
    assert rate.report(60, 0, 80, 20) == 8888  # the delay through the network grew: a queue
    assert rate.report(0, 40, 0, 0) == 5925  # nothing shown, all lost
    for _ in range(2):
        assert rate.report(60, 0, 30, 20) is None
    assert rate.report(60, 0, 30, 20) == 6617  # clean for a while: up by a tenth
    rate.kbps = 19950
    for _ in range(3):
        result = rate.report(60, 0, 30, 20)
    assert result == 20000 and rate.kbps == 20000  # never past the one set
    rate.kbps = appdisplay.MIN_KBPS
    assert rate.report(60, 9, 30, 20) is None  # nor below the least


def test_a_still_desktop_s_few_frames_do_not_move_the_bitrate():
    rate = appdisplay.RateControl(20000)
    for _ in range(4):
        rate.report(30, 0, 30, 20)
    assert rate.report(1, 0, 90, 20) is None  # one frame, late: Wi-Fi's jitter, not a queue
    assert rate.report(30, 0, 90, 20) == 13333  # a second of them late: one


def test_what_is_still_to_go_out_is_known():
    app = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app.bind(("127.0.0.1", 0))
    channel = channel_to(app)
    try:
        assert channel.queued() == 0
        channel.send(b"\x05")
        assert channel.queued() >= 0
    finally:
        channel.close()
        app.close()
    assert channel.queued() == 0  # closed: nothing
