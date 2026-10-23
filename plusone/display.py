"""Virtual monitor and remote input through Mutter's private D-Bus APIs.

Unlike the XDG portal, org.gnome.Mutter.ScreenCast/RemoteDesktop need no
consent dialog, so the monitor can appear as soon as the tablet connects.
The virtual monitor takes the size we negotiate on the PipeWire stream and
disappears when the session stops or this process exits.
"""
from gi.repository import Gio, GLib

from . import log

RD_NAME = "org.gnome.Mutter.RemoteDesktop"
SC_NAME = "org.gnome.Mutter.ScreenCast"


def virtual_monitor_edid():
    """(vendor, product, serial) of the newest Mutter virtual monitor, or None."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    state = bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                          "org.gnome.Mutter.DisplayConfig", "GetCurrentState", None, None,
                          Gio.DBusCallFlags.NONE, -1, None).unpack()
    # state[1]: monitors as ((connector, vendor, product, serial), modes, props)
    virtual = [m[0] for m in state[1] if m[0][0].startswith("Meta-")]
    if not virtual:
        return None
    newest = max(virtual, key=lambda spec: int(spec[3], 16))
    return newest[1], newest[2], newest[3]


class VirtualMonitor:
    def __init__(self, on_ready):
        """on_ready(node_id) runs on the GLib loop once PipeWire has the stream."""
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.on_ready = on_ready
        self.rd_session = None
        self.stream = None
        self._sub = None

    def _call(self, name, path, iface, method, args=None, rtype=None):
        return self.bus.call_sync(name, path, iface, method, args,
                                  GLib.VariantType(rtype) if rtype else None,
                                  Gio.DBusCallFlags.NONE, -1, None)

    def start(self):
        self.rd_session = self._call(RD_NAME, "/org/gnome/Mutter/RemoteDesktop", RD_NAME,
                                     "CreateSession", None, "(o)").unpack()[0]
        session_id = self._call(RD_NAME, self.rd_session, "org.freedesktop.DBus.Properties", "Get",
                                GLib.Variant("(ss)", (RD_NAME + ".Session", "SessionId")),
                                "(v)").unpack()[0]
        sc_session = self._call(SC_NAME, "/org/gnome/Mutter/ScreenCast", SC_NAME, "CreateSession",
                                GLib.Variant("(a{sv})", ({"remote-desktop-session-id":
                                                          GLib.Variant("s", session_id)},)),
                                "(o)").unpack()[0]
        # cursor-mode 1 = embedded: the tablet has no cursor of its own.
        self.stream = self._call(SC_NAME, sc_session, SC_NAME + ".Session", "RecordVirtual",
                                 GLib.Variant("(a{sv})", ({"cursor-mode": GLib.Variant("u", 1),
                                                           "is-platform": GLib.Variant("b", True)},)),
                                 "(o)").unpack()[0]
        self._sub = self.bus.signal_subscribe(SC_NAME, SC_NAME + ".Stream", "PipeWireStreamAdded",
                                              self.stream, None, Gio.DBusSignalFlags.NONE,
                                              self._stream_added)
        self._call(RD_NAME, self.rd_session, RD_NAME + ".Session", "Start")
        log("virtual monitor session started")

    def _stream_added(self, conn, sender, path, iface, signal, params):
        node = params.unpack()[0]
        log("virtual monitor PipeWire node", node)
        self.on_ready(node)

    def stop(self):
        if self.rd_session:
            try:
                self._call(RD_NAME, self.rd_session, RD_NAME + ".Session", "Stop")
            except GLib.Error as e:
                log("stopping remote desktop session:", e.message)
            self.rd_session = None
        if self._sub is not None:
            self.bus.signal_unsubscribe(self._sub)
            self._sub = None

    # Input injection, in stream (virtual monitor) coordinates.
    def _notify(self, method, args):
        if self.rd_session:
            self.bus.call(RD_NAME, self.rd_session, RD_NAME + ".Session", method, args,
                          None, Gio.DBusCallFlags.NONE, -1, None, None, None)

    def touch_down(self, slot, x, y):
        self._notify("NotifyTouchDown", GLib.Variant("(sudd)", (self.stream, slot, x, y)))

    def touch_motion(self, slot, x, y):
        self._notify("NotifyTouchMotion", GLib.Variant("(sudd)", (self.stream, slot, x, y)))

    def touch_up(self, slot):
        self._notify("NotifyTouchUp", GLib.Variant("(u)", (slot,)))

    def pointer_move(self, x, y):
        self._notify("NotifyPointerMotionAbsolute", GLib.Variant("(sdd)", (self.stream, x, y)))

    def pointer_button(self, button, pressed):
        self._notify("NotifyPointerButton", GLib.Variant("(ib)", (button, pressed)))
