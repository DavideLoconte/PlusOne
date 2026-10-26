"""The macOS app display's arithmetic: the stream's pixels onto the display's
points, and the pen's report read as macOS wants it (no Mac needed)."""
import struct

from plusone import macos, pen


def test_stream_pixels_land_on_the_displays_points():
    # A HiDPI display of 2560x1600 pixels, right of a 1512-point-wide screen.
    bounds = (1512, 0, 1280, 800)
    assert macos.to_screen(bounds, (2560, 1600), 0, 0) == (1512, 0)
    assert macos.to_screen(bounds, (2560, 1600), 2560, 1600) == (2792, 800)
    assert macos.to_screen(bounds, (2560, 1600), 1280, 400) == (2152, 200)


def test_a_pen_report_in_macos_ranges():
    report = bytes([pen.REPORT_ID]) + struct.pack("<BHHHbb", pen.TIP | pen.IN_RANGE,
                                                  pen.AXIS_MAX, 0, pen.PRESSURE_MAX // 2, -90, 45)
    bits, x, y, pressure, tilt_x, tilt_y = macos.pen_sample(report)
    assert bits & pen.TIP and (x, y) == (1.0, 0.0)
    assert pressure == 0.5 and (tilt_x, tilt_y) == (-1.0, 0.5)


def test_videotoolbox_frames_become_annex_b_with_their_parameter_sets():
    sps, pps = bytes.fromhex("6764001fac"), bytes.fromhex("68ee3c80")
    avcc = (bytes([1, 0x64, 0, 0x1F, 0xFF, 0xE1]) + len(sps).to_bytes(2, "big") + sps
            + bytes([1]) + len(pps).to_bytes(2, "big") + pps)
    sets, length_size = macos.parameter_sets(avcc)
    assert sets == [sps, pps] and length_size == 4
    idr, slice_ = bytes.fromhex("65888400"), bytes.fromhex("419a")
    frame = len(idr).to_bytes(4, "big") + idr + len(slice_).to_bytes(4, "big") + slice_
    start = macos.START
    assert macos.annex_b(frame, 4, sets) == start + sps + start + pps + start + idr + start + slice_
    assert macos.annex_b(frame) == start + idr + start + slice_

