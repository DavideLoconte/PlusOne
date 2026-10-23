"""GTK4 / libadwaita front end: one button, a status, a few settings."""
import json
import os
import sys
from dataclasses import asdict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import Adw, GLib, Gst, Gtk  # noqa: E402

from . import add_log_handler, i18n  # noqa: E402
from .engine import Controller, Settings, State  # noqa: E402
from .i18n import _  # noqa: E402
from .rtsp import MODES  # noqa: E402

APP_ID = "io.github.santal.PlusOne"
CONFIG = os.path.join(GLib.get_user_config_dir(), "plus-one", "settings.json")
MODE_LIST = sorted(((w, h, f) for (w, h, f) in MODES if f == 60), key=lambda m: -m[0] * m[1])
STATUS = {
    State.IDLE: ("video-display-symbolic", "Ready"),
    State.WAITING: ("network-wireless-symbolic", "Waiting for the tablet"),
    State.CONNECTING: ("network-transmit-receive-symbolic", "Connecting…"),
    State.STREAMING: ("emblem-ok-symbolic", "Connected"),
    State.ERROR: ("dialog-warning-symbolic", "Problem"),
}
IDLE_HINT = "Press Start, then tap “Second screen” in the tablet's quick settings"


def load_settings():
    try:
        with open(CONFIG) as f:
            data = json.load(f)
        known = Settings.__dataclass_fields__
        return Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(asdict(settings), f, indent=2)


def mode_label(mode):
    w, h, _f = mode
    ratio = "16:10" if w * 10 == h * 16 else "16:9" if w * 9 == h * 16 else ""
    return f"{w} × {h} ({ratio})" if ratio else f"{w} × {h}"


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Plus One", default_width=480, default_height=760)
        self.settings = load_settings()
        i18n.set_language(self.settings.language)
        self.controller = Controller(self.settings, self._on_state)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_content(toolbar)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)

        # A hand-made status block: Adw.StatusPage scrolls on its own and gets
        # clipped inside a PreferencesPage.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12)
        self.status_icon = Gtk.Image(pixel_size=96, css_classes=["dim-label"])
        self.status_title = Gtk.Label(css_classes=["title-1"], wrap=True,
                                      justify=Gtk.Justification.CENTER)
        self.status_detail = Gtk.Label(css_classes=["dim-label"], wrap=True,
                                       justify=Gtk.Justification.CENTER)
        self.button = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["pill"], margin_top=12)
        self.button.connect("clicked", self._toggle)
        for widget in (self.status_icon, self.status_title, self.status_detail, self.button):
            box.append(widget)
        header = Adw.PreferencesGroup()
        header.add(box)
        page.add(header)

        self.display_group = Adw.PreferencesGroup()
        self.mode_row = Adw.ComboRow(model=Gtk.StringList.new([mode_label(m) for m in MODE_LIST]))
        current = (self.settings.width, self.settings.height, self.settings.fps)
        self.mode_row.set_selected(MODE_LIST.index(current) if current in MODE_LIST else 0)
        self.bitrate_row = Adw.SpinRow.new_with_range(5, 60, 1)
        self.bitrate_row.set_value(self.settings.bitrate / 1000)
        self.display_group.add(self.mode_row)
        self.display_group.add(self.bitrate_row)
        page.add(self.display_group)

        self.options_group = Adw.PreferencesGroup()
        self.touch_row = Adw.SwitchRow(active=self.settings.touch)
        self.ready_row = Adw.SwitchRow(active=self.settings.stay_ready)
        self.adb_row = Adw.SwitchRow(active=self.settings.use_adb)
        self.language_row = Adw.ComboRow(model=Gtk.StringList.new(
            [i18n.LANGUAGE_NAMES[code] for code in i18n.LANGUAGES]))
        language = self.settings.language if self.settings.language in i18n.LANGUAGES else "auto"
        self.language_row.set_selected(i18n.LANGUAGES.index(language))
        self._language_handler = self.language_row.connect("notify::selected", self._on_language)
        for row in (self.touch_row, self.ready_row, self.adb_row, self.language_row):
            self.options_group.add(row)
        page.add(self.options_group)
        self.setting_rows = [self.mode_row, self.bitrate_row, self.touch_row, self.adb_row]

        log_group = Adw.PreferencesGroup()
        self.log_expander = Gtk.Expander()
        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_expander.set_child(Gtk.ScrolledWindow(min_content_height=180, child=self.log_view))
        log_group.add(self.log_expander)
        page.add(log_group)
        add_log_handler(lambda line: GLib.idle_add(self._append_log, line))

        self.connect("close-request", self._on_close)
        self._translate()

    # -- language --------------------------------------------------------------
    def _translate(self):
        """(Re)apply every user-visible string in the current language."""
        self.display_group.set_title(_("Display"))
        self.mode_row.set_title(_("Resolution"))
        self.bitrate_row.set_title(_("Quality"))
        self.bitrate_row.set_subtitle(_("Mbit/s: higher is sharper but uses more bandwidth"))
        self.options_group.set_title(_("Options"))
        self.touch_row.set_title(_("Touch and S Pen"))
        self.ready_row.set_title(_("Stay ready"))
        self.ready_row.set_subtitle(_("Reconnects when you reopen Second Screen"))
        self.adb_row.set_title(_("Open Second Screen from here"))
        self.adb_row.set_subtitle(_("Uses adb: needs wireless debugging on the tablet"))
        self.language_row.set_title(_("Language"))
        self.log_expander.set_label(_("Log"))
        # Only "System" needs translating; rebuild the model without re-firing.
        with self.language_row.handler_block(self._language_handler):
            selected = self.language_row.get_selected()
            self.language_row.set_model(Gtk.StringList.new(
                [_(i18n.LANGUAGE_NAMES[code]) for code in i18n.LANGUAGES]))
            self.language_row.set_selected(selected)
        template, args = self.controller.detail
        self._on_state(self.controller.state, template, args)

    def _on_language(self, row, _pspec):
        self.settings.language = i18n.LANGUAGES[row.get_selected()]
        i18n.set_language(self.settings.language)
        save_settings(self.settings)
        # Not from inside this notify emission: _translate swaps the row's model.
        GLib.idle_add(lambda: self._translate() and False)

    # -- controller ------------------------------------------------------------
    def _apply_settings(self):
        w, h, f = MODE_LIST[self.mode_row.get_selected()]
        s = self.settings
        s.width, s.height, s.fps = w, h, f
        s.bitrate = int(self.bitrate_row.get_value() * 1000)
        s.touch = self.touch_row.get_active()
        s.stay_ready = self.ready_row.get_active()
        s.use_adb = self.adb_row.get_active()
        save_settings(s)

    def _toggle(self, _button):
        if self.controller._running:
            self.controller.stop()
        else:
            self._apply_settings()
            self.controller.start()

    def _on_state(self, state, template, args):
        icon, title = STATUS[state]
        self.status_icon.set_from_icon_name(icon)
        self.status_title.set_label(_(title))
        if template:
            detail = _(template).format(**args)
        else:
            detail = _(IDLE_HINT) if state == State.IDLE else ""
        self.status_detail.set_label(detail)
        running = self.controller._running
        self.button.set_label(_("Stop") if running else _("Start"))
        self.button.set_css_classes(["pill", "destructive-action" if running else "suggested-action"])
        for row in self.setting_rows:
            row.set_sensitive(not running)

    def _append_log(self, line):
        buf = self.log_view.get_buffer()
        buf.insert(buf.get_end_iter(), line + "\n")
        if buf.get_line_count() > 400:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_line(100)[1])
        self.log_view.scroll_to_iter(buf.get_end_iter(), 0, False, 0, 0)
        return False

    def _on_close(self, _window):
        self.controller.stop()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.connect("activate", self._activate)

    def _activate(self, app):
        win = self.get_active_window() or Window(self)
        win.present()


def main():
    Gst.init(None)
    return App().run(sys.argv[:1])
