import struct

from plusone.pen import (BTN_LEFT, BTN_RIGHT, UHID_CREATE2, UHID_INPUT2, PointerPen, UhidPen)
from test_uibc import PEN_DESCRIPTOR, PEN_REPORT, Recorder


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
