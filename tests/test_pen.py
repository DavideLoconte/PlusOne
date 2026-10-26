import struct

from plusone.pen import (BTN_LEFT, BTN_RIGHT, UHID_CREATE2, UHID_INPUT2, PointerPen, UhidPen)

# Descriptor and a report captured from a Galaxy Tab S10 FE+ S Pen (Second
# Screen's UIBC sent them so; the app sends the same reports).
PEN_DESCRIPTOR = bytes.fromhex(
    "050d0902a10185140920a10009420944093c0945093215002501750195058102950381030501"
    "093075109501a4550d65133500463a2027ff7f000081020931462c1827ff7f00008102b4050d"
    "093026001081027508093d15a6255a8102093e8102c0c0")
PEN_REPORT = bytes.fromhex("14114a39277dfc013319")


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def report(bits, x, y, pressure=0):
    return bytes([0x14, bits]) + struct.pack("<HHH", x, y, pressure) + b"\x00\x00"


def test_uhid_create_event_layout():
    event = UhidPen.create_event(PEN_DESCRIPTOR)
    # u32 type, name[128], phys[64], uniq[64], u16 rd_size, u16 bus, 4 x u32, descriptor
    assert struct.unpack_from("<I", event)[0] == UHID_CREATE2
    assert struct.unpack_from("<H", event, 4 + 128 + 64 + 64)[0] == len(PEN_DESCRIPTOR)
    assert event[-len(PEN_DESCRIPTOR):] == PEN_DESCRIPTOR
    assert len(event) == 4 + 128 + 64 + 64 + 2 + 2 + 4 * 4 + len(PEN_DESCRIPTOR)


def test_uhid_input_event_layout():
    event = UhidPen.input_event(PEN_REPORT)
    assert struct.unpack_from("<IH", event) == (UHID_INPUT2, len(PEN_REPORT))
    assert event[6:] == PEN_REPORT


def test_pointer_pen_hover_tip_barrel():
    injector = Recorder()
    pen = PointerPen(injector, (1920, 1200))
    pen.report(report(0x10, 32767, 0))            # hovering, top-right corner
    pen.report(report(0x11, 32767, 32767, 500))   # tip down, bottom-right
    pen.report(report(0x13, 32767, 32767, 500))   # + barrel button
    pen.report(report(0x10, 32767, 32767))        # lifted
    pen.report(report(0x00, 0, 0))                # out of range
    moves = [c for c in injector.calls if c[0] == "pointer_move"]
    buttons = [c[1:] for c in injector.calls if c[0] == "pointer_button"]
    assert moves[0][1:] == (1920.0, 0.0) and moves[1][1:] == (1920.0, 1200.0)
    assert buttons == [(BTN_LEFT, True), (BTN_RIGHT, True), (BTN_LEFT, False), (BTN_RIGHT, False)]


def test_pointer_pen_close_releases_buttons():
    injector = Recorder()
    pen = PointerPen(injector, (100, 100))
    pen.report(report(0x11, 10, 10, 1))
    pen.close()
    assert injector.calls[-1] == ("pointer_button", BTN_LEFT, False)


def pen_report(bits, x, y, pressure):
    return struct.pack("<BBHHHbb", 0x14, bits, x, y, pressure, 0, 0)


def test_parse_report():
    from plusone.pen import parse_report
    bits, x, y, pressure = parse_report(pen_report(0x11, 32767, 0, 2048))
    assert (bits, x, y, pressure) == (0x11, 1.0, 0.0, 0.5)
    assert parse_report(b"\x15" + bytes(9)) is None and parse_report(b"\x14\x11") is None


def test_feed_gives_every_report_to_each_reader():
    from plusone.pen import Feed
    feed = Feed()
    feed.publish(pen_report(0x11, 0, 0, 0))   # nobody listening: dropped
    a, b = feed.listen(), feed.listen()
    for i in range(300):
        feed.publish(pen_report(0x11, i, i, 100))
    assert len(a) == len(b) == 300 and a[-1][1:] == (0x11, 299 / 32767, 299 / 32767, 100 / 4096)
    feed.unlisten(a)
    feed.publish(pen_report(0x10, 0, 0, 0))
    assert len(a) == 300 and len(b) == 301


def test_why_the_pen_has_no_pressure(monkeypatch):
    # No group to join any more: the helper, or nothing.
    from plusone import pen
    monkeypatch.setattr(pen, "uhid_available", lambda: False)
    assert pen.uhid_missing() == "set up"
    monkeypatch.setattr(pen, "uhid_available", lambda: True)
    assert pen.uhid_missing() is None


def test_the_helper_takes_only_pen_reports(monkeypatch):
    # The helper's socket carries the pen's 10-byte reports and nothing else.
    import socket
    from plusone import pen
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    device = pen.HelperPen(ours)
    device.report(b"\x14" + bytes(9))      # a pen report
    device.report(b"\x01\x02\x03")          # not one: not sent
    assert theirs.recv(64) == b"\x14" + bytes(9)
    theirs.setblocking(False)
    try:
        theirs.recv(64)
        raise AssertionError("more than the pen's report went")
    except BlockingIOError:
        pass
    device.close()
    theirs.setblocking(True)
    assert theirs.recv(64) == b""  # closed: the helper takes its pen away
