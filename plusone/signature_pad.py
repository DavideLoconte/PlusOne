"""The saved signatures on screen: a list to pick or remove them, a pad to
draw a new one, and a window with the list for Plus One's main window.
They are kept by signatures.py, and shared with the tablet's app."""
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import log, signatures  # noqa: E402
from .i18n import _  # noqa: E402
from .ink import ERASER_RADIUS, NO_PRESSURE, normalize, touches  # noqa: E402
from .pdf import Stroke, draw_marks  # noqa: E402

PAD_POINTS = 280.0  # the pad's width in page points: twice a placed signature
# The S Pen's side button arrives as the second or third button, a mouse's right one as the third.
ERASE_BUTTONS = Gdk.ModifierType.BUTTON2_MASK | Gdk.ModifierType.BUTTON3_MASK


def draw_eraser_icon(area, cr, width, height):
    color = area.get_color()
    cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
    cr.translate(width / 2, height / 2)
    cr.rotate(math.pi / 4)
    # An eraser tilted 45 degrees: solid body, outlined rubber end.
    cr.rectangle(-4, -8, 8, 9)
    cr.fill()
    cr.set_line_width(1.6)
    cr.rectangle(-3.2, 1.8, 6.4, 5.4)
    cr.stroke()


def draw_thumb(cr, signature, width, height, color):
    """The signature fitted into width x height, in the pen's colour, on
    white (as on paper, and seen in the dark style too)."""
    r = 4  # rounded, as the theme's cards
    cr.new_sub_path()
    for x, y, start in ((width - r, r, -math.pi / 2), (width - r, height - r, 0),
                        (r, height - r, math.pi / 2), (r, r, math.pi)):
        cr.arc(x, y, r, start, start + math.pi / 2)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill()
    strokes = signature["strokes"]
    tall = max((pt[1] for s in strokes for pt in s), default=0) or 0.01
    scale = min(width - 6, (height - 6) / tall)
    ox, oy = (width - scale) / 2, (height - tall * scale) / 2
    draw_marks(cr, [Stroke([(ox + x * scale, oy + y * scale, p) for x, y, p in s], width=2.2, color=color)
                    for s in strokes])


class SignaturePad(Adw.Window):
    """Draws a new signature, with the pen (pressure shows) or the mouse,
    and keeps it; on_saved(signature) then. The pen's side button, its
    eraser end, the right mouse button or the eraser button erase whole
    strokes, as in the signing window."""

    def __init__(self, parent, color, pen, on_saved=None):
        super().__init__(transient_for=parent, modal=True, title=_("New signature"),
                         default_width=720, default_height=400)
        self.color, self.pen, self.on_saved = color, pen, on_saved
        self.strokes = []  # lists of (x, y, pressure) in pad pixels
        self.current = None
        self.erasing = None  # the eraser's last point while it is down

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda b: self.close())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=_("Save"), css_classes=["suggested-action"], sensitive=False)
        self.save_button.connect("clicked", lambda b: self._save())
        header.pack_end(self.save_button)
        self.clear_button = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text=_("Start over"),
                                       sensitive=False)
        self.clear_button.connect("clicked", lambda b: self._clear())
        header.pack_end(self.clear_button)
        eraser_icon = Gtk.DrawingArea(content_width=20, content_height=20)
        eraser_icon.set_draw_func(draw_eraser_icon)
        self.eraser = Gtk.ToggleButton(child=eraser_icon, tooltip_text=_("Eraser"))
        header.pack_end(self.eraser)

        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, cursor=Gdk.Cursor.new_from_name("crosshair"))
        self.area.set_draw_func(self._draw)
        self.drag_gesture = Gtk.GestureDrag(button=0)
        self.drag_gesture.connect("drag-begin", self._begin)
        self.drag_gesture.connect("drag-update", self._move)
        self.drag_gesture.connect("drag-end", self._end)
        self.area.add_controller(self.drag_gesture)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        box.append(self.area)
        box.append(Gtk.Label(label=_("Sign here with the pen or the mouse"), css_classes=["dim-label"]))
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        self.set_content(view)
        self.origin = (0, 0)

    def _sample(self, gesture, x, y):
        event = gesture.get_current_event()
        has, pressure = event.get_axis(Gdk.AxisUse.PRESSURE) if event else (False, 0)
        return x, y, pressure if has and pressure > 0 else NO_PRESSURE

    def _is_eraser(self, gesture):
        if self.eraser.get_active() or gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            return True
        event = gesture.get_current_event()
        tool = event.get_device_tool() if event else None
        if tool is not None and tool.get_tool_type() == Gdk.DeviceToolType.ERASER:
            return True
        return bool(gesture.get_current_event_state() & ERASE_BUTTONS)

    def _begin(self, gesture, x, y):
        self.origin = (x, y)
        if self._is_eraser(gesture):
            self.erasing = (x, y)
            self._erase(x, y)
            return
        self.current = [self._sample(gesture, x, y)]
        self.strokes.append(self.current)
        self.area.queue_draw()

    def _move(self, gesture, dx, dy):
        x, y = self.origin[0] + dx, self.origin[1] + dy
        if self.erasing is not None:
            self._erase(x, y)
        elif self.current is not None:
            self.current.append(self._sample(gesture, x, y))
            self.area.queue_draw()

    def _end(self, gesture, dx, dy):
        self.current = self.erasing = None
        self._changed()
        self.area.queue_draw()

    def _erase(self, x, y):
        """Removes the strokes the eraser touches on its way to x, y."""
        a, self.erasing = self.erasing, (x, y)
        width = self.pen * self.area.get_width() / PAD_POINTS
        self.strokes[:] = [s for s in self.strokes
                           if not touches(Stroke(s, width=width), a, (x, y), ERASER_RADIUS)]
        self.area.queue_draw()

    def _changed(self):
        self.save_button.set_sensitive(bool(self.strokes))
        self.clear_button.set_sensitive(bool(self.strokes))

    def _clear(self):
        self.strokes.clear()
        self._changed()
        self.area.queue_draw()

    def _draw(self, area, cr, width, height):
        # A white card with a line to sign on.
        cr.set_source_rgb(1, 1, 1)
        radius = 12
        cr.new_sub_path()
        for x, y, start in ((width - radius, radius, -90), (width - radius, height - radius, 0),
                            (radius, height - radius, 90), (radius, radius, 180)):
            cr.arc(x, y, radius, math.radians(start), math.radians(start + 90))
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(1)
        cr.move_to(24, round(height * 0.72) + 0.5)
        cr.line_to(width - 24, round(height * 0.72) + 0.5)
        cr.stroke()
        scale = width / PAD_POINTS
        draw_marks(cr, [Stroke(s, width=self.pen * scale, color=self.color) for s in self.strokes])
        if self.erasing is not None:
            cr.set_source_rgba(0.3, 0.3, 0.3, 0.8)
            cr.set_line_width(1.5)
            cr.arc(*self.erasing, ERASER_RADIUS, 0, 2 * math.pi)
            cr.stroke()

    def _save(self):
        scale = self.area.get_width() / PAD_POINTS
        strokes = [Stroke([(x / scale, y / scale, p) for x, y, p in s], width=self.pen)
                   for s in self.strokes if s]
        if not strokes:
            return
        try:
            kept = signatures.add(normalize(strokes))
        except (OSError, ValueError) as e:
            log("signature not saved:", repr(e))  # the pad stays open: nothing drawn is lost
            return
        self.close()
        if self.on_saved:
            self.on_saved(kept)


class SignatureList(Gtk.Box):
    """The saved signatures, each with a button to remove it, and a button
    to draw a new one. With on_pick(signature), each is a button too."""

    def __init__(self, color, pen, on_pick=None, on_new=None, on_change=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.color, self.pen = color, pen
        self.on_pick, self.on_new, self.on_change = on_pick, on_new, on_change
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.append(self.rows)
        new = Gtk.Button(child=Adw.ButtonContent(icon_name="list-add-symbolic", label=_("New signature")),
                         css_classes=["flat"])
        new.connect("clicked", lambda b: self._new())
        self.append(new)
        self.reload()

    def reload(self):
        while (child := self.rows.get_first_child()) is not None:
            self.rows.remove(child)
        found, _removed = signatures.load()
        if not found:
            self.rows.append(Gtk.Label(label=_("No signature saved yet"), css_classes=["dim-label"],
                                       margin_top=12, margin_bottom=12))
        for signature in found:
            thumb = Gtk.DrawingArea(content_width=180, content_height=60)
            thumb.set_draw_func(lambda a, cr, w, h, s=signature: draw_thumb(cr, s, w, h, self.color))
            row = Gtk.Box(spacing=4)
            if self.on_pick:
                pick = Gtk.Button(child=thumb, css_classes=["flat"], tooltip_text=_("Place this signature"))
                pick.connect("clicked", lambda b, s=signature: self.on_pick(s))
                row.append(pick)
            else:
                row.append(Gtk.Frame(child=thumb, css_classes=["signature-card"]))
            remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Remove"),
                                css_classes=["flat"], valign=Gtk.Align.CENTER)
            remove.connect("clicked", lambda b, s=signature: self._ask_remove(s))
            row.append(remove)
            self.rows.append(row)

    def _new(self):
        if self.on_new:
            self.on_new()
            return
        SignaturePad(self.get_root(), self.color, self.pen, on_saved=lambda s: self._changed()).present()

    def _ask_remove(self, signature):
        dialog = Adw.MessageDialog(transient_for=self.get_root(), heading=_("Remove this signature?"),
                                   body=_("It goes from the tablet too, the next time you sign there."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("remove", _("Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(d, response):
            if response == "remove":
                signatures.remove(signature["id"])
                self._changed()
        dialog.connect("response", answered)
        dialog.present()

    def _changed(self):
        self.reload()
        if self.on_change:
            self.on_change()


class SignaturesWindow(Adw.Window):
    """The saved signatures, from Plus One's main window."""

    def __init__(self, parent, color, pen):
        super().__init__(transient_for=parent, modal=True, title=_("My signatures"),
                         default_width=420, default_height=520)
        content = SignatureList(color, pen)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(
            child=Adw.Clamp(child=content, maximum_size=360), hscrollbar_policy=Gtk.PolicyType.NEVER))
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)
