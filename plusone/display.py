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
    def __init__(self, on_ready, on_closed=None):
        """on_ready(node_id) runs on the GLib loop once PipeWire has the stream;
        on_closed() if the session ends without stop() (GNOME's screen
        sharing indicator stopped it, say)."""
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.on_ready = on_ready
        self.on_closed = on_closed or (lambda: None)
        self.rd_session = None
        self.stream = None
        self._sub = self._closed_sub = None

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
        self._closed_sub = self.bus.signal_subscribe(RD_NAME, RD_NAME + ".Session", "Closed",
                                                     self.rd_session, None, Gio.DBusSignalFlags.NONE,
                                                     self._closed)
        self._call(RD_NAME, self.rd_session, RD_NAME + ".Session", "Start")
        log("virtual monitor session started")

    def _closed(self, *_args):
        if self._closed_sub is None:
            return  # stop()'s own
        log("virtual monitor session closed by GNOME")
        self._unsubscribe_closed()
        self.rd_session = None  # gone already: nothing to stop
        self.on_closed()

    def _unsubscribe_closed(self):
        if self._closed_sub is not None:
            self.bus.signal_unsubscribe(self._closed_sub)
            self._closed_sub = None

    def _stream_added(self, conn, sender, path, iface, signal, params):
        node = params.unpack()[0]
        log("virtual monitor PipeWire node", node)
        self.on_ready(node)

    def stop(self):
        self._unsubscribe_closed()
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


def set_virtual_monitor_scale(scale):
    """The newest virtual monitor at this scale (2: HiDPI), the others as
    they are: whether it could. Mutter forgets a virtual monitor's settings
    (its serial is new every session), so Plus One sets the scale each time,
    through the same configuration call GNOME Settings uses, temporarily."""
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    serial, monitors, logical, _props = bus.call_sync(
        "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig", "org.gnome.Mutter.DisplayConfig",
        "GetCurrentState", None, None, Gio.DBusCallFlags.NONE, -1, None).unpack()
    current = {}  # connector -> (current mode id, its supported scales)
    for spec, modes, _mprops in monitors:
        for mode_id, _w, _h, _rate, _preferred, scales, mode_props in modes:
            if mode_props.get("is-current"):
                current[spec[0]] = (mode_id, scales)
    virtual = [spec for spec, _modes, _p in monitors if spec[0].startswith("Meta-")]
    if not virtual:
        return False
    target = max(virtual, key=lambda spec: int(spec[3], 16))[0]
    if target not in current or not any(abs(s - scale) < 0.01 for s in current[target][1]):
        log(f"display: scale {scale} not offered for the tablet's monitor")
        return False
    config = []
    for x, y, lscale, transform, primary, specs, _lprops in logical:
        connectors = [spec[0] for spec in specs]
        if any(c not in current for c in connectors):
            return False  # a monitor without a current mode: not ours to guess
        config.append((x, y, scale if target in connectors else lscale, transform, primary,
                       [(c, current[c][0], {}) for c in connectors]))
    bus.call_sync("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                  "org.gnome.Mutter.DisplayConfig", "ApplyMonitorsConfig",
                  GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (serial, 1, config, {})),
                  None, Gio.DBusCallFlags.NONE, -1, None)
    log(f"display: the tablet's monitor at scale {scale}")
    return True
