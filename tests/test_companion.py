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
    pytest.importorskip("cryptography")
    cert, key, der = companion.identity(str(tmp_path))
    assert companion.identity(str(tmp_path))[2] == der
    assert oct((tmp_path / "key.pem").stat().st_mode)[-3:] == "600"


def test_paired_tablets_are_remembered(tmp_path):
    paired = companion.Paired(str(tmp_path))
    paired.add("ab" * 32, "Galaxy Tab")
    assert companion.Paired(str(tmp_path)).all() == {"ab" * 32: "Galaxy Tab"}
    paired.forget("ab" * 32)
    assert paired.all() == {}
