"""Signing on the tablet: requests, windows and the connection opened for them."""
from gi.repository import GLib

from plusone import i18n
from plusone import companion
from plusone.engine import NOT_ANSWERING, Settings, State
from plusone.signing import Signing


class FakeController:
    def __init__(self):
        self.state, self.detail, self.running = State.IDLE, ("", {}), False
        self.starts = self.stops = 0

    def start(self):
        self.running, self.starts = True, self.starts + 1

    def stop(self):
        self.running, self.stops = False, self.stops + 1


class FakeNearby:
    def __init__(self):
        self.app_on = None  # (address, port, name) of the companion app, if any

    def companion(self, paired=()):
        return self.app_on


class FakeApp:
    def __init__(self):
        self.controller = FakeController()
        self.settings = Settings()
        self.nearby = FakeNearby()
        self.notes = []

    def add_action(self, action):
        pass

    def send_notification(self, id, note):
        self.notes.append(id)

    def withdraw_notification(self, id):
        pass


class FakeWindow:
    def __init__(self, path):
        self.path, self.presented = path, 0

    def present(self):
        self.presented += 1


def make(monkeypatch):
    app = FakeApp()
    signing = Signing(app)
    opened, notes = [], []
    monkeypatch.setattr(signing, "_open_on_tablet", opened.append)
    monkeypatch.setattr(signing, "_notify_waiting", lambda path, body: notes.append((path, body)))
    return app, signing, opened, notes


def test_asking_again_while_waiting_opens_one_window(monkeypatch, tmp_path):
    # Files was clicked three times while the tablet took its time: Done must
    # still close the only window and the connection opened for it.
    app, signing, opened, _ = make(monkeypatch)
    path = str(tmp_path / "form.pdf")
    for _ in range(3):
        signing.request(path)
    assert signing.queue == [path] and app.controller.starts == 1
    app.controller.state = State.STREAMING
    signing.on_state(State.STREAMING)
    assert opened == [path]
    window = FakeWindow(path)
    signing.windows.append(window)
    signing.request(path)
    assert window.presented == 1 and opened == [path]
    signing._done(window, path, None)
    while GLib.MainContext.default().iteration(False):
        pass
    assert app.controller.stops == 1


def test_no_disconnect_while_another_document_waits_for_the_monitor(monkeypatch, tmp_path):
    app, signing, _, _ = make(monkeypatch)
    signing.connected_for_us = True
    signing.opening.add(str(tmp_path / "other.pdf"))
    signing._maybe_disconnect()
    assert app.controller.stops == 0


def test_not_answering_reaches_the_notification(monkeypatch, tmp_path):
    app, signing, _, notes = make(monkeypatch)
    path = str(tmp_path / "form.pdf")
    signing.request(path)
    app.controller.state, app.controller.detail = State.CONNECTING, (NOT_ANSWERING, {})
    assert signing.on_state(State.CONNECTING) is True
    assert notes[-1] == (path, i18n._(NOT_ANSWERING))


def test_a_start_that_fails_at_once_keeps_its_notification(monkeypatch, tmp_path):
    # GNOME Network Displays running, say: "sign on this PC instead" must
    # not be replaced by "tap Second screen".
    app, signing, _, notes = make(monkeypatch)

    def failing_start():
        app.controller.state = State.ERROR
        signing.on_state(State.ERROR)
    app.controller.start = failing_start
    signing.request(str(tmp_path / "form.pdf"))
    assert notes == [] and signing.queue == []


def spin(until, timeout=5):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end and not until():
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    return until()


def test_the_tablets_app_signs_when_it_is_on_the_network(monkeypatch, tmp_path):
    app, signing, _, _ = make(monkeypatch)
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab")
    marks = {"pages": [{"page": 1, "marks": [
        {"type": "stroke", "width": 1.2, "color": [0, 0, 0], "points": [10, 20, 0.5, 30, 40, 0.6]}]}]}
    asked, saved = [], []
    monkeypatch.setattr(companion, "sign", lambda target, path, ask, **kw: asked.append((target, path)) or marks)
    import plusone.pdf
    monkeypatch.setattr(plusone.pdf, "save_signed", lambda path, m: saved.append(m) or path + ".signed")
    path = str(tmp_path / "form.pdf")
    signing.request(path)
    assert spin(lambda: saved)
    assert asked == [(("10.0.0.2", 40000), path)] and app.controller.starts == 0  # no Second Screen
    stroke = saved[0][1][0]
    assert stroke.points == [(10, 20, 0.5), (30, 40, 0.6)] and stroke.width == 1.2
    assert spin(lambda: path not in signing.in_app)


def test_an_unreachable_app_falls_back_to_second_screen(monkeypatch, tmp_path):
    app, signing, _, notes = make(monkeypatch)
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab")

    def unreachable(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(companion, "sign", unreachable)
    signing.request(str(tmp_path / "form.pdf"))
    assert spin(lambda: app.controller.starts == 1)


def test_pen_and_signatures_from_the_tablet_are_kept(monkeypatch, tmp_path):
    from plusone import signatures
    from plusone import signing as signing_module
    from plusone.ink import load_pen
    monkeypatch.setattr(signing_module, "PEN", str(tmp_path / "pen.json"))
    app, signing, _, _ = make(monkeypatch)
    signing._keep_from_app({"pen": {"color": [0.8, 0.1, 0.1], "width": 0.9}})
    assert load_pen(str(tmp_path / "pen.json"))[:2] == ((0.8, 0.1, 0.1), 0.9)
    signing._merge_signatures({"signatures": [{"id": "t1", "width": 120.0,
                                               "strokes": [[[0, 0, 0.5], [1, 0.2, 0.5]]]}], "removed": []})
    assert [s["width"] for s in signatures.load()[0]] == [120.0]


def test_the_first_answer_to_pairing_counts(monkeypatch):
    # Closing the dialog after "Pair" answers "close" too: it must not undo the pairing.
    import threading
    app, signing, _, _ = make(monkeypatch)
    shown = []
    monkeypatch.setattr(signing, "_show_pairing", lambda tablet, code: shown.append(code))
    monkeypatch.setattr(signing, "_pairing_over", lambda code: None)
    results = []
    askers = [threading.Thread(target=lambda: results.append(signing._ask_pairing("Tab", "123456")))
              for _ in range(2)]  # two PDFs at once: the same tablet, the same code
    for t in askers:
        t.start()
    assert spin(lambda: shown) and spin(lambda: "123456" in signing.pairing)
    signing._answer_pairing("123456", True)
    signing._answer_pairing("123456", False)
    for t in askers:
        t.join(5)
    assert results == [True, True] and shown == ["123456"]


def test_a_paired_app_comes_first():
    from plusone.nearby import Nearby
    nearby = Nearby()
    nearby.companions = {"a": ("10.0.0.2", 1000, "Stranger", "aaaa"), "b": ("10.0.0.9", 2000, "Tab", "bbbb")}
    assert nearby.companion() == ("10.0.0.2", 1000, "Stranger")
    assert nearby.companion(["bbbb" + "0" * 60]) == ("10.0.0.9", 2000, "Tab")
