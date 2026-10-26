"""Datagrams: what must not wait (frames, input, sound) as UDP packets,
sealed with AES-GCM under a key fresh for each session, which goes to the
app over the link (link.py) with the session's request.

A packet: nonce (12 bytes: 4 for the direction, FROM_PC or FROM_APP, 8
counting, never the same twice) | sealed body (android/.../Sealed.kt). Only
the app's address is listened to, and only its packets newer than the
newest heard (an older one is a replay).
"""
import fcntl
import os
import select
import socket
import struct
import termios
import threading

from .. import log

FROM_PC, FROM_APP = 1, 2
NONCE = struct.Struct("!IQ")  # direction, count
TAG = 16                      # AES-GCM's, after the body
KEY_BYTES = 32


class Datagrams:
    """A session's UDP socket, sealed both ways. key: 32 bytes, made here
    when not given; port: this end's, for the app; app: (address, port),
    the app's, once it answered."""

    def __init__(self, key=None):
        # Here, not at the top: the window uses the display's sizes where
        # python3-cryptography may be missing (from the sources).
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.key = key or os.urandom(KEY_BYTES)
        self.aead = AESGCM(self.key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Room for a keyframe's packets at once (as much of it as the kernel
        # allows: net.core.wmem_max; past it, sending waits for the Wi-Fi).
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        self.sock.bind(("0.0.0.0", 0))
        self.port = self.sock.getsockname()[1]
        self.app = None
        self._sent = 0
        self._lock = threading.Lock()
        self._heard = -1  # the app's highest count: older ones are replays
        self.errors = set()

    def send(self, body):
        with self._lock:
            nonce = NONCE.pack(FROM_PC, self._sent)
            self._sent += 1
        try:
            self.sock.sendto(nonce + self.aead.encrypt(nonce, body, None), self.app)
        except OSError as e:
            if e.errno not in self.errors:
                self.errors.add(e.errno)
                log("datagrams: cannot send:", e.strerror)

    def queued(self):
        """Bytes sent and not yet on the air: in the socket, the queue, the
        Wi-Fi driver (each packet counts until it is transmitted)."""
        try:
            return struct.unpack("i", fcntl.ioctl(self.sock.fileno(), termios.TIOCOUTQ, b"\0" * 4))[0]
        except (OSError, ValueError):
            return 0

    def receive(self, timeout):
        """A body from the app, or None (nothing in time, or not the app's).
        The socket stays blocking: a timeout on it would be the sending
        thread's too, and a frame's tail dropped when the buffer is full."""
        try:
            if not select.select([self.sock], [], [], timeout)[0]:
                return None
            packet, address = self.sock.recvfrom(2048)
        except (OSError, ValueError):  # closed
            return None
        if self.app is None or address[0] != self.app[0] or len(packet) < NONCE.size + TAG + 1:
            return None
        direction, count = NONCE.unpack_from(packet)
        if direction != FROM_APP or count <= self._heard:
            return None
        try:
            body = self.aead.decrypt(packet[:NONCE.size], packet[NONCE.size:], None)
        except Exception:  # noqa: BLE001 - not sealed with this session's key
            return None
        self._heard = count
        return body

    def close(self):
        self.sock.close()
