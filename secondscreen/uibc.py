"""WFD UIBC (User Input Back Channel) server: tablet touches -> desktop input.

The sink connects to the TCP port we announce in M4 and sends generic input
events (WFD spec 4.11): a 4-byte header (version/timestamp flag, input
category, total length), an optional 2-byte timestamp, then one or more
generic input elements: ID (1 byte), length (2 bytes), body.
"""
import socket
import struct
import threading

from . import log

TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE, KEY_DOWN, KEY_UP = 0, 1, 2, 3, 4


class UibcServer:
    def __init__(self, injector, port=50000):
        """injector: object with touch_down/touch_motion/touch_up(slot, x, y)."""
        self.injector = injector
        self.port = port
        self.scale = (1.0, 1.0)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.listen(1)
        self.down = set()
        threading.Thread(target=self._serve, daemon=True).start()

    def set_scale(self, negotiated, stream):
        """Coordinates arrive in the negotiated (M4) size; map them to the stream."""
        self.scale = (stream[0] / negotiated[0], stream[1] / negotiated[1])

    def _serve(self):
        while True:
            try:
                conn, addr = self.sock.accept()
            except OSError:
                return
            log("UIBC: sink connected from", addr)
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _recv(self, conn, n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _handle(self, conn):
        try:
            while True:
                head = self._recv(conn, 4)
                has_ts = head[0] & 0x10
                category = head[1] & 0x0F
                length = struct.unpack(">H", head[2:4])[0]
                body = self._recv(conn, length - 4)
                if has_ts:
                    body = body[2:]
                if category == 0:
                    self._generic(body)
                else:
                    log(f"UIBC: ignoring category {category} ({len(body)} bytes)")
        except (EOFError, OSError):
            log("UIBC: sink disconnected")
            for slot in list(self.down):
                self.injector.touch_up(slot)
            self.down.clear()

    def _generic(self, body):
        while len(body) >= 3:
            ie_id = body[0]
            ie_len = struct.unpack(">H", body[1:3])[0]
            desc, body = body[3:3 + ie_len], body[3 + ie_len:]
            if ie_id in (TOUCH_DOWN, TOUCH_UP, TOUCH_MOVE) and desc:
                count = desc[0]
                for i in range(count):
                    pid, x, y = struct.unpack(">BHH", desc[1 + 5 * i:6 + 5 * i])
                    x, y = x * self.scale[0], y * self.scale[1]
                    if ie_id == TOUCH_DOWN:
                        self.down.add(pid)
                        self.injector.touch_down(pid, x, y)
                    elif ie_id == TOUCH_MOVE and pid in self.down:
                        self.injector.touch_motion(pid, x, y)
                    elif ie_id == TOUCH_UP:
                        self.down.discard(pid)
                        self.injector.touch_up(pid)
            else:
                log(f"UIBC: generic event {ie_id} {desc.hex()}")

    def close(self):
        self.sock.close()
