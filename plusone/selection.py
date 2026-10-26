"""Marks picked to be moved, turned or scaled, as note apps do it
(docs/FINDINGS.md), and as the tablet's app does (android/.../Selection.kt).

A lasso picks the marks its area touches; a tap picks the mark under it.
The box turns with the selection; a handle at each corner scales from the
opposite one, keeping proportions; a turn handle out of a corner turns it
about its centre, showing the angle and snapping to right angles; the
inside drags. Toolkit-free: the signing window (sign.py) maps and draws.

Matrices are affine tuples (a, b, c, d, e, f): x, y go to a x + c y + e,
b x + d y + f, as ink.transform takes them.
"""
import math

from .ink import bounds, side, text_corners, transform
from .pdf import Stroke, Text

BLUE = (11 / 255, 153 / 255, 1.0)
LABEL = (1.0, 70 / 255, 49 / 255)
CORNER_RADIUS = 6   # widget pixels
CORNER_HIT = 22
TURN_RADIUS = 13
TURN_HIT = 24
TURN_OFFSET = 25    # along each edge out of the corner
MIN_SIDE = 60       # a smaller box is shown this big: the handles never crowd
SNAP = 4            # degrees from a right angle that snap to it
TAP = 10            # a lasso smaller than this (widget pixels) is a tap on a mark
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def multiply(m, n):
    """m, then n."""
    a, b, c, d, e, f = m
    p, q, r, s, t, u = n
    return (p * a + r * b, q * a + s * b, p * c + r * d, q * c + s * d, p * e + r * f + t, q * e + s * f + u)


def apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def translation(dx, dy):
    return (1.0, 0.0, 0.0, 1.0, dx, dy)


def scaling(k, cx, cy):
    return (k, 0.0, 0.0, k, cx - k * cx, cy - k * cy)


def rotation(degrees, cx, cy):
    r = math.radians(degrees)
    cos, sin = math.cos(r), math.sin(r)
    return (cos, sin, -sin, cos, cx - cos * cx + sin * cy, cy - sin * cx - cos * cy)


class Selection:
    def __init__(self, page, marks):
        self.page = page
        self.originals = list(marks)
        self.matrix = IDENTITY
        boxes = [bounds(m) for m in self.originals]
        self.frame = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                      max(b[2] for b in boxes), max(b[3] for b in boxes))
        self._shown = (None, [])

    def marks(self):
        """The marks as they are now (made again only when they moved)."""
        if self._shown[0] != self.matrix:
            self._shown = (self.matrix, [transform(m, self.matrix) for m in self.originals])
        return self._shown[1]

    def corners(self):
        """The box in page points: top left, top right, bottom right, bottom left."""
        x0, y0, x1, y1 = self.frame
        return [apply(self.matrix, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]

    @property
    def angle(self):
        """How far it is turned, in degrees (-180..180]."""
        a, b = self.matrix[:2]
        return math.degrees(math.atan2(b, a))


def snap_angle(degrees):
    nearest = round(degrees / 90) * 90
    return nearest if abs(degrees - nearest) <= SNAP else degrees


def scale_along(anchor, start, to):
    """The uniform scale taking start (from anchor) towards to, along start's direction."""
    dx, dy = start[0] - anchor[0], start[1] - anchor[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-9:
        return 1.0
    return max(0.05, ((to[0] - anchor[0]) * dx + (to[1] - anchor[1]) * dy) / length2)


# -- picking -------------------------------------------------------------------
def inside(polygon, x, y):
    """Even-odd ray casting; polygon: [(x, y), ...]."""
    result = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            result = not result
        j = i
    return result


def _crosses(polygon, a, b):
    n = len(polygon)
    for i in range(n):
        c, d = polygon[i], polygon[(i + 1) % n]
        if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
            return True
    return False


def picks(mark, polygon):
    """Whether a lasso (closed polygon, page points) picks mark: its area touches it."""
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    x0, y0, x1, y1 = bounds(mark)
    if max(xs) < x0 or min(xs) > x1 or max(ys) < y0 or min(ys) > y1:
        return False
    if isinstance(mark, Stroke):
        pts = [p[:2] for p in mark.points]
        return (any(inside(polygon, x, y) for x, y in pts)
                or any(_crosses(polygon, p, q) for p, q in zip(pts, pts[1:])))
    if isinstance(mark, Text):
        corners = text_corners(mark)
        return (any(inside(polygon, x, y) for x, y in corners)
                or any(inside(corners, x, y) for x, y in polygon)
                or any(_crosses(polygon, corners[i], corners[(i + 1) % 4]) for i in range(4)))
    return False


# -- on screen -----------------------------------------------------------------
def _unit(x, y):
    length = math.hypot(x, y)
    return (0.0, 0.0) if length < 1e-6 else (x / length, y / length)


def _grow(q, a, b, d, c, default):
    """Widens the opposite sides a-b and d-c to at least MIN_SIDE."""
    dx, dy = q[b][0] - q[a][0], q[b][1] - q[a][1]
    length = math.hypot(dx, dy)
    if length >= MIN_SIDE:
        return
    ux, uy = (dx / length, dy / length) if length > 0.01 else default
    half = (MIN_SIDE - length) / 2
    for i, sign in ((a, -1), (d, -1), (b, 1), (c, 1)):
        q[i] = (q[i][0] + sign * ux * half, q[i][1] + sign * uy * half)


def shape(corners, visible):
    """(quad, turn handle) on screen from the box's corners in widget pixels;
    visible: (x0, y0, x1, y1) where a handle can be touched."""
    q = list(corners)
    _grow(q, 0, 1, 3, 2, (1.0, 0.0))
    _grow(q, 0, 3, 1, 2, (0.0, 1.0))
    room = (visible[0] + TURN_RADIUS, visible[1] + TURN_RADIUS, visible[2] - TURN_RADIUS, visible[3] - TURN_RADIUS)
    first = None
    # Out of the bottom-right corner along both edges, or another corner's
    # when that is off screen or under a bar.
    for k in (2, 3, 1, 0):
        prev, nxt = q[(k + 3) % 4], q[(k + 1) % 4]
        ux, uy = _unit(q[k][0] - prev[0], q[k][1] - prev[1])
        vx, vy = _unit(q[k][0] - nxt[0], q[k][1] - nxt[1])
        turn = (q[k][0] + (ux + vx) * TURN_OFFSET, q[k][1] + (uy + vy) * TURN_OFFSET)
        first = first or turn
        if room[0] <= turn[0] <= room[2] and room[1] <= turn[1] <= room[3]:
            return q, turn
    return q, first


def hit(quad, turn, x, y):
    """What widget point x, y grabs: a corner (0-3), the turn handle (4),
    the inside (5), or nothing (-1)."""
    if math.hypot(x - turn[0], y - turn[1]) <= TURN_HIT:
        return 4
    corner = min(range(4), key=lambda i: math.hypot(x - quad[i][0], y - quad[i][1]))
    if math.hypot(x - quad[corner][0], y - quad[corner][1]) <= CORNER_HIT:
        return corner
    return 5 if inside(quad, x, y) else -1


def draw(cr, quad, turn, active=-1, turned=None):
    """The box, its corner handles and the turn handle (cairo, widget pixels)."""
    cr.set_line_width(2)
    cr.set_line_cap(1)  # round
    cr.set_source_rgb(*BLUE)
    cr.move_to(*quad[0])
    for point in quad[1:]:
        cr.line_to(*point)
    cr.close_path()
    cr.stroke()
    for i, (x, y) in enumerate(quad):
        cr.arc(x, y, CORNER_RADIUS, 0, 2 * math.pi)
        cr.set_source_rgb(*(BLUE if i == active else (1, 1, 1)))
        cr.fill_preserve()
        cr.set_source_rgb(*BLUE)
        cr.stroke()
    turning = active == 4
    x, y = turn
    cr.arc(x, y, TURN_RADIUS, 0, 2 * math.pi)
    cr.set_source_rgb(*(BLUE if turning else (1, 1, 1)))
    cr.fill_preserve()
    cr.set_source_rgb(*BLUE)
    cr.stroke()
    # A turning arrow.
    r = TURN_RADIUS * 0.48
    cr.set_source_rgb(*((1, 1, 1) if turning else BLUE))
    cr.set_line_width(1.8)
    cr.new_sub_path()
    cr.arc(x, y, r, math.radians(-60), math.radians(220))
    cr.stroke()
    tip = (x + r * 0.5, y - r * 0.866)
    cr.move_to(tip[0] - r * 0.55, tip[1] - r * 0.25)
    cr.line_to(*tip)
    cr.line_to(tip[0] - r * 0.15, tip[1] + r * 0.55)
    cr.stroke()
    if turned is not None:
        cr.set_source_rgb(*LABEL)
        cr.set_font_size(13)
        cr.move_to(x + TURN_RADIUS + 6, y + 4)
        cr.show_text(f"{turned % 360:.1f}°")


def draw_lasso(cr, points):
    """The lasso so far (widget pixels), closed as it will be: blue dots over white ones."""
    if len(points) < 2:
        return
    cr.set_line_width(3)
    cr.set_line_cap(1)
    for color, offset in ((BLUE + (1.0,), 0), ((1, 1, 1, 0.2), 6)):
        cr.set_dash([0.1, 12], offset)
        cr.move_to(*points[0])
        for point in points[1:]:
            cr.line_to(*point)
        cr.close_path()
        cr.set_source_rgba(*color)
        cr.stroke()
    cr.set_dash([])
