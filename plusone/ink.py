"""Toolkit-free helpers for the signing window: page view and saved signature."""
import json
import os

from .pdf import Stroke

SIGNATURE_WIDTH = 140.0  # points (about 5 cm) when a saved signature is placed


class View:
    """Maps page points to widget pixels: widget = page * zoom + offset."""

    MIN_ZOOM, MAX_ZOOM = 0.2, 8.0

    def __init__(self):
        self.zoom, self.ox, self.oy = 1.0, 0.0, 0.0

    def fit(self, page_w, page_h, widget_w, widget_h, margin=16):
        """Whole page visible and centred."""
        self.zoom = max(self.MIN_ZOOM, min((widget_w - 2 * margin) / page_w,
                                           (widget_h - 2 * margin) / page_h))
        self.ox = (widget_w - page_w * self.zoom) / 2
        self.oy = (widget_h - page_h * self.zoom) / 2

    def to_page(self, x, y):
        return (x - self.ox) / self.zoom, (y - self.oy) / self.zoom

    def to_widget(self, px, py):
        return px * self.zoom + self.ox, py * self.zoom + self.oy

    def zoom_at(self, zoom, wx, wy, anchor=None):
        """Set zoom keeping page point `anchor` (default: the one under
        wx, wy) at widget position wx, wy."""
        px, py = anchor if anchor else self.to_page(wx, wy)
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        self.ox, self.oy = wx - px * self.zoom, wy - py * self.zoom

    def pan(self, dx, dy):
        self.ox += dx
        self.oy += dy


def normalize(strokes):
    """Strokes as a size-free signature: top-left at 0,0, width 1."""
    x0 = min(s.bounds()[0] for s in strokes)
    y0 = min(s.bounds()[1] for s in strokes)
    width = max(s.bounds()[2] for s in strokes) - x0 or 1.0
    return {"width": width,
            "strokes": [[[(x - x0) / width, (y - y0) / width, p] for x, y, p in s.points]
                        for s in strokes]}


def place(signature, x, y, width=SIGNATURE_WIDTH):
    """Strokes of a normalized signature with its left/vertical centre at x, y."""
    height = max((pt[1] for s in signature["strokes"] for pt in s), default=0) * width
    # Pen width scales with the signature so a small one does not look bold.
    pen = Stroke().width * width / signature.get("width", width)
    return [Stroke([(x + sx * width, y - height / 2 + sy * width, p) for sx, sy, p in s],
                   width=max(0.6, min(pen, 3.0)))
            for s in signature["strokes"]]


def load_signature(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if data.get("strokes") else None
    except (OSError, ValueError):
        return None


def save_signature(path, strokes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w") as f:
        json.dump(normalize(strokes), f)
    os.replace(tmp, path)
