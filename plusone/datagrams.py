"""The camera's and the microphone's pieces over UDP (companion.capture): no
waiting for a lost packet to come again, as TCP does, and with it everything
after it. A lost Opus packet is covered by the decoder; a frame that does not
come whole is left out, and the app asked for a key frame.

Each datagram: a header (as the AEAD's associated data) and the piece, or a
part of a frame, sealed with AES-256-GCM under the session's key, which the
app sent over the TLS link; the nonce is the app's salt and the datagram's
sequence number, never the same twice for a key. The app sends to the
address a "punch" came from (the token, also from the TLS link, from the
TLS peer's own address): that is the PC's way through its firewall too.
"""
import socket
import struct
import time

from .aead import Key

MAGIC = b"P1"
HEADER = struct.Struct(">2sBBIIHHq")  # magic, kind, flags, sequence, frame, part, parts, pts (µs)
KINDS = {ord("v"): "video", ord("a"): "audio"}
KEY_FRAME, CONFIG = 1, 2
PUNCH_EVERY = 2.0    # seconds: the firewall's and the app's idea of where to send
FIRST_WITHIN = 3.0   # seconds for the first datagram, else TCP after all
SILENT_FOR = 5.0     # seconds without one: the link is gone
KEY_AGAIN = 0.5      # seconds between two key frame requests
WINDOW = 1024        # sequence numbers remembered against replays
STATS_EVERY = 1.0    # seconds between reports of what came and what was lost (the app's bitrate follows)


def nonce(salt, sequence):
    return salt + struct.pack(">Q", sequence)


class Pieces:
    """Datagrams in, pieces out: decrypted, frames put together, in order."""

    def __init__(self, key, salt):
        self.key = Key(key)
        self.salt = salt
        self.highest = -1
        self.seen = set()
        self.frames = {}     # frame -> [parts], pts, flags, kind
        self.delivered = -1  # the last frame given out
        self.lost = 0        # frames that never came whole
        self.received = 0    # datagrams that came (each once)
        self.first = None    # the first sequence number seen

    def add(self, datagram):
        """The pieces this datagram completes: [(header, payload)]."""
        if len(datagram) < HEADER.size:
            return []
        magic, kind, flags, sequence, frame, part, parts, pts = HEADER.unpack_from(datagram)
        if magic != MAGIC or kind not in KINDS or not 0 <= part < parts:
            return []
        if sequence in self.seen or sequence <= self.highest - WINDOW:
            return []  # again, or too old: a replay
        payload = self.key.open(nonce(self.salt, sequence), datagram[HEADER.size:], datagram[:HEADER.size])
        if payload is None:
            return []
        self.seen.add(sequence)
        self.received += 1
        if self.first is None:
            self.first = sequence
        if sequence > self.highest:
            self.highest = sequence
            self.seen = {s for s in self.seen if s > sequence - WINDOW}
        if frame <= self.delivered:
            return []  # late: its moment has passed
        entry = self.frames.setdefault(frame, [[None] * parts, pts, flags])
        if len(entry[0]) != parts:
            return []
        entry[0][part] = payload
        if any(p is None for p in entry[0]):
            return []
        # Whole: out it goes, and those before it that never came whole are
        # given up. Video counts them (they are missed by the next frames,
        # until a key frame); a lost Opus packet the decoder covers.
        if KINDS[kind] == "video" and self.delivered >= 0:
            self.lost += frame - self.delivered - 1
        for f in [f for f in self.frames if f < frame]:
            del self.frames[f]
        del self.frames[frame]
        self.delivered = frame
        header = {"type": KINDS[kind], "pts": pts, "key": bool(flags & KEY_FRAME), "config": bool(flags & CONFIG),
                  "frame": frame}
        return [(header, b"".join(entry[0]))]

    def missing(self):
        """Datagrams sent so far that never came (by the sequence numbers)."""
        return 0 if self.first is None else self.highest - self.first + 1 - self.received


class Link:
    """A capture's pieces over UDP; its TLS link stays for control (key
    frames, and hanging up)."""

    def __init__(self, tls, wire, info, udp):
        self.tls, self.wire, self.udp = tls, wire, udp
        self.app = (tls.getpeername()[0], int(info["udp"]))
        self.token = bytes.fromhex(info["token"])
        self.pieces = Pieces(bytes.fromhex(info["key"]), bytes.fromhex(info["salt"]))
        self.ready = []
        self.heard = None
        self.punched = 0.0
        self.asked = 0.0
        self.started = time.monotonic()
        self.closed = False
        self.reported = (time.monotonic(), 0, 0)  # when, received, missing
        udp.settimeout(0.25)

    def receive_piece(self):
        """The next whole piece; raises TimeoutError if no datagram comes."""
        while not self.ready:
            if self.closed:
                raise EOFError("hung up")
            now = time.monotonic()
            if now - self.punched > PUNCH_EVERY:
                self.udp.sendto(self.token, self.app)
                self.punched = now
            if self.heard is None and now - self.started > FIRST_WITHIN:
                raise TimeoutError("no datagram from the app")
            if self.heard is not None and now - self.heard > SILENT_FOR:
                raise TimeoutError("the app's datagrams stopped")
            try:
                datagram, sender = self.udp.recvfrom(65536)
            except socket.timeout:
                continue
            if sender[0] != self.app[0]:
                continue
            self.heard = time.monotonic()
            lost = self.pieces.lost
            self.ready += self.pieces.add(datagram)
            if self.pieces.lost > lost:
                self.key_frame()
            self._report()
        return self.ready.pop(0)

    def _report(self):
        """What came and what was lost since the last report: the app's
        encoder goes down when the network loses, up when it does not."""
        when, received, missing = self.reported
        now = time.monotonic()
        if now - when >= STATS_EVERY:
            got, lost = self.pieces.received - received, max(0, self.pieces.missing() - missing)
            self.reported = (now, self.pieces.received, max(missing, self.pieces.missing()))
            self.wire.send({"type": "stats", "received": got, "lost": lost})

    def key_frame(self):
        """Asks the app for a key frame (a frame was lost), not too often."""
        now = time.monotonic()
        if now - self.asked > KEY_AGAIN:
            self.asked = now
            self.wire.send({"type": "key"})

    def close(self):
        self.closed = True
        for sock in (self.udp, self.tls):
            try:
                sock.close()
            except OSError:
                pass
