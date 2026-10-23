"""S Pen from the tablet: a real Linux tablet device when possible.

Second Screen sends the pen as a UIBC HIDC "Gesture" device: first its HID
report descriptor (digitizer pen, report ID 0x14: tip/barrel/invert/eraser/
in-range bits, X/Y 0..32767, pressure 0..4096, X/Y tilt -90..90), then
10-byte input reports. If /dev/uhid is writable we replay that descriptor
and those reports into a virtual HID device, so the kernel creates a proper
tablet (pressure and tilt reach Krita, GIMP, Xournal++...). Otherwise the pen
drives the pointer through Mutter's remote desktop session.
"""
import os
import struct
import subprocess
import threading

from . import log

UHID_PATH = "/dev/uhid"
UHID_DESTROY, UHID_CREATE2, UHID_INPUT2 = 1, 11, 12
BUS_VIRTUAL = 0x06
VENDOR, PRODUCT = 0x04E8, 0x5350  # Samsung vendor id, "SP"
DEVICE_NAME = "Plus One S Pen"
TABLET_SCHEMA = "org.gnome.desktop.peripherals.tablet"
BTN_LEFT, BTN_RIGHT = 0x110, 0x111
TIP, BARREL, IN_RANGE = 0x01, 0x02, 0x10
AXIS_MAX = 32767


def uhid_available():
    return os.access(UHID_PATH, os.R_OK | os.W_OK)


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

    def __init__(self, descriptor):
        self.fd = os.open(UHID_PATH, os.O_RDWR | os.O_CLOEXEC)
        os.write(self.fd, self.create_event(descriptor))
        # The kernel queues start/open notifications; drain them so the queue
        # never fills up.
        threading.Thread(target=self._drain, daemon=True).start()
        log("pen: virtual HID tablet created (pressure and tilt)")

    def _drain(self):
        try:
            while os.read(self.fd, 4380):
                pass
        except OSError:
            pass

    def report(self, data):
        os.write(self.fd, self.input_event(data))

    def close(self):
        try:
            os.write(self.fd, struct.pack("<I", UHID_DESTROY))
        finally:
            os.close(self.fd)


class PointerPen:
    """Fallback: hover moves the pointer, tip = left button, barrel = right."""

    def __init__(self, injector, size):
        self.injector = injector
        self.size = size
        self.buttons = 0
        log("pen: no access to /dev/uhid, the pen drives the pointer (no pressure)")

    def report(self, data):
        if len(data) < 6 or data[0] != 0x14:
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


def map_to_monitor(edid):
    """Tell Mutter to map our tablet onto the monitor with this (vendor, product, serial)."""
    path = f"/org/gnome/desktop/peripherals/tablets/{VENDOR:04x}:{PRODUCT:04x}/"
    value = "[" + ", ".join(f"'{part}'" for part in edid) + "]"
    subprocess.run(["gsettings", "set", f"{TABLET_SCHEMA}:{path}", "output", value],
                   capture_output=True)
    subprocess.run(["gsettings", "set", f"{TABLET_SCHEMA}:{path}", "keep-aspect", "false"],
                   capture_output=True)
    log("pen: mapped to monitor", edid)
