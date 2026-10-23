"""Plus One as a GNOME application that keeps running in the background.

One process holds the engine and offers it three ways: a D-Bus interface for
other front ends (the Quick Settings extension), notifications, and the
settings window, created only when asked for. With the background mode on it
starts at login (`--gapplication-service`, no window) and idles at no cost, so
the Quick Settings toggle connects at once.
"""
import collections
import json
import os
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import Adw, Gio, GLib, Gst  # noqa: E402

from . import add_log_handler, i18n, log, once  # noqa: E402
from .engine import Controller, Settings, State  # noqa: E402
from .i18n import _  # noqa: E402
from .signing import Signing  # noqa: E402

APP_ID = "io.github.gabrielesantangelo.PlusOne"
INTERFACE = APP_ID + "1"
INTERFACE_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Connect"/>
    <method name="Disconnect"/>
    <method name="ShowWindow"/>
    <method name="SignDocument">
      <arg type="s" name="path" direction="in"/>
    </method>
    <property name="State" type="s" access="read"/>
    <property name="Active" type="b" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Detail" type="s" access="read"/>
    <property name="Background" type="b" access="readwrite"/>
  </interface>
</node>"""
CONFIG = os.path.join(GLib.get_user_config_dir(), "plus-one", "settings.json")
IN_FLATPAK = os.path.exists("/.flatpak-info")
TITLES = {
    State.IDLE: "Ready",
    State.WAITING: "Waiting for the tablet",
    State.CONNECTING: "Connecting…",
    State.STREAMING: "Connected",
    State.ERROR: "Problem",
}
EXTENSION = "plus-one@gabrielesantangelo.github.io"
IDLE_HINT = "Press Connect, then tap “Second screen” in the tablet's quick settings"
NOTIFICATION = "status"


def load_settings(path=CONFIG):
    try:
        with open(path) as f:
            data = json.load(f)
        known = Settings.__dataclass_fields__
        return Settings(**{k: v for k, v in data.items() if k in known})
    except (OSError, ValueError, TypeError):
        return Settings()


def save_settings(settings):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        json.dump(asdict(settings), f, indent=2)


def launcher():
    """Command that starts this installation of Plus One."""
    if IN_FLATPAK or shutil.which("plus-one"):
        return "plus-one"
    return str(Path(__file__).resolve().parents[1] / "bin" / "plus-one")


def set_autostart(enabled, config_dir=None):
    """Start in the background at login: an XDG autostart entry, or the
    Background portal inside a Flatpak (which cannot write ~/.config/autostart)."""
    if IN_FLATPAK:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        options = {"reason": GLib.Variant("s", _("Keeps the Quick Settings toggle ready to connect the tablet")),
                   "autostart": GLib.Variant("b", enabled),
                   "commandline": GLib.Variant("as", ["plus-one", "--gapplication-service"]),
                   "dbus-activatable": GLib.Variant("b", False)}
        bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                 "org.freedesktop.portal.Background", "RequestBackground",
                 GLib.Variant("(sa{sv})", ("", options)), None, Gio.DBusCallFlags.NONE, -1,
                 None, None, None)
        return
    path = Path(config_dir or GLib.get_user_config_dir()) / "autostart" / f"{APP_ID}.desktop"
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\nType=Application\nName=Plus One\n"
                    f"Exec={launcher()} --gapplication-service\nIcon={APP_ID}\n"
                    "NoDisplay=true\nX-GNOME-Autostart-enabled=true\n")


def _shell_extensions_call(method, args, reply_type, callback):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def done(conn, res):
        try:
            result = conn.call_finish(res).unpack()
        except GLib.Error:
            result = None  # not GNOME Shell, or no access from a sandbox
        if callback:
            callback(result)

    bus.call("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions", "org.gnome.Shell.Extensions",
             method, args, reply_type, Gio.DBusCallFlags.NONE, -1, None, done)


def shell_extension_state(callback):
    """callback(None) if the Quick Settings toggle is not installed (or the
    shell cannot tell), else callback(True/False) for enabled or not."""
    def got(result):
        info = result[0] if result else {}
        callback(info.get("state") == 1 if info else None)
    _shell_extensions_call("GetExtensionInfo", GLib.Variant("(s)", (EXTENSION,)),
                           GLib.VariantType("(a{sv})"), got)


def set_shell_extension(enabled, callback=None):
    """Only ever on the user's explicit request: Plus One never turns shell
    extensions on by itself."""
    log("Quick Settings toggle", "on" if enabled else "off")
    _shell_extensions_call("EnableExtension" if enabled else "DisableExtension",
                           GLib.Variant("(s)", (EXTENSION,)), GLib.VariantType("(b)"),
                           callback)


class PlusOneApp(Adw.Application):
    def __init__(self):
        # Command line: `--sign [FILE...]`; D-Bus Open: files to sign.
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                         | Gio.ApplicationFlags.HANDLES_OPEN)
        # Started by D-Bus activation (Quick Settings): stay long enough to
        # take the call that woke us up.
        self.set_inactivity_timeout(10000)
        self.settings = load_settings()
        i18n.set_language(self.settings.language)
        self.controller = Controller(self.settings, self._on_state)
        self.listeners = []      # windows following the state
        self.log_listeners = []  # windows showing the log
        self.log_lines = collections.deque(maxlen=400)
        add_log_handler(lambda line: GLib.idle_add(once(self._on_log, line)))
        self._held = False
        self._registration = None
        for name, callback in (("connect", lambda *a: self.controller.start()),
                               ("disconnect", lambda *a: self.controller.stop()),
                               ("show", lambda *a: self.activate())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.signing = Signing(self)

    # -- GApplication ------------------------------------------------------
    def do_startup(self):
        Adw.Application.do_startup(self)
        Gst.init(None)
        if self.settings.background:
            set_autostart(True)
        self._update_hold()

    def do_activate(self):
        from .gui import Window
        window = self.get_active_window() or Window(self)
        window.present()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        if args[:1] == ["--sign"]:
            paths = [cmdline.create_file_for_arg(arg).get_path() for arg in args[1:]]
            for path in filter(None, paths):
                self.signing.request(path)
            if not args[1:]:
                self._choose_and_sign()
        else:
            self.activate()
        return 0

    def do_open(self, files, n_files, hint):
        for file in files:
            if file.get_path():
                self.signing.request(file.get_path())

    def _choose_and_sign(self):
        from gi.repository import Gtk
        pdfs = Gtk.FileFilter(name="PDF")
        pdfs.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(pdfs)
        dialog = Gtk.FileDialog(title=_("Choose the PDF to sign"), filters=filters)
        self.hold()  # the portal dialog is not one of our windows

        def chosen(dialog, result):
            self.release()
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if file and file.get_path():
                self.signing.request(file.get_path())
        dialog.open(None, None, chosen)

    def do_dbus_register(self, connection, object_path):
        Adw.Application.do_dbus_register(self, connection, object_path)
        info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
        reg = connection.register_object(object_path, info, self._dbus_method,
                                         self._dbus_get, self._dbus_set)
        self._registration = (connection, object_path, reg)
        return True

    def do_dbus_unregister(self, connection, object_path):
        if self._registration:
            connection.unregister_object(self._registration[2])
            self._registration = None
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def _on_log(self, line):
        self.log_lines.append(line)
        for listener in list(self.log_listeners):
            listener(line)

    def window_closed(self):
        # Without the background mode, closing the window ends Plus One.
        if not self.settings.background:
            self.controller.stop()

    def save_settings(self):
        save_settings(self.settings)

    # -- state -------------------------------------------------------------
    def status_text(self):
        """(title, detail) for the current state, in the current language."""
        state = self.controller.state
        template, args = self.controller.detail
        if template:
            detail = _(template).format(**args)
        else:
            detail = _(IDLE_HINT) if state == State.IDLE else ""
        return _(TITLES[state]), detail

    def _on_state(self, state, template, args):
        self._update_hold()
        if not self.signing.on_state(state):
            self._notify(state, template)
        self._emit_properties()
        for listener in list(self.listeners):
            listener()

    def _update_hold(self):
        # Stay without a window while connected, or always in background mode
        # (idle costs nothing: the radio is only used while connecting).
        want = self.settings.background or self.controller.running
        if want and not self._held:
            self.hold()
        elif not want and self._held:
            self.release()
        self._held = want

    def _notify(self, state, template):
        title, detail = self.status_text()
        if state == State.STREAMING:
            note = Gio.Notification.new(_("Tablet connected"))
            note.set_body(detail)
            note.add_button(_("Disconnect"), "app.disconnect")
        elif state == State.ERROR or (state == State.IDLE and template):
            note = Gio.Notification.new(title)
            note.set_body(detail)
            note.add_button(_("Connect"), "app.connect")
        else:
            self.withdraw_notification(NOTIFICATION)
            return
        note.set_default_action("app.show")
        self.send_notification(NOTIFICATION, note)

    def set_background(self, enabled):
        if enabled == self.settings.background:
            return
        self.settings.background = enabled
        save_settings(self.settings)
        set_autostart(enabled)
        self._update_hold()
        self._emit_properties()

    # -- D-Bus -------------------------------------------------------------
    def _properties(self):
        title, detail = self.status_text()
        return {"State": GLib.Variant("s", self.controller.state),
                "Active": GLib.Variant("b", self.controller.running),
                "Title": GLib.Variant("s", title),
                "Detail": GLib.Variant("s", detail),
                "Background": GLib.Variant("b", self.settings.background)}

    def _emit_properties(self):
        if self._registration:
            connection, path, _reg = self._registration
            connection.emit_signal(None, path, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                   GLib.Variant("(sa{sv}as)", (INTERFACE, self._properties(), [])))

    def _dbus_method(self, connection, sender, path, interface, method, params, invocation):
        if method == "Connect":
            self.controller.start()
        elif method == "Disconnect":
            self.controller.stop()
        elif method == "ShowWindow":
            self.activate()
        elif method == "SignDocument":
            self.signing.request(params.unpack()[0])
        invocation.return_value(None)

    def _dbus_get(self, connection, sender, path, interface, prop):
        return self._properties()[prop]

    def _dbus_set(self, connection, sender, path, interface, prop, value):
        if prop == "Background":
            self.set_background(value.unpack())
            return True
        return False


def main(argv=None):
    log("Plus One", sys.argv[1:] or "")
    return PlusOneApp().run(argv if argv is not None else sys.argv[:1])
