"""The signing window's page raster: changes re-render only their area."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Poppler", "0.18")

from test_pdf import make_pdf  # noqa: E402

from plusone import sign  # noqa: E402
from plusone.ink import bounds, touches  # noqa: E402
from plusone.pdf import Stroke, Text  # noqa: E402


def strokes():
    return [Stroke([(60 + 40 * i + j, 80 + 30 * i + (j % 7), 0.5) for j in range(40)], width=1.5)
            for i in range(8)] + [Text(300, 700, "Name Surname")]


def raster_for(tmp_path, marks):
    src = tmp_path / "doc.pdf"
    make_pdf(src)
    page = sign.open_document(str(src)).get_page(0)
    raster = sign.PageRaster(0, 1.5, page, page.get_size())
    raster.update(marks)
    return raster, page


def pixels(raster):
    raster.surface.flush()
    return bytes(raster.surface.get_data())


def test_erasing_and_undoing_match_a_full_render(tmp_path):
    marks = strokes()
    raster, page = raster_for(tmp_path, marks)
    for key in raster.tile_boxes():
        raster.tile(*key)
    erased = marks[:3] + marks[4:]
    raster.update(erased)
    fresh = sign.PageRaster(0, 1.5, page, page.get_size())
    fresh.update(erased)
    assert pixels(raster) == pixels(fresh)
    # Only the tiles under the erased stroke were made again.
    area = raster.area([marks[3]])
    gone = {key for key in raster.tile_boxes() if key not in raster.tiles}
    assert gone and all(sign._overlap(raster._tile_box(*key), area) for key in gone)
    raster.update(marks)  # undo
    fresh.update(marks)
    assert pixels(raster) == pixels(fresh)


def test_bounds_and_quick_rejection():
    stroke = Stroke([(10, 10, 0.5), (20, 15, 0.5)], width=2)
    x0, y0, x1, y1 = bounds(stroke)
    assert x0 <= 9 and y0 <= 9 and x1 >= 21 and y1 >= 16
    assert not touches(stroke, (200, 200), (210, 210), 5)
    assert touches(stroke, (15, 5), (15, 20), 1)
    assert bounds(Text(0, 20, "WWW", size=10))[2] >= 30
