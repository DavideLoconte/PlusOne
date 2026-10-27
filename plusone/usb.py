"""Plus One over the USB cable: Android Open Accessory (AOA). The PC is the
USB host, the tablet its accessory, and one bulk stream between them carries
what the network would (UsbTunnel.kt): the companion's TLS connections and
the display's sealed datagrams. Locally the link is a TCP listener (the
targets companion connects to) and a UDP socket (the display's relay), so
pairing, TLS and AES-GCM stay end to end and the code above is unchanged.

Needs libusb (brew install libusb) and pyusb; no developer options on the
tablet, which asks once whether Plus One may use the accessory.
"""
import select
import socket
import struct
import threading
import time

from . import log

FRAME = struct.Struct("!BBI")  # kind, connection, length
HELLO, OPEN, DATA, CLOSE, DATAGRAM = 0, 1, 2, 3, 4
GOOGLE, ACCESSORY_PRODUCTS = 0x18D1, (0x2D00, 0x2D01)  # an Android device in accessory mode
# ponytail: Samsung's id only; other makers' tablets when one is used.
ANDROID_VENDORS = (0x04E8,)
# As res/xml/accessory_filter.xml matches: manufacturer, model, description,
# version, URI, serial.
IDENTITY = ("Plus One", "Plus One", "Plus One on the PC", "1", "https://github.com/GabrieleSantangelo/PlusOne", "")
GET_PROTOCOL, SEND_STRING, START = 51, 52, 53
CHUNK = 16384          # bytes of a read, either way
BATCH = 48 * 1024      # bytes of datagrams in one USB write, at most
SWITCH_WAIT = 10       # seconds for the tablet to come back as an accessory
ANSWER_WAIT = 300      # seconds for the user to let Plus One have it, the first time


def frame(kind, connection, data=b""):
    return FRAME.pack(kind, connection, len(data)) + data


class Frames:
    """The stream cut back into frames, however the reads fell."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        """[(kind, connection, data)] completed by these bytes."""
        self.buffer += data
        found = []
        while len(self.buffer) >= FRAME.size:
            kind, connection, length = FRAME.unpack_from(self.buffer)
            if len(self.buffer) < FRAME.size + length:
                break
            found.append((kind, connection, bytes(self.buffer[FRAME.size:FRAME.size + length])))
            del self.buffer[:FRAME.size + length]
        return found


def _find(**match):
    import usb.core
    return usb.core.find(custom_match=lambda d: all(f(d) for f in match.values()))


def accessory():
    return _find(vendor=lambda d: d.idVendor == GOOGLE, product=lambda d: d.idProduct in ACCESSORY_PRODUCTS)


def tablet():
    """An Android device on the cable (as an accessory already, or not), or None.
    None too without libusb or pyusb."""
    try:
        return accessory() or _find(vendor=lambda d: d.idVendor in ANDROID_VENDORS)
    except Exception:  # noqa: BLE001 - no pyusb, no libusb: no cable
        return None


class Link:
    """The bulk stream to Plus One's app, once the tablet is an accessory
    and the app has answered HELLO."""

    def __init__(self):
        import usb.util
        device = accessory()
        if device is not None:
            # Left as an accessory by an earlier session, maybe mid-frame: reset,
            # so both ends start afresh. Android then either leaves accessory
            # mode (asked again below) or keeps it, and the app opens it again.
            log("usb: the tablet was an accessory already: reset")
            try:
                device.reset()
            except Exception as e:  # noqa: BLE001 - it went, which is the point
                log("usb: reset:", repr(e))
            usb.util.dispose_resources(device)
            time.sleep(1)
            device = accessory()
        if device is None:
            device = self._switch()
        device.set_configuration()
        interface = device.get_active_configuration()[(0, 0)]
        usb.util.claim_interface(device, interface)
        direction = usb.util.endpoint_direction
        self.ep_in = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        self.ep_out = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.device, self.interface = device, interface
        self.packet = self.ep_out.wMaxPacketSize
        self.lock = threading.Lock()
        self.frames = Frames()
        self._hello()

    def _switch(self):
        """The tablet asked to be Plus One's accessory: it comes back as one."""
        import usb.util
        device = tablet()
        if device is None:
            raise RuntimeError("no tablet on the cable")
        version = device.ctrl_transfer(0xC0, GET_PROTOCOL, 0, 0, 2)
        if not (version[0] | version[1] << 8):
            raise RuntimeError("the tablet cannot be an accessory (Android Open Accessory)")
        for index, text in enumerate(IDENTITY):
            device.ctrl_transfer(0x40, SEND_STRING, 0, index, text.encode() + b"\0")
        device.ctrl_transfer(0x40, START, 0, 0, None)
        usb.util.dispose_resources(device)
        log("usb: asked the tablet to be Plus One's accessory")
        deadline = time.monotonic() + SWITCH_WAIT
        while time.monotonic() < deadline:
            time.sleep(0.3)
            device = accessory()
            if device is not None:
                return device
        raise RuntimeError("the tablet did not come back as an accessory")

    def _hello(self):
        """HELLO until the app reads it (Android asks the user first, once),
        then its answer: whatever was open before is closed on both sides."""
        deadline = time.monotonic() + ANSWER_WAIT
        while True:
            try:
                self.write(frame(HELLO, 0), timeout=1000)
                break
            except Exception as e:  # noqa: BLE001 - not read yet: the app is not open
                if time.monotonic() > deadline:
                    raise TimeoutError("Plus One did not open on the tablet (allow it there)") from e
        while time.monotonic() < deadline:
            for kind, _connection, _data in self.read(1000):
                if kind == HELLO:
                    log("usb: Plus One's app answers on the cable")
                    return
        raise TimeoutError("Plus One's app did not answer on the cable")

    def read(self, timeout):
        """The frames that came within timeout (ms): [] if none."""
        import usb.core
        try:
            data = self.ep_in.read(CHUNK, timeout=timeout)
        except usb.core.USBTimeoutError:
            return []
        return self.frames.feed(bytes(data))

    def write(self, data, timeout=5000):
        with self.lock:
            self.ep_out.write(data, timeout=timeout)
            if len(data) % self.packet == 0:
                self.ep_out.write(b"", timeout=timeout)  # a transfer ends on a short packet

    def close(self):
        import usb.util
        try:
            usb.util.release_interface(self.device, self.interface)
        except Exception:  # noqa: BLE001 - unplugged
            pass
        usb.util.dispose_resources(self.device)


class Bridge:
    """The link's local ends: target, a TCP address whose connections reach
    the app; relay, the UDP address the display's datagrams go through.
    on_lost(error) from the link's thread when the cable goes."""

    def __init__(self, link, on_lost=None):
        self.link, self.on_lost = link, on_lost or (lambda error: None)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)  # a keyframe at once
        self.udp.bind(("127.0.0.1", 0))
        self.target, self.relay = self.listener.getsockname(), self.udp.getsockname()
        self.connections = {}  # number -> socket
        self.display = None    # where the display's datagrams come from here
        self.running = True
        self._next = 0
        for run in (self._accept, self._from_tablet, self._datagrams):
            threading.Thread(target=run, daemon=True).start()

    def _send(self, kind, number, data=b""):
        for at in range(0, max(len(data), 1), CHUNK):
            self.link.write(frame(kind, number, data[at:at + CHUNK]))

    def _accept(self):
        while self.running:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._next = self._next % 255 + 1
            number = self._next
            self.connections[number] = conn
            try:
                self._send(OPEN, number)
            except Exception as e:  # noqa: BLE001 - the cable went
                self._lost(e)
                return
            threading.Thread(target=self._to_tablet, args=(number, conn), daemon=True).start()

    def _to_tablet(self, number, conn):
        try:
            while True:
                data = conn.recv(CHUNK)
                if not data:
                    break
                self._send(DATA, number, data)
        except OSError:
            pass
        except Exception as e:  # noqa: BLE001 - the cable went
            self._lost(e)
        if self.connections.pop(number, None) is conn:
            try:
                self._send(CLOSE, number)
            except Exception:  # noqa: BLE001 - the cable went: said by the reader
                pass
        conn.close()

    def _from_tablet(self):
        while self.running:
            try:
                frames = self.link.read(500)
            except Exception as e:  # noqa: BLE001 - unplugged
                self._lost(e)
                return
            for kind, number, data in frames:
                conn = self.connections.get(number)
                if kind == DATA and conn is not None:
                    try:
                        conn.sendall(data)
                    except OSError:
                        self.connections.pop(number, None)
                        conn.close()
                elif kind == CLOSE and conn is not None:
                    self.connections.pop(number, None)
                    conn.shutdown(socket.SHUT_WR)  # what it sent still reaches companion
                elif kind == DATAGRAM and self.display is not None:
                    self.udp.sendto(data, self.display)

    def _datagrams(self):
        """The display's datagrams to the tablet: those waiting already go
        in one USB write (one each took ~0.14 ms: a keyframe's hundred
        packets overflowed the socket's buffer)."""
        while self.running:
            try:
                data, self.display = self.udp.recvfrom(2048)
                batch = [frame(DATAGRAM, 0, data)]
                size = len(batch[0])
                while size < BATCH and select.select([self.udp], [], [], 0)[0]:
                    data, self.display = self.udp.recvfrom(2048)
                    batch.append(frame(DATAGRAM, 0, data))
                    size += len(batch[-1])
                self.link.write(b"".join(batch))
            except OSError:
                return
            except Exception as e:  # noqa: BLE001 - the cable went
                self._lost(e)
                return

    def _lost(self, error):
        if self.running:
            log("usb: the cable's link is lost:", repr(error))
            self.close()
            self.on_lost(error)

    def close(self):
        self.running = False
        for conn in list(self.connections.values()):
            conn.close()
        self.connections.clear()
        self.listener.close()
        self.udp.close()
        self.link.close()
