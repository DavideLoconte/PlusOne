"""Nautilus: "Sign on tablet" for PDF files. Plus One does the work; this
only asks it over D-Bus (starting it if needed)."""
import os

from gi.repository import Gio, GLib, GObject, Nautilus


def _label():
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "Firma sul tablet" if value.startswith("it") else "Sign on Tablet"
    return "Sign on Tablet"


class PlusOneSign(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        paths = [f.get_location().get_path() for f in files
                 if f.get_mime_type() == "application/pdf" and f.get_location().get_path()]
        if not paths or len(paths) != len(files):
            return []
        item = Nautilus.MenuItem(name="PlusOne::Sign", label=_label())
        item.connect("activate", self._sign, paths)
        return [item]

    def _sign(self, _item, paths):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        for path in paths:
            bus.call("io.github.gabrielesantangelo.PlusOne", "/io/github/gabrielesantangelo/PlusOne",
                     "io.github.gabrielesantangelo.PlusOne1", "SignDocument",
                     GLib.Variant("(s)", (path,)), None, Gio.DBusCallFlags.NONE, -1, None, None, None)
