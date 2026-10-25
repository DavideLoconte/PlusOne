"""S Pen from the tablet: a real Linux tablet device when possible.

Second Screen sends the pen as a UIBC HIDC "Gesture" device: first its HID
report descriptor (digitizer pen, report ID 0x14: tip/barrel/invert/eraser/
in-range bits, X/Y 0..32767, pressure 0..4096, X/Y tilt -90..90), then
10-byte input reports. If /dev/uhid is writable we create a virtual HID
device with that same descriptor (S_PEN, never the one received: whatever
reaches the UIBC port must not get to describe a keyboard) and replay the
reports that fit it, so the kernel creates a proper tablet (pressure and
tilt reach Krita, GIMP, Xournal++...). Otherwise the pen drives the pointer
through Mutter's remote desktop session.

The reports are also published, all of them, to readers in this process
(`feed`): the signing window draws with those.
"""
import collections
import os
import select
import socket
import struct
import threading
import time

from gi.repository import Gio, GLib

from . import log
from .display import virtual_monitor_edid

UHID_PATH = "/dev/uhid"
UHID_DESTROY, UHID_CREATE2, UHID_INPUT2 = 1, 11, 12
BUS_VIRTUAL = 0x06
VENDOR, PRODUCT = 0x04E8, 0x5350  # Samsung vendor id, "SP"
DEVICE_NAME = "Plus One S Pen"
TABLET_SCHEMA = "org.gnome.desktop.peripherals.tablet"
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
TIP, BARREL, IN_RANGE = 0x01, 0x02, 0x10
AXIS_MAX = 32767
PRESSURE_MAX = 4096
REPORT_ID, REPORT_SIZE = 0x14, 10
# The Galaxy Tab's S Pen as Second Screen describes it (one report, 0x14):
# Digitizer / Pen / Stylus with tip, barrel, invert, eraser and in-range
# bits, X and Y 0..32767, tip pressure 0..4096, X and Y tilt -90..90.
S_PEN = bytes.fromhex(
    "050d0902a10185140920a10009420944093c0945093215002501750195058102950381030501"
    "093075109501a4550d65133500463a2027ff7f000081020931462c1827ff7f00008102b4050d"
    "093026001081027508093d15a6255a8102093e8102c0c0")


def is_report(data):
    """Whether data is a pen report that fits S_PEN."""
    return len(data) == REPORT_SIZE and data[0] == REPORT_ID


def parse_report(data):
    """(bits, x, y, pressure) of a pen report, all but bits 0..1; None if
    it is not one."""
    if len(data) < 8 or data[0] != 0x14:
        return None
    bits, x, y, pressure = struct.unpack("<BHHH", data[1:8])
    return bits, x / AXIS_MAX, y / AXIS_MAX, min(1.0, pressure / PRESSURE_MAX)


class Feed:
    """Every pen report from the tablet (about 285 a second), for readers in
    this process, as (time, bits, x, y, pressure): they get each one as it
    arrives, a few milliseconds before the desktop delivers it, and whatever
    the desktop merges."""

    def __init__(self):
        self.readers = []

    def listen(self):
        reader = collections.deque(maxlen=4096)
        self.readers = self.readers + [reader]
        return reader

    def unlisten(self, reader):
        self.readers = [r for r in self.readers if r is not reader]

    def publish(self, data):
        readers = self.readers  # called from the UIBC thread
        if readers:
            sample = parse_report(data)
            if sample:
                for reader in readers:
                    reader.append((time.monotonic(),) + sample)


feed = Feed()


HELPER = "io.github.gabrielesantangelo.PlusOne.Pen"  # data/pen-helper
HELPER_PATH = "/io/github/gabrielesantangelo/PlusOne/Pen"


def helper_bus():
    """The bus Plus One's pen helper is on (the system's, or for trying it
    out the session's), or None when it is not there."""
    for bus_type in (Gio.BusType.SYSTEM, Gio.BusType.SESSION):
        try:
            bus = Gio.bus_get_sync(bus_type, None)
            names = set()
            for method in ("ListNames", "ListActivatableNames"):
                names.update(bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                           method, None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE,
                                           2000, None).unpack()[0])
            if HELPER in names:
                return bus
        except GLib.Error:
            continue
    return None


def uhid_available():
    """Whether the pen can be a tablet with pressure: the helper, or /dev/uhid itself."""
    return helper_bus() is not None or os.access(UHID_PATH, os.R_OK | os.W_OK)


def tablet_pen():
    """The S Pen as a pen tablet: through the helper (the only way with the
    .deb: no group with /dev/uhid), else /dev/uhid directly if this user may."""
    bus = helper_bus()
    if bus is not None:
        try:
            reply, fds = bus.call_with_unix_fd_list_sync(HELPER, HELPER_PATH, HELPER, "Open", None,
                                                         GLib.VariantType("(h)"), Gio.DBusCallFlags.NONE,
                                                         10000, None, None)
            return HelperPen(socket.socket(fileno=fds.get(reply.unpack()[0])))
        except GLib.Error as e:
            log("pen: the helper said no:", e.message)
    if os.access(UHID_PATH, os.R_OK | os.W_OK):
        return UhidPen()
    return None


def uhid_missing():
    """Why the pen has no pressure, or None if it has it: "set up" (no
    helper: from source without enable-pen.sh, or a Flatpak on a PC without
    the .deb)."""
    return None if uhid_available() else "set up"


class HelperPen:
    """The pen tablet the helper made for us: its reports go to it through
    the socket (it checks each is one), and it goes when the socket closes."""

    def __init__(self, sock):
        self.sock = sock
        self.sock.setblocking(False)
        log("pen: virtual HID tablet created by the helper (pressure and tilt)")

    def report(self, data):
        if is_report(data):
            try:
                self.sock.send(data)
            except BlockingIOError:
                pass  # the helper behind: this one report goes, not the session
            except OSError:
                pass

    def close(self):
        self.sock.close()


class UhidPen:
    @staticmethod
    def create_event(descriptor):
        """struct uhid_event { u32 type; struct uhid_create2_req } (packed)."""
        return struct.pack("<I128s64s64sHHIIII", UHID_CREATE2, DEVICE_NAME.encode(),
                           b"plus-one", b"", len(descriptor), BUS_VIRTUAL,
                           VENDOR, PRODUCT, 1, 0) + descriptor

    @staticmethod
    def input_event(data):
        """struct uhid_event { u32 type; struct uhid_input2_req { u16 size; data } }."""
        return struct.pack("<IH", UHID_INPUT2, len(data)) + data

    def __init__(self):
        fd = os.open(UHID_PATH, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            os.write(fd, self.create_event(S_PEN))
        except OSError:
            os.close(fd)
            raise
        self.fd = fd
        self.closing = False
        # report() (the UIBC thread) against the drain thread's close: a
        # write must never reach a number another file has by then.
        self._lock = threading.Lock()
        # The kernel queues start/open notifications; drain them so the queue
        # never fills up. The drain thread closes the fd, once told to: closed
        # here, its number could be another file's by the time it reads.
        threading.Thread(target=self._drain, daemon=True).start()
        log("pen: virtual HID tablet created (pressure and tilt)")

    def _drain(self):
        try:
            while not self.closing:
                if select.select([self.fd], [], [], 0.5)[0]:
                    os.read(self.fd, 4380)
        except OSError:
            pass
        finally:
            with self._lock:
                self.closing = True
                os.close(self.fd)

    def report(self, data):
        if is_report(data):
            with self._lock:
                if not self.closing:
                    os.write(self.fd, self.input_event(data))

    def close(self):
        with self._lock:
            if self.closing:
                return
            try:
                os.write(self.fd, struct.pack("<I", UHID_DESTROY))
            except OSError:
                pass
            self.closing = True


class PointerPen:
    """Fallback: hover moves the pointer, tip = left button, barrel = right."""

    def __init__(self, injector, size):
        self.injector = injector
        self.size = size
        self.buttons = 0
        log("pen: no access to /dev/uhid, the pen drives the pointer (no pressure)")

    def report(self, data):
        if not is_report(data):
            return
        bits = data[1]
        x, y = struct.unpack("<HH", data[2:6])
        if bits & IN_RANGE:
            self.injector.pointer_move(x * self.size[0] / AXIS_MAX, y * self.size[1] / AXIS_MAX)
        for mask, button in ((TIP, BTN_LEFT), (BARREL, BTN_RIGHT)):
            pressed = bool(bits & mask) and bool(bits & IN_RANGE)
            if pressed != bool(self.buttons & mask):
                self.injector.pointer_button(button, pressed)
                self.buttons ^= mask

    def close(self):
        for mask, button in ((TIP, BTN_LEFT), (BARREL, BTN_RIGHT)):
            if self.buttons & mask:
                self.injector.pointer_button(button, False)
        self.buttons = 0


def for_monitor(injector, size):
    """The S Pen for the tablet's virtual monitor: a pen tablet (pressure,
    tilt) mapped onto it, else the pointer through injector (the monitor's
    remote desktop session, size its pixels). A pen tablet is the whole
    desktop's until mapped: not mapped, the tablet's pen would point and
    click on the PC's own screens, so then the pointer, kept in the stream."""
    if uhid_available():
        mapped = False
        try:
            edid = virtual_monitor_edid()
            mapped = bool(edid) and map_to_monitor(edid)
        except Exception as e:  # noqa: BLE001 - the pointer pen then
            log("pen: not mapped to the monitor:", repr(e))
        if mapped:
            device = tablet_pen()
            if device is not None:
                return device
        else:
            log("pen: the tablet's monitor not known to map it onto: the pointer instead")
    return PointerPen(injector, size)


def map_to_monitor(edid):
    """Tell Mutter to map our tablet onto the monitor with this (vendor,
    product, serial): whether it could."""
    if not Gio.SettingsSchemaSource.get_default().lookup(TABLET_SCHEMA, True):
        log("pen: no tablet settings schema, cannot map the pen to the monitor")
        return False
    path = f"/org/gnome/desktop/peripherals/tablets/{VENDOR:04x}:{PRODUCT:04x}/"
    settings = Gio.Settings.new_with_path(TABLET_SCHEMA, path)
    settings.set_strv("output", list(edid))
    settings.set_boolean("keep-aspect", False)
    Gio.Settings.sync()
    log("pen: mapped to monitor", edid)
    return True
