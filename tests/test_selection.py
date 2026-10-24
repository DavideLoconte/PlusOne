"""Picking marks and moving, turning, scaling them (selection.py)."""
import math

import pytest

from plusone import selection as picking
from plusone.pdf import Stroke, Text


def close(a, b):
    return all(abs(x - y) < 1e-6 for x, y in zip(a, b))


def test_matrices():
    turn = picking.rotation(90, 10, 10)
    assert close(picking.apply(turn, 20, 10), (10, 20))  # y points down: a quarter turn clockwise
    grow = picking.scaling(2, 10, 10)
    assert close(picking.apply(grow, 20, 10), (30, 10))
    both = picking.multiply(grow, picking.translation(5, 0))  # first grow, then move
    assert close(picking.apply(both, 20, 10), (35, 10))


def test_the_box_turns_and_scales_with_the_marks():
    chosen = picking.Selection(0, [Stroke([(0, 0, 0.5), (100, 0, 0.5)], width=2)])
    x0, y0, x1, y1 = chosen.frame
    chosen.matrix = picking.multiply(picking.scaling(2, x0, y0), picking.rotation(90, x0, y0))
    assert chosen.angle == pytest.approx(90)
    stroke = chosen.marks()[0]
    assert stroke.width == pytest.approx(4)
    assert close(stroke.points[1][:2], picking.apply(chosen.matrix, 100, 0))
    corners = chosen.corners()
    assert close(corners[0], (x0, y0))  # it turned about the top-left corner


def test_a_lasso_picks_what_its_area_touches():
    lasso = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert picking.picks(Stroke([(10, 10, 0.5), (20, 20, 0.5)]), lasso)        # inside
    assert picking.picks(Stroke([(-50, 50, 0.5), (150, 50, 0.5)]), lasso)      # across it
    assert not picking.picks(Stroke([(150, 10, 0.5), (190, 90, 0.5)]), lasso)  # beside it
    assert picking.picks(Text(40, 50, "24/10/2026"), lasso)
    assert not picking.picks(Text(300, 50, "far"), lasso)


def test_small_boxes_are_shown_big_and_the_turn_handle_stays_reachable():
    corners = [(100, 100), (110, 100), (110, 104), (100, 104)]
    quad, turn = picking.shape(corners, (0, 0, 1000, 1000))
    assert math.dist(quad[0], quad[1]) == pytest.approx(picking.MIN_SIDE)
    assert math.dist(quad[1], quad[2]) == pytest.approx(picking.MIN_SIDE)
    assert turn[0] > quad[2][0] and turn[1] > quad[2][1]  # out of the bottom-right corner
    # Bottom right under the toolbar: another corner's.
    corners = [(100, 900), (300, 900), (300, 990), (100, 990)]
    _quad, turn = picking.shape(corners, (0, 0, 1000, 950))
    assert turn[1] < 950


def test_what_a_touch_grabs():
    quad = [(100, 100), (300, 100), (300, 200), (100, 200)]
    turn = (318, 218)
    assert picking.hit(quad, turn, 102, 99) == 0
    assert picking.hit(quad, turn, 299, 201) == 2
    assert picking.hit(quad, turn, 320, 220) == 4
    assert picking.hit(quad, turn, 200, 150) == 5
    assert picking.hit(quad, turn, 500, 500) == -1


def test_turning_snaps_to_right_angles():
    assert picking.snap_angle(3) == 0 and picking.snap_angle(-88) == -90
    assert picking.snap_angle(30) == 30
    assert picking.scale_along((0, 0), (10, 10), (20, 20)) == pytest.approx(2)
