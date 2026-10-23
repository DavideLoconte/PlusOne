"""Full-screen window for signing a PDF with the pen, shown on the tablet.

The S Pen draws (with pressure); fingers never do, so a palm resting on the
screen leaves no marks. Two fingers zoom and move the page. A mouse also
draws, for signing on the PC.
"""
import datetime
import os
import threading

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Poppler", "0.18")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Poppler  # noqa: E402

from . import log, once  # noqa: E402
from .i18n import _  # noqa: E402
from .ink import View, load_signature, place, save_signature  # noqa: E402
from .pdf import Stroke, Text, draw_marks, output_path, save_signed  # noqa: E402

SIGNATURE = os.path.join(GLib.get_user_data_dir(), "plus-one", "signature.json")
PEN_SOURCES = (Gdk.InputSource.PEN, Gdk.InputSource.TOUCHSCREEN)


def tablet_monitor():
    """The tablet's virtual monitor (Mutter names them Meta-N), if any."""
    monitors = Gdk.Display.get_default().get_monitors()
    found = None
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        if (monitor.get_connector() or "").startswith("Meta-"):
            found = monitor
    return found


def open_document(path):
    """A Poppler document, or raise GLib.Error (missing, damaged, password)."""
    return Poppler.Document.new_from_file(Gio.File.new_for_path(path).get_uri(), None)


class PageArea(Gtk.DrawingArea):
    """One page with its marks; turns pen/mouse input into page coordinates."""

    def __init__(self, window):
        # A natural size, or a window that is not maximized shrinks to nothing.
        super().__init__(hexpand=True, vexpand=True, focusable=True,
                         content_width=480, content_height=600)
        self.window = window
        self.view = View()
        self.fitted = False
        self._cache = None  # (page index, zoom, surface)
        self._stroke = None
        self.set_draw_func(self._draw)
        self.connect("resize", lambda *a: self.fit())

        stylus = Gtk.GestureStylus()
        stylus.connect("down", self._pen_down)
        stylus.connect("motion", self._pen_motion)
        stylus.connect("up", self._pen_up)
        self.add_controller(stylus)

        mouse = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        mouse.connect("drag-begin", self._mouse_begin)
        mouse.connect("drag-update", self._mouse_update)
        mouse.connect("drag-end", self._mouse_end)
        self.add_controller(mouse)

        tap = Gtk.GestureClick(touch_only=True)
        tap.connect("released", lambda g, n, x, y: self.window.place_at(*self.view.to_page(x, y)))
        self.add_controller(tap)

        zoom = Gtk.GestureZoom()
        zoom.connect("begin", self._zoom_begin)
        zoom.connect("scale-changed", self._zoom_changed)
        zoom.connect("end", lambda *a: self.queue_draw())
        self.add_controller(zoom)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._scroll)
        self.add_controller(scroll)

    # -- view ------------------------------------------------------------------
    def page_size(self):
        return self.window.page().get_size()

    def fit(self):
        w, h = self.get_width(), self.get_height()
        if w and h:
            self.view.fit(*self.page_size(), w, h)
            self.fitted = True
            self.queue_draw()

    def _zoom_begin(self, gesture, _seq):
        ok, x, y = gesture.get_bounding_box_center()
        self._zoom_start = (self.view.zoom, self.view.to_page(x, y))

    def _zoom_changed(self, gesture, scale):
        ok, x, y = gesture.get_bounding_box_center()
        zoom, anchor = self._zoom_start
        # Pinch zooms, moving both fingers pans: the anchor follows them.
        self.view.zoom_at(zoom * scale, x, y, anchor)
        self.queue_draw()

    def _scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            x, y = self.get_width() / 2, self.get_height() / 2
            self.view.zoom_at(self.view.zoom * (0.9 if dy > 0 else 1.1), x, y)
        else:
            self.view.pan(-dx * 40, -dy * 40)
        self.queue_draw()
        return True

    # -- input -----------------------------------------------------------------
    def _pressure(self, gesture):
        ok, value = gesture.get_axis(Gdk.AxisUse.PRESSURE)
        return value if ok and value > 0 else 0.5

    def _begin(self, x, y, pressure):
        px, py = self.view.to_page(x, y)
        if self.window.place_at(px, py):
            return
        self._stroke = Stroke([(px, py, pressure)])
        self.queue_draw()

    def _extend(self, x, y, pressure):
        if self._stroke is not None:
            self._stroke.points.append((*self.view.to_page(x, y), pressure))
            self.queue_draw()

    def _finish(self):
        if self._stroke is not None:
            self.window.add_mark(self._stroke)
            self._stroke = None

    def _pen_down(self, gesture, x, y):
        self._begin(x, y, self._pressure(gesture))

    def _pen_motion(self, gesture, x, y):
        self._extend(x, y, self._pressure(gesture))

    def _pen_up(self, gesture, x, y):
        self._extend(x, y, self._pressure(gesture))
        self._finish()

    def _mouse_begin(self, gesture, x, y):
        device = gesture.get_device()
        if device is not None and device.get_source() in PEN_SOURCES:
            # The stylus gesture handles the pen; fingers never draw.
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._mouse_origin = (x, y)
        self._begin(x, y, 0.6)

    def _mouse_update(self, gesture, dx, dy):
        x, y = self._mouse_origin
        self._extend(x + dx, y + dy, 0.6)

    def _mouse_end(self, gesture, dx, dy):
        self._finish()

    # -- drawing ---------------------------------------------------------------
    def _page_surface(self, scale):
        """The page rendered at `scale` device pixels per point (cached)."""
        index = self.window.index
        if self._cache and self._cache[0] == index and abs(self._cache[1] / scale - 1) < 0.15:
            return self._cache[1], self._cache[2]
        w, h = self.page_size()
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, max(1, int(w * scale)), max(1, int(h * scale)))
        cr = cairo.Context(surface)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(scale, scale)
        self.window.page().render(cr)
        self._cache = (index, scale, surface)
        return scale, surface

    def _draw(self, area, cr, width, height):
        if not self.fitted:
            self.fit()
        cr.set_source_rgb(0.85, 0.85, 0.87)
        cr.paint()
        v = self.view
        w, h = self.page_size()
        cr.save()
        cr.translate(v.ox, v.oy)
        scale, surface = self._page_surface(v.zoom * self.get_scale_factor())
        cr.save()
        cr.scale(v.zoom / scale, v.zoom / scale)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()
        cr.scale(v.zoom, v.zoom)
        cr.rectangle(0, 0, w, h)
        cr.clip()
        marks = list(self.window.marks.get(self.window.index, []))
        if self._stroke is not None:
            marks.append(self._stroke)
        draw_marks(cr, marks)
        cr.restore()


class SignWindow(Adw.ApplicationWindow):
    def __init__(self, app, path, on_done):
        """on_done(signed path or None) runs once, when the window closes."""
        super().__init__(application=app, title=os.path.basename(path),
                         default_width=1000, default_height=820)
        self.path = path
        self.on_done = on_done
        self.doc = open_document(path)
        self.index = 0
        self.marks = {}       # page index -> [Stroke | Text]
        self.history = []     # (page index, mark), for undo
        self.pending = None   # marks waiting for a tap to be placed
        self.result = None
        self.signature = load_signature(SIGNATURE)

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda b: self._cancel())
        header.pack_start(cancel)
        self.done = Gtk.Button(label=_("Done"), css_classes=["suggested-action"])
        self.done.connect("clicked", lambda b: self._save())
        header.pack_end(self.done)
        self.title = Adw.WindowTitle(title=os.path.basename(path))
        header.set_title_widget(self.title)
        toolbar.add_top_bar(header)

        self.area = PageArea(self)
        self.banner = Adw.Banner()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        content.append(self.area)
        toolbar.set_content(content)

        bar = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6,
                      css_classes=["toolbar"])
        self.prev = self._tool(bar, "go-previous-symbolic", None, lambda: self.show_page(self.index - 1))
        self.page_label = Gtk.Label(width_chars=8)
        bar.append(self.page_label)
        self.next = self._tool(bar, "go-next-symbolic", None, lambda: self.show_page(self.index + 1))
        bar.append(Gtk.Separator())
        self.undo = self._tool(bar, "edit-undo-symbolic", _("Undo"), self._undo)
        self._tool(bar, "edit-clear-all-symbolic", _("Clear page"), self._clear_page)
        bar.append(Gtk.Separator())
        self.sign_button = self._tool(bar, "document-edit-symbolic", _("My signature"), self._place_signature)
        self.keep_button = self._tool(bar, "document-save-symbolic", _("Keep as my signature"),
                                      self._keep_signature)
        self._tool(bar, "x-office-calendar-symbolic", _("Date"), self._place_date)
        self._tool(bar, "insert-text-symbolic", _("Text"), self._ask_text)
        bar.append(Gtk.Separator())
        self._tool(bar, "zoom-fit-best-symbolic", _("Fit"), self.area.fit)
        toolbar.add_bottom_bar(bar)
        self.set_content(toolbar)

        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>z"),
                                           Gtk.CallbackAction.new(lambda *a: self._undo() or True)))
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                           Gtk.CallbackAction.new(lambda *a: self._cancel() or True)))
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self.show_page(0)

    def _tool(self, bar, icon, label, callback):
        child = Adw.ButtonContent(icon_name=icon, label=label) if label else None
        button = Gtk.Button(css_classes=["flat"])
        if child:
            button.set_child(child)
        else:
            button.set_icon_name(icon)
        button.connect("clicked", lambda b: callback())
        bar.append(button)
        return button

    # -- pages and marks -------------------------------------------------------
    def page(self):
        return self.doc.get_page(self.index)

    def show_page(self, index):
        n = self.doc.get_n_pages()
        self.index = max(0, min(n - 1, index))
        self.page_label.set_label(_("Page {n} of {total}").format(n=self.index + 1, total=n))
        self.prev.set_sensitive(self.index > 0)
        self.next.set_sensitive(self.index < n - 1)
        self.area.fitted = False
        self._update()

    def add_mark(self, mark):
        self.marks.setdefault(self.index, []).append(mark)
        self.history.append((self.index, mark))
        self._update()

    def _undo(self):
        if self.history:
            index, mark = self.history.pop()
            self.marks[index].remove(mark)
            if index != self.index:
                self.show_page(index)
        self._update()

    def _clear_page(self):
        for mark in self.marks.pop(self.index, []):
            self.history = [(i, m) for i, m in self.history if m is not mark]
        self._update()

    def _update(self):
        strokes = [m for m in self.marks.get(self.index, []) if isinstance(m, Stroke)]
        self.undo.set_sensitive(bool(self.history))
        self.sign_button.set_sensitive(self.signature is not None)
        self.keep_button.set_visible(self.signature is None and bool(strokes))
        self.done.set_sensitive(any(self.marks.values()))
        self.area.queue_draw()

    # -- placing ---------------------------------------------------------------
    def _arm(self, make_marks, message):
        """Next tap (pen, finger or mouse) places make_marks(x, y)."""
        self.pending = make_marks
        self.banner.set_title(message)
        self.banner.set_revealed(True)

    def place_at(self, x, y):
        """Called by the page on a tap; True if it placed something."""
        if self.pending is None:
            return False
        for mark in self.pending(x, y):
            self.add_mark(mark)
        self.pending = None
        self.banner.set_revealed(False)
        return True

    def _place_signature(self):
        if self.signature:
            self._arm(lambda x, y: place(self.signature, x, y), _("Tap where your signature goes"))

    def _keep_signature(self):
        strokes = [m for m in self.marks.get(self.index, []) if isinstance(m, Stroke)]
        if strokes:
            save_signature(SIGNATURE, strokes)
            self.signature = load_signature(SIGNATURE)
            self._update()
            self.banner.set_title(_("Saved: next time, “My signature” places it with one tap"))
            self.banner.set_revealed(True)
            GLib.timeout_add_seconds(4, once(self.banner.set_revealed, False))

    def _place_date(self):
        today = datetime.date.today().strftime("%d/%m/%Y")
        self._arm(lambda x, y: [Text(x, y, today)], _("Tap where the date goes"))

    def _ask_text(self):
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Text to add"))
        entry = Gtk.Entry(activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("ok", _("Place"))
        dialog.set_default_response("ok")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)

        def answered(d, response):
            text = entry.get_text().strip()
            if response == "ok" and text:
                self._arm(lambda x, y: [Text(x, y, text)], _("Tap where the text goes"))
        dialog.connect("response", answered)
        dialog.present()

    # -- closing ---------------------------------------------------------------
    def _save(self, dst=None):
        self.done.set_sensitive(False)
        self.banner.set_title(_("Saving…"))
        self.banner.set_revealed(True)
        marks = {i: list(m) for i, m in self.marks.items() if m}

        def work():
            try:
                result = save_signed(self.path, marks, dst)
            except Exception as e:  # noqa: BLE001 - shown to the user
                log("signing: could not save:", repr(e))
                result = e
            GLib.idle_add(once(self._saved, result, dst is None))
        threading.Thread(target=work, daemon=True).start()

    def _saved(self, result, next_to_original):
        if isinstance(result, PermissionError) and next_to_original:
            # A read-only folder, or a Flatpak that may only touch the file it
            # was given: ask where to put the signed copy.
            self._save_as()
            return
        if isinstance(result, Exception):
            self.banner.set_title(_("Could not save: {error}").format(error=result))
            self.done.set_sensitive(True)
            return
        self.result = result
        self.close()

    def _save_as(self):
        self.banner.set_title(_("Choose where to save the signed copy"))
        name = os.path.basename(output_path(self.path))
        dialog = Gtk.FileDialog(title=_("Save the signed PDF"), initial_name=name)

        def chosen(dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                file = None  # cancelled: keep the marks, let the user retry
            if file is None or not file.get_path():
                self.banner.set_revealed(False)
                self.done.set_sensitive(True)
                return
            self._save(file.get_path())
        dialog.save(self, None, chosen)

    def _cancel(self):
        if not any(self.marks.values()):
            self.close()
            return
        dialog = Adw.MessageDialog(transient_for=self, heading=_("Discard the signature?"))
        dialog.add_response("keep", _("Keep signing"))
        dialog.add_response("discard", _("Discard"))
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda d, r: r == "discard" and self._discard())
        dialog.present()

    def _discard(self):
        self.marks.clear()
        self.close()

    def _on_close(self, _window):
        callback, self.on_done = self.on_done, None
        if callback:
            callback(self.result)
        return False
