"""Framed messages over a stream: a 4-byte big-endian length, a JSON
header, and as many payload bytes as the header's "size" says
(android/.../Wire.kt)."""
import json
import select
import ssl
import struct

MAX_HEADER = 16 * 1024 * 1024  # the marks of a long document (the app takes as much)
UNPAIRED_HEADER = 4096         # hello and pairing: small, from an app not yet known


class Wire:
    def __init__(self, sock):
        self.sock = sock

    def send(self, header, payload=None):
        if payload is not None:
            header = dict(header, size=len(payload))
        data = json.dumps(header).encode()
        self.sock.sendall(struct.pack(">I", len(data)) + data + (payload or b""))

    def readable(self, timeout):
        """Whether a message starts arriving within timeout seconds."""
        if isinstance(self.sock, ssl.SSLSocket) and self.sock.pending():
            return True
        return bool(select.select([self.sock], [], [], timeout)[0])

    def _exactly(self, n):
        chunks, left = [], n
        while left:
            chunk = self.sock.recv(min(left, 1 << 20))
            if not chunk:
                raise EOFError("the tablet closed the connection")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def receive(self, limit=MAX_HEADER):
        """The next message (the app sends headers only), at most limit bytes."""
        (length,) = struct.unpack(">I", self._exactly(4))
        if not 0 < length <= limit:
            raise ValueError(f"bad header length {length}")
        header = json.loads(self._exactly(length))
        if not isinstance(header, dict):
            raise ValueError("a message that is not an object")
        if header.get("size"):
            raise ValueError("the app sends no payloads")
        return header
