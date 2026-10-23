"""Signing window helpers that do not need a display."""
import pytest

from plusone.ink import SIGNATURE_WIDTH, View, load_signature, normalize, place, save_signature
from plusone.pdf import Stroke


def test_fit_shows_whole_page_centred():
    view = View()
    view.fit(595, 842, 1920, 1100, margin=16)
    assert view.zoom == pytest.approx((1100 - 32) / 842)
    left, top = view.to_widget(0, 0)
    right, bottom = view.to_widget(595, 842)
    assert top == pytest.approx(16) and bottom == pytest.approx(1100 - 16)
    assert left == pytest.approx(1920 - right)  # centred horizontally


def test_page_widget_round_trip():
    view = View()
    view.fit(595, 842, 800, 600)
    view.zoom_at(2.5, 300, 200)
    assert view.to_page(*view.to_widget(123.4, 567.8)) == pytest.approx((123.4, 567.8))


def test_zoom_keeps_point_under_fingers():
    view = View()
    view.fit(595, 842, 800, 600)
    before = view.to_page(400, 300)
    view.zoom_at(view.zoom * 3, 400, 300)
    assert view.to_page(400, 300) == pytest.approx(before)


def test_pinch_anchor_follows_fingers():
    view = View()
    view.fit(595, 842, 800, 600)
    anchor = view.to_page(400, 300)
    view.zoom_at(view.zoom * 2, 450, 320, anchor)  # fingers moved while pinching
    assert view.to_widget(*anchor) == pytest.approx((450, 320))


def test_zoom_is_clamped():
    view = View()
    view.zoom_at(1000, 0, 0)
    assert view.zoom == View.MAX_ZOOM


def strokes():
    return [Stroke([(100, 200, 0.5), (160, 220, 0.8)]), Stroke([(120, 190, 0.4), (130, 230, 0.6)])]


def test_saved_signature_is_placed_at_the_tap(tmp_path):
    path = tmp_path / "plus-one" / "signature.json"
    save_signature(str(path), strokes())
    signature = load_signature(str(path))
    placed = place(signature, 300, 500)
    xs = [x for s in placed for x, _y, _p in s.points]
    ys = [y for s in placed for _x, y, _p in s.points]
    assert min(xs) == pytest.approx(300)
    assert max(xs) - min(xs) == pytest.approx(SIGNATURE_WIDTH)
    assert (min(ys) + max(ys)) / 2 == pytest.approx(500)  # vertically centred on the tap
    # Pressure survives; shape keeps its proportions (60 wide, 40 tall).
    assert placed[0].points[1][2] == 0.8
    assert max(ys) - min(ys) == pytest.approx(SIGNATURE_WIDTH * 40 / 60)


def test_missing_or_broken_signature(tmp_path):
    assert load_signature(str(tmp_path / "none.json")) is None
    (tmp_path / "bad.json").write_text("{nope")
    assert load_signature(str(tmp_path / "bad.json")) is None


def test_normalize_single_dot():
    signature = normalize([Stroke([(10, 10, 1.0)])])
    assert signature["strokes"] == [[[0.0, 0.0, 1.0]]]


def test_curve_passes_through_samples_without_repeats():
    from plusone.pdf import curve
    pts = [(0, 0, 0.5), (0, 0, 0.9), (10, 5, 0.5), (20, 0, 0.5), (20, 0, 0.5)]
    segments = list(curve(pts))
    assert [s[0] for s in segments] == [(0, 0), (10, 5)]
    assert [s[3] for s in segments] == [(10, 5), (20, 0)]


def test_curve_does_not_overshoot_uneven_samples():
    # Collinear samples spaced 1, 1, 20 apart (fast pen): a uniform
    # Catmull-Rom would bulge backwards; chord-length tangents stay on the line.
    from plusone.pdf import curve
    pts = [(0, 0, 0.5), (1, 0, 0.5), (2, 0, 0.5), (22, 0, 0.5)]
    for start, c1, c2, end, _p0, _p1 in curve(pts):
        for c in (c1, c2):
            assert c[1] == pytest.approx(0)
            assert min(start[0], end[0]) - 1e-9 <= c[0] <= max(start[0], end[0]) + 1e-9


def test_two_samples_make_a_straight_segment():
    from plusone.pdf import curve
    (start, c1, c2, end, _p0, _p1), = curve([(0, 0, 0.5), (9, 3, 0.5)])
    assert c1 == pytest.approx([3, 1]) and c2 == pytest.approx([6, 2])


def test_pressure_shows_in_the_width():
    from plusone.pdf import PEN_WIDTH, ink_width
    light, firm = ink_width(PEN_WIDTH, 0.15), ink_width(PEN_WIDTH, 0.45)
    assert firm > 2 * light                         # the usual writing range differs visibly
    assert ink_width(PEN_WIDTH, 0.9) == ink_width(PEN_WIDTH, 2) == PEN_WIDTH
    assert ink_width(PEN_WIDTH, -1) == ink_width(PEN_WIDTH, 0) > 0


def test_eraser_touches_strokes_it_passes_over():
    from plusone.ink import touches
    from plusone.pdf import Text
    stroke = Stroke([(0, 0, 0.5), (100, 0, 0.5)], width=2)
    assert touches(stroke, (50, 5), (50, 5), radius=4.5)       # within radius + half the line
    assert not touches(stroke, (50, 8), (50, 8), radius=4.5)
    # A fast eraser jumps over the line between two samples: still erased.
    assert touches(stroke, (50, -30), (50, 30), radius=1)
    assert not touches(stroke, (120, -30), (120, 30), radius=1)
    assert touches(Stroke([(10, 10, 0.5)]), (12, 12), (12, 12), radius=3)
    date = Text(100, 200, "24/10/2026", size=11)
    assert touches(date, (130, 196), (130, 196), radius=1)
    assert not touches(date, (130, 230), (130, 230), radius=1)
