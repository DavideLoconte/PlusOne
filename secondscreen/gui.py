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

from . import add_log_handler  # noqa: E402
from .engine import Controller, Settings, State  # noqa: E402
from .rtsp import MODES  # noqa: E402

APP_ID = "io.github.santal.SchermoTablet"
CONFIG = os.path.join(GLib.get_user_config_dir(), "secondscreen", "settings.json")
MODE_LIST = sorted(((w, h, f) for (w, h, f) in MODES if f == 60), key=lambda m: -m[0] * m[1])
STATUS = {
    State.IDLE: ("video-display-symbolic", "Pronto",
                 "Premi Avvia, poi tocca «Second screen» nel pannello rapido del tablet"),
    State.WAITING: ("network-wireless-symbolic", "In attesa del tablet", None),
    State.CONNECTING: ("network-transmit-receive-symbolic", "Collegamento…", None),
    State.STREAMING: ("emblem-ok-symbolic", "Connesso", None),
    State.ERROR: ("dialog-warning-symbolic", "Problema", None),
}


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


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Schermo Tablet", default_width=480,
                         default_height=720)
        self.settings = load_settings()
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
        self.button = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["pill"],
                                 margin_top=12)
        self.button.connect("clicked", self._toggle)
        for widget in (self.status_icon, self.status_title, self.status_detail, self.button):
            box.append(widget)
        header = Adw.PreferencesGroup()
        header.add(box)
        page.add(header)

        screen = Adw.PreferencesGroup(title="Schermo")
        self.mode_row = Adw.ComboRow(title="Risoluzione",
                                     model=Gtk.StringList.new([self._mode_label(m) for m in MODE_LIST]))
        current = (self.settings.width, self.settings.height, self.settings.fps)
        self.mode_row.set_selected(MODE_LIST.index(current) if current in MODE_LIST else 0)
        screen.add(self.mode_row)
        self.bitrate_row = Adw.SpinRow.new_with_range(5, 60, 1)
        self.bitrate_row.set_title("Qualità")
        self.bitrate_row.set_subtitle("Mbit/s: più alto = più nitido, più banda")
        self.bitrate_row.set_value(self.settings.bitrate / 1000)
        screen.add(self.bitrate_row)
        page.add(screen)

        options = Adw.PreferencesGroup(title="Opzioni")
        self.touch_row = Adw.SwitchRow(title="Touch dal tablet", active=self.settings.touch)
        self.ready_row = Adw.SwitchRow(title="Resta pronto",
                                       subtitle="Si ricollega quando riapri Second Screen",
                                       active=self.settings.stay_ready)
        self.adb_row = Adw.SwitchRow(title="Apri Second Screen da qui",
                                     subtitle="Usa adb: serve il debug wireless sul tablet",
                                     active=self.settings.use_adb)
        for row in (self.touch_row, self.ready_row, self.adb_row):
            options.add(row)
        page.add(options)
        self.setting_rows = [self.mode_row, self.bitrate_row, self.touch_row, self.adb_row]

        log_group = Adw.PreferencesGroup()
        expander = Gtk.Expander(label="Registro")
        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow(min_content_height=180, child=self.log_view)
        expander.set_child(scroller)
        log_group.add(expander)
        page.add(log_group)
        add_log_handler(lambda line: GLib.idle_add(self._append_log, line))

        self.connect("close-request", self._on_close)
        self._on_state(State.IDLE, "")

    @staticmethod
    def _mode_label(mode):
        w, h, f = mode
        ratio = "16:10" if w * 10 == h * 16 else "16:9" if w * 9 == h * 16 else ""
        return f"{w} × {h} ({ratio})" if ratio else f"{w} × {h}"

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
        if self.controller.state == State.IDLE or (
                self.controller.state == State.ERROR and not self.controller._running):
            self._apply_settings()
            self.controller.start()
        else:
            self.controller.stop()

    def _on_state(self, state, detail):
        icon, title, default = STATUS[state]
        self.status_icon.set_from_icon_name(icon)
        self.status_title.set_label(title)
        self.status_detail.set_label(detail or default or "")
        running = self.controller._running
        self.button.set_label("Ferma" if running else "Avvia")
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
