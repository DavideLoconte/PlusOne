"""The link to the tablet's app: pairing code, framing, marks."""
import socket

import pytest

from plusone import companion


def test_the_pairing_code_is_the_same_on_both_sides():
    a, b = b"pc certificate", b"tablet certificate"
    code = companion.pairing_code(a, b)
    assert code == companion.pairing_code(b, a)
    assert len(code) == 6 and code.isdigit()
    assert code != companion.pairing_code(a, b + b"!")  # a swapped certificate shows


def test_messages_with_and_without_payload():
    left, right = socket.socketpair()
    with left, right:
        companion.Wire(left).send({"type": "sign", "job": "x"}, b"%PDF-1.7 ...")
        companion.Wire(left).send({"type": "ping"})
        wire = companion.Wire(right)
        header, payload = wire.receive()
        assert header["type"] == "sign" and header["size"] == 12 and payload == b"%PDF-1.7 ..."
        assert wire.receive() == ({"type": "ping"}, None)


def test_a_closed_connection_is_an_eof():
    left, right = socket.socketpair()
    left.close()
    with right, pytest.raises(EOFError):
        companion.Wire(right).receive()


def test_marks_from_the_app():
    marks = companion.to_marks({"pages": [
        {"page": 0, "marks": [{"type": "stroke", "width": 1.6, "color": [0.1, 0.2, 0.3],
                               "points": [1, 2, 0.4, 3, 4, 0.5, 5, 6, 0.6]}]},
        {"page": 2, "marks": []}]})
    assert list(marks) == [0]
    stroke = marks[0][0]
    assert stroke.points == [(1, 2, 0.4), (3, 4, 0.5), (5, 6, 0.6)] and stroke.color == (0.1, 0.2, 0.3)


def test_identity_is_made_once(tmp_path):
    import shutil
    if not shutil.which("openssl"):
        pytest.skip("no openssl")
    cert, key, der = companion.identity(str(tmp_path))
    assert companion.identity(str(tmp_path))[2] == der
    assert oct((tmp_path / "key.pem").stat().st_mode)[-3:] == "600"


def test_paired_tablets_are_remembered(tmp_path):
    paired = companion.Paired(str(tmp_path))
    paired.add("ab" * 32, "Galaxy Tab")
    assert companion.Paired(str(tmp_path)).all() == {"ab" * 32: "Galaxy Tab"}


def test_text_from_the_app():
    marks = companion.to_marks({"pages": [{"page": 1, "marks": [
        {"type": "text", "x": 100, "y": 700, "text": "24/10/2026", "size": 11, "color": [0, 0, 0]}]}]})
    text = marks[1][0]
    assert (text.x, text.y, text.text, text.size) == (100.0, 700.0, "24/10/2026", 11.0)


def test_a_dropped_link_is_resumed(tmp_path, monkeypatch):
    # The link drops once the PDF is on the tablet; Plus One connects again
    # and gets the signature the user finished meanwhile.
    import shutil
    import ssl
    import threading
    if not shutil.which("openssl"):
        pytest.skip("no openssl")
    monkeypatch.setattr(companion, "CONFIG", str(tmp_path / "pc"))
    monkeypatch.setattr(companion, "RESUME_EVERY", 0.01)
    app_cert, app_key, app_der = companion.identity(str(tmp_path / "app"))
    paired = companion.Paired(str(tmp_path / "pc"))
    paired.add(companion.fingerprint(app_der), "Tab")
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(app_cert, app_key)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    marks = {"pages": [{"page": 0, "marks": []}]}
    ours, theirs = {"signatures": [], "removed": ["x"]}, {"signatures": [], "removed": ["y"]}
    seen, got = [], []

    def app():
        for attempt in range(2):
            raw, _ = listener.accept()
            with server_ctx.wrap_socket(raw, server_side=True) as sock:
                wire = companion.Wire(sock)
                wire.receive()
                wire.send({"type": "hello", "name": "Tab", "paired": True})
                message, payload = wire.receive()
                seen.append(message["type"])
                if attempt == 0:
                    assert message["signatures"] == ours
                    wire.send({"type": "opened", "job": message["job"]})
                    job = message["job"]
                else:
                    assert message == {"type": "resume", "job": job}
                    wire.send({"type": "signed", "job": job, "marks": marks, "signatures": theirs})
                    wire.receive()  # bye

    threading.Thread(target=app, daemon=True).start()
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    target = listener.getsockname()
    result = companion.sign(target, str(pdf), ask_pairing=None, paired=paired, locate=lambda: target,
                            signatures=ours, on_signatures=got.append)
    assert result == marks and seen == ["sign", "resume"] and got == [theirs]
    listener.close()
