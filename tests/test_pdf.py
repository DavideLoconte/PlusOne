"""Saving marks onto a PDF: content kept, marks where the pen put them."""
import os

import cairo
import pytest

from plusone.pdf import Stroke, Text, display_size, output_path, save_signed

pypdf = pytest.importorskip("pypdf")


def make_pdf(path, rotate_second=0):
    """Two A4 pages with some text; the second one optionally rotated."""
    surface = cairo.PDFSurface(str(path), 595, 842)
    cr = cairo.Context(surface)
    for n in (1, 2):
        cr.set_font_size(14)
        cr.move_to(72, 72)
        cr.show_text(f"Original text page {n}")
        cr.show_page()
    surface.finish()
    if rotate_second:
        writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(str(path)))
        writer.pages[1].rotate(rotate_second)
        with open(path, "wb") as f:
            writer.write(f)


def blob(x, y):
    """A thick dot of ink at display position (x, y)."""
    return Stroke([(x, y, 1.0), (x + 0.1, y, 1.0)], width=12)


def ink_at(path, index, x, y):
    """Darkness of the rendered page around display point (x, y), 0..1."""
    gi = pytest.importorskip("gi")
    try:
        gi.require_version("Poppler", "0.18")
    except ValueError:
        pytest.skip("poppler introspection data not installed")
    from gi.repository import Poppler
    page = Poppler.Document.new_from_file(path.as_uri(), None).get_page(index)
    w, h = page.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, int(w), int(h))
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    page.render(cr)
    surface.flush()
    data, stride = surface.get_data(), surface.get_stride()
    px = data[int(y) * stride + int(x) * 4: int(y) * stride + int(x) * 4 + 3]
    return 1 - sum(px) / (3 * 255)


def test_output_path_never_overwrites(tmp_path):
    src = tmp_path / "contract.pdf"
    assert output_path(str(src)) == str(tmp_path / "contract-signed.pdf")
    (tmp_path / "contract-signed.pdf").write_bytes(b"x")
    assert output_path(str(src)) == str(tmp_path / "contract-signed-2.pdf")


def test_text_kept_and_marks_added(tmp_path):
    src = tmp_path / "doc.pdf"
    make_pdf(src)
    out = save_signed(str(src), {0: [Text(80, 400, "SIGNED-HERE")], 1: []})
    assert out.endswith("doc-signed.pdf")
    reader = pypdf.PdfReader(out)
    assert len(reader.pages) == 2
    first = reader.pages[0].extract_text()
    assert "Original text page 1" in first and "SIGNED-HERE" in first
    assert "SIGNED-HERE" not in reader.pages[1].extract_text()
    # The original is untouched.
    assert "SIGNED-HERE" not in pypdf.PdfReader(str(src)).pages[0].extract_text()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_ink_lands_where_drawn(tmp_path, rotation):
    src = tmp_path / "doc.pdf"
    make_pdf(src, rotate_second=rotation)
    page = pypdf.PdfReader(str(src)).pages[1]
    width, height = display_size(page)
    # Near the top-left corner of the page as displayed: a wrong rotation or
    # flip would move the dot to another corner.
    x, y = 60, 90
    out = tmp_path / "out.pdf"
    save_signed(str(src), {1: [blob(x, y)]}, dst=str(out))
    assert ink_at(out, 1, x, y) > 0.5
    for other in ((width - x, y), (x, height - y), (width - x, height - y)):
        assert ink_at(out, 1, *other) < 0.1, other


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
def test_read_only_folder_raises_permission_error(tmp_path):
    # The signing window answers PermissionError with a "save as" dialog
    # (read-only folders, and Flatpak documents shared one file at a time).
    folder = tmp_path / "ro"
    folder.mkdir()
    src = folder / "doc.pdf"
    make_pdf(src)
    folder.chmod(0o555)
    try:
        with pytest.raises(PermissionError):
            save_signed(str(src), {0: [Text(80, 400, "x")]})
    finally:
        folder.chmod(0o755)
