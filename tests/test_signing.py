"""Signing on the tablet: requests, windows and the connection opened for them."""
from gi.repository import GLib
import pytest

from plusone import i18n
from plusone import companion
from plusone.engine import NOT_ANSWERING, Settings, State
from plusone import signing as signing_module
from plusone.signing import Signing
from plusone.tablets import Tablets

TAB = "a1b2" + "0" * 60  # the paired tablet's fingerprint
APP = TAB[:16]           # what its app announces


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
        self.app_on = None  # (address, port, name, app id) of the companion app, if any

    def apps(self):
        return [(a[3], a[0], a[1], a[2]) for a in [self.app_on] if a]

    def addresses(self, app_id):
        return [(a[0], a[1]) for a in [self.app_on] if a and a[3] == app_id]


class FakePaired:
    def __init__(self, entries):
        self.data = entries

    def entries(self):
        return {fp: dict(entry) for fp, entry in self.data.items()}

    def learn(self, fp, **fields):
        self.data[fp].update(fields)

    def forget(self, fp):
        self.data.pop(fp, None)


class FakeApp:
    def __init__(self, paired=True):
        self.controller = FakeController()
        self.settings = Settings()
        self.nearby = FakeNearby()
        self.tablets = Tablets(self.settings, nearby=self.nearby,
                               paired=FakePaired({TAB: {"name": "Galaxy Tab"}} if paired else {}))
        self.notes = []

    def add_action(self, action):
        pass

    def send_notification(self, id, note):
        self.notes.append(id)

    def withdraw_notification(self, id):
        pass

    def save_settings(self):
        pass

    held = 0

    def hold(self):
        self.held += 1

    def release(self):
        self.held -= 1


class FakeWindow:
    def __init__(self, path):
        self.path, self.presented = path, 0

    def present(self):
        self.presented += 1


@pytest.fixture(autouse=True)
def pdfs(tmp_path, monkeypatch):
    """The PDFs the tests sign, and where the copies sent to the app go."""
    for name in ("form.pdf", "other.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setattr(signing_module, "SENT", str(tmp_path / "sent"))


def make(monkeypatch, paired=True):
    app = FakeApp(paired)
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
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab", APP)
    marks = {"pages": [{"page": 1, "marks": [
        {"type": "stroke", "width": 1.2, "color": [0, 0, 0], "points": [10, 20, 0.5, 30, 40, 0.6]}]}]}
    asked, saved = [], []
    monkeypatch.setattr(companion, "sign", lambda target, path, **kw: asked.append(
        (target, path, kw["expect"], kw["title"])) or marks)
    import plusone.pdf
    monkeypatch.setattr(plusone.pdf, "save_signed",
                        lambda src, m, like: saved.append((src, m, like)) or like + ".signed")
    path = str(tmp_path / "form.pdf")
    signing.request(path)
    assert spin(lambda: saved)
    # The copy sent is what was signed and what the marks go on; the result next to the original.
    (target, sent, expect, title), = asked
    assert target == [("10.0.0.2", 40000)] and expect == APP and title == "form.pdf"
    assert sent.startswith(str(tmp_path / "sent")) and app.controller.starts == 0  # no Second Screen
    src, marks_saved, like = saved[0]
    assert src == sent and like == path
    stroke = marks_saved[1][0]
    assert stroke.points == [(10, 20, 0.5), (30, 40, 0.6)] and stroke.width == 1.2
    assert spin(lambda: path not in signing.in_app)
    assert spin(lambda: app.held == 0) and not (tmp_path / "sent").exists() or not list((tmp_path / "sent").iterdir())


def test_an_unreachable_app_falls_back_to_second_screen(monkeypatch, tmp_path):
    app, signing, _, notes = make(monkeypatch)
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab", APP)

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


def test_only_the_tablet_in_use_signs(monkeypatch, tmp_path):
    # Another tablet's app on the network, paired or not, gets nothing.
    app, signing, _, _ = make(monkeypatch)
    app.nearby.app_on = ("10.0.0.7", 40000, "Someone's Tab", "ffff" + "0" * 12)
    monkeypatch.setattr(companion, "sign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    signing.request(str(tmp_path / "form.pdf"))
    assert app.controller.starts == 1  # Second Screen, which checks the tablet itself


def test_nothing_is_sent_without_a_paired_tablet(monkeypatch, tmp_path):
    app, signing, _, notes = make(monkeypatch, paired=False)
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab", APP)
    monkeypatch.setattr(companion, "sign", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent")))
    signing.request(str(tmp_path / "form.pdf"))
    assert app.notes == ["sign"] and app.controller.starts == 0 and not signing.queue


def test_one_app_per_tablet():
    from plusone.nearby import Nearby
    nearby = Nearby()
    nearby.companions = {"a": ("10.0.0.2", 1000, "Kitchen", "aaaa"), "b": ("10.0.0.9", 2000, "Office", "bbbb"),
                         "b6": ("10.0.0.9", 2000, "Office", "bbbb")}  # the same app on two interfaces
    assert [a[0] for a in nearby.apps()] == ["aaaa", "bbbb"]


def test_a_dropped_link_finds_the_same_tablet_again(monkeypatch):
    # Two tablets on the network: the PDF's comes back from its own, not
    # from whichever would be picked for a new PDF.
    from plusone.nearby import Nearby
    app, signing, _, _ = make(monkeypatch)
    app.nearby = Nearby()
    app.nearby.companions = {"a": ("10.0.0.2", 1000, "Kitchen", "aaaa"), "b": ("10.0.0.9", 2001, "Office", "bbbb")}
    assert signing._locate_app("bbbb") == [("10.0.0.9", 2001)]
    assert signing._locate_app("cccc") is None


def test_signed_on_the_tablet_is_never_lost_for_a_folder_that_takes_nothing(monkeypatch, tmp_path):
    app, signing, _, _ = make(monkeypatch)
    app.nearby.app_on = ("10.0.0.2", 40000, "Galaxy Tab", APP)
    marks = {"pages": [{"page": 0, "marks": [
        {"type": "stroke", "width": 1.2, "color": [0, 0, 0], "points": [10, 20, 0.5, 30, 40, 0.6]}]}]}
    monkeypatch.setattr(companion, "sign", lambda target, path, **kw: marks)
    documents = tmp_path / "Documents"
    documents.mkdir()
    monkeypatch.setattr(signing_module.GLib, "get_user_special_dir", lambda which: str(documents))
    import plusone.pdf
    path = str(tmp_path / "form.pdf")

    def save(src, m, like):
        if like == path:
            raise PermissionError(13, "Permission denied")  # a read-only folder
        return like.replace(".pdf", "-signed.pdf")
    monkeypatch.setattr(plusone.pdf, "save_signed", save)
    results = []
    monkeypatch.setattr(signing, "_signed", lambda result, certified=False: results.append(result))
    signing.request(path)
    assert spin(lambda: results)
    assert results == [str(documents / "form-signed.pdf")]


def test_what_is_not_a_pdf_is_said_before_it_goes():
    import plusone.pdf
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(b"just text")
        f.flush()
        assert plusone.pdf.problem(f.name)[0] == "{name} is not a PDF"
    assert plusone.pdf.problem("/no/such.pdf")[0] == "{name} is not a file"


def test_a_long_signature_is_thinned_not_lost(tmp_path):
    from plusone import signatures
    stroke = [[i / 30000, 0.1, 0.5] for i in range(30000)]
    path = str(tmp_path / "signatures.json")
    kept = signatures.add({"width": 120.0, "strokes": [stroke]}, path)
    loaded, _removed = signatures.load(path)
    assert [s["id"] for s in loaded] == [kept["id"]]  # there after a load, not dropped
    points = loaded[0]["strokes"][0]
    assert len(points) <= signatures.MAX_POINTS and points[0] == stroke[0] and points[-1] == stroke[-1]
