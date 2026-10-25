"""The tablet's camera and microphone as the PC's: who uses them, and the tablet asked only then."""
import time
import types

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib  # noqa: E402

from plusone import companion, devices  # noqa: E402


def spin(until, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until():
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return until()


def test_links_from_pw_link():
    parse = devices.LinkParser()
    assert parse("= plusone_mic:capture_MONO") is None
    # One there already, under its port; then one made and removed.
    assert parse("=   |-> pw-record:input_MONO") == ("=", ("plusone_mic:capture_MONO", "pw-record:input_MONO"))
    assert parse("+ plusone_mic:capture_MONO -> Chrome:input_MONO") == \
        ("+", ("plusone_mic:capture_MONO", "Chrome:input_MONO"))
    assert parse("- plusone_mic:capture_MONO -> Chrome:input_MONO")[0] == "-"
    assert parse("garbage") is None


def test_opening_a_device_is_counted(tmp_path):
    path = tmp_path / "video9"
    path.write_bytes(b"")
    counts = []
    opened = devices.Opened(str(path), counts.append)
    try:
        first = open(path)
        assert spin(lambda: counts and counts[-1] == 1)
        second = open(path, "rb")
        assert spin(lambda: counts[-1] == 2)
        first.close()
        assert spin(lambda: counts[-1] == 1)
        second.close()
        assert spin(lambda: counts[-1] == 0)
    finally:
        opened.close()


class FakeApp:
    def __init__(self):
        self.changes = 0
        tablet = types.SimpleNamespace(app_id="a1b2" + "0" * 12)
        self.tablets = types.SimpleNamespace(active=lambda: tablet, addresses=lambda t: [("10.0.0.2", 47236)])

    def devices_changed(self):
        self.changes += 1


class Probe(devices.Device):
    def __init__(self, app):
        super().__init__(app, "camera")
        self.calls = []

    def begin(self):
        self.calls.append("begin")

    def end(self):
        self.calls.append("end")


def test_the_tablet_only_while_a_program_uses_it(monkeypatch):
    monkeypatch.setattr(devices, "IN_USE_AFTER", 0.01)
    monkeypatch.setattr(devices, "IDLE_AFTER", 0.05)
    device = Probe(FakeApp())
    device.state = "ready"
    device._used(True)
    assert spin(lambda: device.calls == ["begin"]) and device.state == "in use"
    # A program that opens it to look at it and closes it at once: nothing.
    device._used(False)
    device._used(True)
    spin(lambda: False, timeout=0.15)
    assert device.calls == ["begin"]
    device._used(False)
    assert spin(lambda: device.calls == ["begin", "end"]) and device.state == "ready"


def test_nothing_while_switched_off(monkeypatch):
    monkeypatch.setattr(devices, "IN_USE_AFTER", 0.01)
    device = Probe(FakeApp())
    device._used(True)
    spin(lambda: False, timeout=0.1)
    assert device.calls == [] and device.state == "off"


def test_the_feed_asks_the_tablet_in_use_and_hangs_up(monkeypatch):
    left, right = __import__("socket").socketpair()
    pieces, asked = [], []

    class Wire:
        n = 0

        def receive_piece(self):
            Wire.n += 1
            if Wire.n > 3:
                left.recv(1)  # blocks until stop() shuts the socket
                raise EOFError
            return {"type": "audio", "size": 2}, b"ab"

    def capture(targets, request, expect):
        asked.append((targets, request["type"], expect))
        return left, Wire(), {"type": "microphone", "codec": "opus"}
    monkeypatch.setattr(companion, "capture", capture)
    infos = []
    feed = devices.Feed({"type": "microphone"}, lambda: ([("10.0.0.2", 47236)], "a1b2"),
                        infos.append, lambda h, p: pieces.append(p), lambda reason: None)
    feed.start()
    assert spin(lambda: len(pieces) == 3)
    feed.stop()
    feed._thread.join(5)
    right.close()
    assert asked == [([("10.0.0.2", 47236)], "microphone", "a1b2")] and infos[0]["codec"] == "opus"


def test_a_refusal_is_said_and_asked_again_later(monkeypatch):
    monkeypatch.setattr(devices, "REFUSED_RETRY", 0.05)
    tries, problems = [], []

    def capture(*args):
        tries.append(1)
        raise companion.Refused("permission")
    monkeypatch.setattr(companion, "capture", capture)
    feed = devices.Feed({"type": "camera"}, lambda: ([("10.0.0.2", 47236)], "a1b2"),
                        None, None, problems.append)
    feed.start()
    assert spin(lambda: len(tries) >= 2)
    feed.stop()
    feed._thread.join(5)
    assert problems[0] == "permission"


def test_no_tablet_on_the_network(monkeypatch):
    monkeypatch.setattr(devices, "RETRY", 0.05)
    problems = []
    feed = devices.Feed({"type": "camera"}, lambda: ([], "a1b2"), None, None, problems.append)
    feed.start()
    assert spin(lambda: problems)
    feed.stop()
    feed._thread.join(5)
    assert problems[0] == "away"


def test_pieces_from_the_app(tmp_path):
    import socket
    import struct
    left, right = socket.socketpair()
    with left, right:
        def piece(header, payload=b""):
            data = __import__("json").dumps(header).encode()
            right.sendall(struct.pack(">I", len(data)) + data + payload)
        piece({"type": "video", "size": 3, "key": True}, b"xyz")
        piece({"type": "video", "size": companion.MAX_PIECE + 1})
        wire = companion.Wire(left)
        assert wire.receive_piece() == ({"type": "video", "size": 3, "key": True}, b"xyz")
        try:
            wire.receive_piece()
            raise AssertionError("too big a piece was taken")
        except ValueError:
            pass


def test_switched_on_only_with_a_tablet(monkeypatch):
    calls = []
    app = types.SimpleNamespace(settings=types.SimpleNamespace(camera=True, microphone=False),
                                tablets=types.SimpleNamespace(active=lambda: None))
    manager = devices.Devices.__new__(devices.Devices)
    manager.app = app
    manager.camera = types.SimpleNamespace(enable=lambda: calls.append("camera on"),
                                           disable=lambda: calls.append("camera off"))
    manager.microphone = types.SimpleNamespace(enable=lambda: calls.append("mic on"),
                                               disable=lambda: calls.append("mic off"))
    manager.apply()
    assert calls == ["camera off", "mic off"]  # no tablet paired: neither
    app.tablets.active = lambda: object()
    calls.clear()
    manager.apply()
    assert calls == ["camera on", "mic off"]


def test_a_stale_microphone_is_removed(monkeypatch):
    ran = []

    def run(args, **kwargs):
        ran.append(args)
        out = "536870913\tmodule-pipe-source\tsource_name=plusone_mic file=/x\n7\tmodule-x\t\n"
        return types.SimpleNamespace(stdout=out if args[1] == "list" else "")
    monkeypatch.setattr(devices.subprocess, "run", run)
    devices.unload_stale()
    assert ran[-1] == ["pactl", "unload-module", "536870913"]


def test_the_microphone_is_brought_to_a_voice():
    import array
    level = devices.Leveller()
    room = array.array("h", [50, -50] * 480)
    assert level(room) == 4.0  # quiet: the gain is left alone
    voice = array.array("h", [600, -600] * 480)
    gains = [level(voice) for _ in range(200)]
    assert gains[0] < gains[-1] and abs(gains[-1] - devices.Leveller.TARGET / 600) < 0.1
    shout = array.array("h", [20000, -20000] * 480)
    assert level(shout) * 20000 <= 32000  # at once, and no clipping
    assert level(array.array("h")) == level.gain
