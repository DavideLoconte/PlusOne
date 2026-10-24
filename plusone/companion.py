"""The companion app on the tablet (android/): it signs PDFs with ink drawn
on the tablet itself, under the nib, instead of through Second Screen.

The app listens on the home network and says so with mDNS (_plusone._tcp,
found by nearby.py). Plus One connects over TLS, each side presenting a
self-signed certificate. The first time, both screens show a six-digit code
made from the two certificates and the user confirms it on both (like
Bluetooth's numeric comparison); then each side remembers the other's
certificate. Messages: a 4-byte big-endian length, a JSON header, and as
many payload bytes as the header's "size" says (android/.../Wire.kt).
"""
import datetime
import hashlib
import json
import os
import socket
import ssl
import struct
import uuid

from gi.repository import GLib

from . import log

PROTOCOL = 1
CONNECT_TIMEOUT = 5          # seconds
PAIRING_TIMEOUT = 120        # seconds for the user to compare the codes
MAX_HEADER = 16 * 1024 * 1024
CONFIG = os.path.join(GLib.get_user_config_dir(), "plus-one", "companion")


def pairing_code(a, b):
    """Six digits from two certificates (DER), the same in either order;
    android/.../Identity.kt pairingCode computes the same."""
    first, second = sorted([a, b])
    digest = hashlib.sha256(first + second).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def fingerprint(der):
    return hashlib.sha256(der).hexdigest()


def identity(directory=CONFIG):
    """(certificate path, key path, certificate DER) of this PC, made on
    first use: an EC P-256 key and a self-signed certificate."""
    cert, key = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        os.makedirs(directory, mode=0o700, exist_ok=True)
        private = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Plus One on {socket.gethostname()}")])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(private.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - datetime.timedelta(days=1))
                       .not_valid_after(now + datetime.timedelta(days=30 * 365))
                       .sign(private, hashes.SHA256()))
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
        with open(cert, "wb") as f:
            f.write(certificate.public_bytes(serialization.Encoding.PEM))
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
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with open(self.path + ".part", "w") as f:
            json.dump(data, f, indent=2)
        os.replace(self.path + ".part", self.path)

    def forget(self, fp):
        data = self.all()
        if data.pop(fp, None) is not None:
            with open(self.path + ".part", "w") as f:
                json.dump(data, f, indent=2)
            os.replace(self.path + ".part", self.path)


class Wire:
    """Framed messages over a socket (see the module docstring)."""

    def __init__(self, sock):
        self.sock = sock

    def send(self, header, payload=None):
        if payload is not None:
            header = dict(header, size=len(payload))
        data = json.dumps(header).encode()
        self.sock.sendall(struct.pack(">I", len(data)) + data + (payload or b""))

    def _exactly(self, n):
        chunks, left = [], n
        while left:
            chunk = self.sock.recv(min(left, 1 << 20))
            if not chunk:
                raise EOFError("the tablet closed the connection")
            chunks.append(chunk)
            left -= len(chunk)
        return b"".join(chunks)

    def receive(self):
        (length,) = struct.unpack(">I", self._exactly(4))
        if not 0 < length <= MAX_HEADER:
            raise ValueError(f"bad header length {length}")
        header = json.loads(self._exactly(length))
        size = header.get("size")
        payload = self._exactly(size) if isinstance(size, int) and size >= 0 else None
        return header, payload


class PairingRefused(Exception):
    pass


def sign(target, path, ask_pairing, paired=None, on_opened=None):
    """Blocking (run it in a thread): send the PDF at path to the app at
    target (host, port) and wait for the user to sign it there. Returns the
    marks ({"pages": [...]}) or None if the user cancelled. ask_pairing(name,
    code) -> bool asks this PC's user (from this thread) when pairing is
    needed. Raises OSError, EOFError, ValueError or PairingRefused."""
    paired = paired or Paired()
    cert, key, ours = identity()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # No CA vouches for either side: the app's certificate is checked
    # against the paired list (or the user compares the code).
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.load_cert_chain(cert, key)
    raw = socket.create_connection(target, timeout=CONNECT_TIMEOUT)
    with context.wrap_socket(raw) as sock:
        theirs = sock.getpeercert(binary_form=True)
        fp = fingerprint(theirs)
        wire = Wire(sock)
        known = fp in paired.all()
        wire.send({"type": "hello", "name": socket.gethostname(), "version": PROTOCOL, "paired": known})
        hello, _ = wire.receive()
        name = hello.get("name", "tablet")
        if not known or not hello.get("paired"):
            code = pairing_code(ours, theirs)
            log(f"companion: pairing with {name}, code {code}")
            sock.settimeout(PAIRING_TIMEOUT + 10)
            accepted = ask_pairing(name, code)
            wire.send({"type": "pair-ok" if accepted else "pair-no"})
            answer, _ = wire.receive()
            if not accepted or answer.get("type") != "pair-ok":
                raise PairingRefused(name)
            paired.add(fp, name)
        with open(path, "rb") as f:
            pdf = f.read()
        job = uuid.uuid4().hex
        sock.settimeout(CONNECT_TIMEOUT * 6)
        wire.send({"type": "sign", "job": job, "title": os.path.basename(path)}, pdf)
        # Signing takes as long as the user wants.
        sock.settimeout(None)
        while True:
            message, _ = wire.receive()
            kind = message.get("type")
            if kind == "opened" and on_opened:
                on_opened(name)
            elif kind == "signed" and message.get("job") == job:
                wire.send({"type": "bye"})
                return message.get("marks") or {"pages": []}
            elif kind == "cancelled" and message.get("job") == job:
                wire.send({"type": "bye"})
                return None


def to_marks(result):
    """The app's marks as plusone/pdf.py takes them: {page index: [Stroke]}."""
    from .pdf import Stroke

    marks = {}
    for page in result.get("pages", []):
        strokes = []
        for mark in page.get("marks", []):
            if mark.get("type") != "stroke":
                continue
            p = mark.get("points", [])
            points = [(p[i], p[i + 1], p[i + 2]) for i in range(0, len(p) - 2, 3)]
            if points:
                strokes.append(Stroke(points, width=float(mark.get("width", 1.6)),
                                      color=tuple(mark.get("color", (0.05, 0.1, 0.35)))))
        if strokes:
            marks[int(page["page"])] = strokes
    return marks
