"""The companion app on the tablet (android/): it signs PDFs with ink drawn
on the tablet itself, under the nib, instead of through Second Screen.

The app listens on the home network and says so with mDNS (_plusone._tcp,
found by nearby.py). Plus One connects over TLS, each side presenting a
self-signed certificate. The first time, both screens show a six-digit code
made from the two certificates and the user confirms it on both (like
Bluetooth's numeric comparison); then each side remembers the other's
certificate. The code also mixes a random number from each side, the app's
committed to before it sees the PC's: someone in the middle, showing each
side a certificate of their own, cannot make both screens show the same
code but by a one-in-a-million chance.

Messages: a 4-byte big-endian length, a JSON header, and as many payload
bytes as the header's "size" says (android/.../Wire.kt).
"""
import hashlib
import json
import math
import os
import secrets
import select
import socket
import ssl
import struct
import subprocess
import threading
import time
import uuid

from gi.repository import GLib

from . import log, write_private

PROTOCOL = 2
CONNECT_TIMEOUT = 5          # seconds
PAIRING_TIMEOUT = 120        # seconds for the user to compare the codes
IO_TIMEOUT = 30              # seconds for a message to go through
PING_EVERY = 15              # seconds of silence while the user signs before asking "still there?"
SILENT_FOR = 45              # seconds of silence after which the link is taken for dead
MAX_HEADER = 16 * 1024 * 1024  # the marks of a long document (the app takes as much)
UNPAIRED_HEADER = 4096         # hello and pairing: small, from an app not yet known
HANDSHAKE_FOR = 20             # seconds for TLS, hello and pairing messages (not the user's answer)
MAX_NAME = 64
CONFIG = os.path.join(GLib.get_user_config_dir(), "plus-one", "companion")
_making_identity = threading.Lock()


class Incompatible(Exception):
    """The app speaks another version of the protocol: one of the two needs updating."""


def pairing_code(a, b, ours, theirs):
    """Six digits from two certificates (DER, in either order) and the PC's
    and the app's random numbers; android/.../Identity.kt pairingCode
    computes the same."""
    first, second = sorted([a, b])
    digest = hashlib.sha256(first + second + ours + theirs).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def commitment(nonce, app_cert, pc_cert):
    """What the app sends before it learns the PC's number: binds its own."""
    return hashlib.sha256(nonce + app_cert + pc_cert).hexdigest()


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def identity(directory=CONFIG):
    """(certificate path, key path, certificate DER) of this PC, made on
    first use with openssl (on every Ubuntu, and in the GNOME runtime): an
    EC P-256 key and a self-signed certificate."""
    cert, key = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    with _making_identity:  # two PDFs at once: one key, one certificate
        if not (os.path.exists(cert) and os.path.exists(key)):
            os.makedirs(directory, mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)  # the key in it is ours only
            subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                            "-nodes", "-keyout", key + ".part", "-out", cert + ".part", "-days", "10950",
                            "-subj", f"/CN=Plus One on {socket.gethostname()}"],
                           check=True, capture_output=True, timeout=30)
            os.chmod(key + ".part", 0o600)
            os.replace(key + ".part", key)
            os.replace(cert + ".part", cert)
    with open(cert) as f:
        der = ssl.PEM_cert_to_DER_cert(f.read())
    return cert, key, der


class Paired:
    """Tablets the user paired with: certificate fingerprint -> name."""

    def __init__(self, directory=CONFIG):
        self.path = os.path.join(directory, "paired.json")

    def all(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def add(self, fp, name):
        data = self.all()
        data[fp] = name
        write_private(self.path, data)

    def forget(self, fp):
        data = self.all()
        if data.pop(fp, None) is not None:
            write_private(self.path, data)


class Wire:
    """Framed messages over a socket (see the module docstring)."""

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


class PairingRefused(Exception):
    pass


class Impostor(PairingRefused):
    """At an address announced for a paired tablet, an app with another
    certificate: someone answering mDNS for it. Never paired with."""


class Lost(Exception):
    """The link went while the user was signing on the tablet, and it did
    not come back in time: what was signed there did not reach the PC."""


RESUME_FOR = 600     # seconds to look for the tablet again after the link drops mid-job
RESUME_EVERY = 5     # seconds between attempts
MAX_JOBS = 24        # PDFs waiting on the tablet at once (the app takes 32 connections)


class _Turns:
    """PDFs sent together (five selected in Files) go to the tablet one at a
    time, in the order they were asked for: the tablet's queue keeps that
    order, and a first pairing asks once, the others finding the tablet
    paired."""

    def __init__(self):
        self._changed = threading.Condition()
        self._issued = 0
        self._now = 0

    def ticket(self):
        with self._changed:
            self._issued += 1
            return self._issued - 1

    def wait(self, ticket):
        with self._changed:
            self._changed.wait_for(lambda: self._now == ticket)

    def done(self, ticket):
        with self._changed:
            self._now = max(self._now, ticket + 1)
            self._changed.notify_all()


_turns = _Turns()
_places = threading.BoundedSemaphore(MAX_JOBS)


def next_turn():
    """A place in line for sign(), taken when the PDF is asked for (its order)."""
    return _turns.ticket()


def _connect(target, paired, ask_pairing, expect=None):
    """(socket, wire, tablet name) of a TLS link to the app at target, the
    tablet paired (asking the user through ask_pairing if needed, or raising
    PairingRefused if ask_pairing is None). expect: the id the app was
    announced with; if it is a paired tablet's, that tablet's certificate is
    required (Impostor otherwise)."""
    cert, key, ours = identity()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    # No CA vouches for either side: the app's certificate is checked
    # against the paired list (or the user compares the code).
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(cert, key)
    raw = socket.create_connection(target, timeout=CONNECT_TIMEOUT)
    try:
        sock = context.wrap_socket(raw)
    except Exception:
        raw.close()
        raise
    deadline = time.monotonic() + HANDSHAKE_FOR

    def small():
        """A message of the handshake: small, and all of them in time."""
        sock.settimeout(max(0.1, deadline - time.monotonic()))
        return wire.receive(UNPAIRED_HEADER)

    try:
        theirs = sock.getpeercert(binary_form=True)
        fp = fingerprint(theirs)
        wire = Wire(sock)
        known = paired.all()
        pinned = [p for p in known if expect and p.startswith(expect)]
        if pinned and fp not in pinned:
            raise Impostor(f"an app at {target[0]} announced as a paired tablet, with another certificate")
        known = fp in known
        sock.settimeout(IO_TIMEOUT)
        wire.send({"type": "hello", "name": socket.gethostname(), "version": PROTOCOL, "paired": known})
        hello = small()
        name = str(hello.get("name") or "tablet")[:MAX_NAME]
        if hello.get("version") != PROTOCOL:
            raise Incompatible(f"{name} speaks version {hello.get('version')}, this PC {PROTOCOL}")
        if not known or not hello.get("paired"):
            if ask_pairing is None:
                raise PairingRefused(name)
            # The app commits to its number, learns ours, then shows its own.
            commit = small()
            nonce = secrets.token_bytes(16)
            wire.send({"type": "pair-nonce", "nonce": nonce.hex()})
            reveal = small()
            their_nonce = bytes.fromhex(str(reveal.get("nonce", "")))
            if (commit.get("type") != "pair-commit" or len(their_nonce) != 16
                    or commit.get("commit") != commitment(their_nonce, theirs, ours)):
                raise PairingRefused(name)
            code = pairing_code(ours, theirs, nonce, their_nonce)
            log(f"companion: pairing with {name}, code {code}")
            sock.settimeout(PAIRING_TIMEOUT + 10)
            accepted = ask_pairing(name, code)
            wire.send({"type": "pair-ok" if accepted else "pair-no"})
            answer = wire.receive(UNPAIRED_HEADER)
            if not accepted or answer.get("type") != "pair-ok":
                raise PairingRefused(name)
            paired.add(fp, name)
            sock.settimeout(IO_TIMEOUT)
        return sock, wire, name
    except Exception:
        sock.close()
        raise


def _answer(wire, job, on_opened, name, on_signatures):
    """Waits for the job's answer: the result, or None if cancelled. Asks
    whether the app is still there when it is quiet for a while: a link
    that died without a word (Wi-Fi gone) raises TimeoutError."""
    heard = time.monotonic()
    while True:
        if not wire.readable(PING_EVERY):
            if time.monotonic() - heard > SILENT_FOR:
                raise TimeoutError("the tablet stopped answering")
            wire.send({"type": "ping"})
            continue
        message = wire.receive()
        heard = time.monotonic()
        kind = message.get("type")
        if kind == "opened" and on_opened:
            on_opened(name)
        elif kind == "resumed":
            log("companion: the tablet still has the job")
        elif kind == "unknown-job":
            raise Lost("the tablet no longer has the job")
        elif kind in ("signed", "cancelled") and message.get("job") == job:
            # The app keeps the answer until it knows it arrived.
            wire.send({"type": "got", "job": job})
            wire.send({"type": "bye"})
            if on_signatures and isinstance(message.get("signatures"), dict):
                on_signatures(message["signatures"])
            marks = message.get("marks") if kind == "signed" else None
            return (marks if isinstance(marks, dict) else {"pages": []}) if kind == "signed" else None


def sign(target, path, ask_pairing, paired=None, on_opened=None, pen=None, signatures=None,
         on_signatures=None, locate=None, resume_for=RESUME_FOR, turn=None, expect=None):
    """Blocking (run it in a thread): send the PDF at path to the app at
    target ((host, port), or a list of them: the addresses the app was
    announced at, tried in turn) and wait for the user to sign it there.
    expect: the id it was announced with (_connect). Returns the
    result ({"pages": [...], "pen": ..., maybe "signature": ...}) or None if
    the user cancelled. ask_pairing(name, code) -> bool asks this PC's user
    (from this thread) when pairing is needed. pen ({"color", "width"}) and
    the saved signatures (signatures.message()) go along, so the tablet
    signs like the PC; on_signatures(the tablet's list) runs with the answer.

    Several at once: each goes in its turn (next_turn(), or one taken now),
    and at most MAX_JOBS wait on the tablet; more wait here for a place.

    Should the link drop once the PDF is on the tablet, the app keeps the
    job: Plus One connects again (to locate()'s addresses, the app's port may
    have changed) and asks for it, for resume_for seconds.

    Raises OSError, EOFError, ValueError, PairingRefused or Incompatible
    before the PDF reached the tablet, Lost after."""
    paired = paired or Paired()
    turn = next_turn() if turn is None else turn
    try:
        _turns.wait(turn)
        _places.acquire()
        try:
            sock, wire, name, job = _send(target, path, paired, ask_pairing, pen, signatures, expect)
        except BaseException:
            _places.release()
            raise
    finally:
        _turns.done(turn)
    try:
        return _wait(sock, wire, name, job, target, paired, on_opened, on_signatures, locate, resume_for,
                     expect)
    finally:
        _places.release()


def _connect_any(targets, paired, ask_pairing, expect):
    """_connect() to the first of targets that answers as it should."""
    error = None
    for target in [targets] if isinstance(targets, tuple) else targets:
        try:
            return _connect(target, paired, ask_pairing, expect)
        except Impostor as e:
            log("companion:", e)
            error = e
        except (OSError, EOFError, ValueError) as e:
            error = e
    raise error or OSError("no address for the tablet")


def _send(target, path, paired, ask_pairing, pen, signatures, expect):
    """(socket, wire, tablet name, job id) once the PDF is on the tablet."""
    sock, wire, name = _connect_any(target, paired, ask_pairing, expect)
    job = uuid.uuid4().hex
    try:
        with open(path, "rb") as f:
            pdf = f.read()
        sock.settimeout(IO_TIMEOUT * 4)  # a big PDF over slow Wi-Fi
        header = {"type": "sign", "job": job, "title": os.path.basename(path)}
        if pen:
            header["pen"] = pen
        if signatures:
            header["signatures"] = signatures
        wire.send(header, pdf)
        sock.settimeout(IO_TIMEOUT)
    except BaseException:
        sock.close()
        raise
    return sock, wire, name, job


def _wait(sock, wire, name, job, target, paired, on_opened, on_signatures, locate, resume_for, expect):
    """The job's answer, connecting again if the link drops (sign())."""
    with sock:
        try:
            return _answer(wire, job, on_opened, name, on_signatures)
        except Exception as e:  # noqa: BLE001 - the PDF is on the tablet: whatever broke, ask again
            log("companion: lost the tablet while signing:", repr(e))
    deadline = time.monotonic() + resume_for
    while time.monotonic() < deadline:
        time.sleep(RESUME_EVERY)
        where = locate() if locate else target
        if not where:
            continue
        try:
            sock, wire, name = _connect_any(where, paired, None, expect)
        except (OSError, EOFError, ValueError, PairingRefused, Incompatible):
            continue
        with sock:
            try:
                wire.send({"type": "resume", "job": job})
                log("companion: back in touch with the tablet")
                return _answer(wire, job, None, name, on_signatures)
            except Exception as e:  # noqa: BLE001 - as above
                log("companion: lost the tablet again:", repr(e))
    raise Lost(name)


def _finite(value):
    number = float(value)  # TypeError, ValueError: not a number
    if not math.isfinite(number):
        raise ValueError("not a finite number")
    return number


def to_marks(result):
    """The app's marks as plusone/pdf.py takes them: {page index: [Stroke |
    Text]}. What does not make sense (not a page index, not finite, a colour
    not of three parts, control characters) is left out, mark by mark."""
    from .pdf import INK, PEN_WIDTH, Stroke, Text

    marks = {}
    pages = result.get("pages") if isinstance(result, dict) else None
    for page in pages if isinstance(pages, list) else []:
        index = page.get("page") if isinstance(page, dict) else None
        if type(index) is not int or index < 0:
            continue
        found = []
        for mark in page.get("marks") if isinstance(page.get("marks"), list) else []:
            try:
                color = tuple(min(1.0, max(0.0, _finite(c))) for c in mark.get("color", INK))
                if len(color) != 3:
                    raise ValueError("a colour of three parts")
                if mark.get("type") == "stroke":
                    p = [_finite(v) for v in mark.get("points", [])]
                    points = [(p[i], p[i + 1], min(1.0, max(0.0, p[i + 2]))) for i in range(0, len(p) - 2, 3)]
                    width = _finite(mark.get("width", PEN_WIDTH))
                    if points and 0 < width <= 100:
                        found.append(Stroke(points, width=width, color=color))
                elif mark.get("type") == "text" and isinstance(mark.get("text"), str):
                    text = "".join(ch for ch in mark["text"] if ch.isprintable())[:500]
                    size = _finite(mark.get("size", 11.0))
                    if text and 0 < size <= 500:
                        found.append(Text(_finite(mark["x"]), _finite(mark["y"]), text, size=size, color=color,
                                          angle=_finite(mark.get("angle", 0.0))))
            except (AttributeError, KeyError, TypeError, ValueError):
                log("companion: a mark from the tablet skipped")
        if found:
            marks[index] = found
    return marks
