"""The controller's way out when something on the way to streaming fails."""
import types

from gi.repository import GLib

from plusone import engine
from plusone.engine import Controller, Settings, State


class FakeTablets:
    """The tablet in use (tablets.py), and what verify() says of an address."""

    def __init__(self, active=True, problem=None, app_at=()):
        self.tablet = types.SimpleNamespace(name="Tab", app_id="0123456789abcdef") if active else None
        self.problem, self.checked, self.app_at = problem, [], list(app_at)

    def addresses(self, tablet):
        return self.app_at

    def screen_seen(self, screen):
        self.screen = screen

    def active(self):
        return self.tablet

    def match(self):
        return ["Tab"]

    def verify(self, address):
        self.checked.append(address)
        return self.problem

    def learned(self, wifi_direct):
        pass


def test_no_wifi_direct_is_an_error_not_a_wait(monkeypatch):
    def no_p2p_device(*args, **kwargs):
        raise StopIteration  # next() over the devices found none
    monkeypatch.setattr(engine, "P2PLink", no_p2p_device)
    states = []
    controller = Controller(Settings(), on_state=lambda state, template, args: states.append((state, template)),
                            tablets=FakeTablets())
    controller._running = True
    controller._wait_for_tablet(10)
    assert states[-1] == (State.ERROR, "This PC's Wi-Fi cannot do Wi-Fi Direct")
    assert not controller.running and controller.link is None


def test_no_screen_without_a_paired_tablet():
    states = []
    controller = Controller(Settings(), on_state=lambda state, template, args: states.append((state, template)),
                            tablets=FakeTablets(active=False))
    controller.start()
    assert states == [(State.ERROR, "Pair a tablet first")] and not controller.running


def group_member(monkeypatch, tablets):
    """A controller whose Wi-Fi Direct group is 192.168.49.0/24, and the
    sessions it would serve."""
    import ipaddress
    controller = Controller(Settings(), tablets=tablets)
    controller._running, controller._generation = True, 1
    network = ipaddress.ip_network("192.168.49.0/24")
    controller.link = types.SimpleNamespace(network=lambda: network, connected=False, stop=lambda: None,
                                            owns=lambda a: ipaddress.ip_address(a) in network)
    served = []
    monkeypatch.setattr(engine, "WfdSession",
                        lambda *a, **k: served.append(a) or types.SimpleNamespace(run=lambda: None))
    return controller, served


def test_only_the_wi_fi_direct_group_gets_the_desktop(monkeypatch):
    # Port 7236 listens on every interface: a LAN host dialling in is hung up
    # on, the tablet on the group's network is served.
    import socket
    controller, served = group_member(monkeypatch, FakeTablets())
    assert controller._from_tablet("192.168.49.7")
    assert not controller._from_tablet("192.168.1.20")
    left, right = socket.socketpair()
    with left, right:
        controller._serve(left, ("192.168.1.20", 5000), 1)
        assert served == [] and controller.link.connected is False and controller._idle.is_set()
    assert controller.tablets.checked == []  # not even asked about


def test_only_the_tablet_in_use_gets_the_desktop(monkeypatch):
    # In the group, but not proven the paired tablet: hung up on, and the
    # user told why.
    import socket
    states = []
    tablets = FakeTablets(problem=("This is not {name}: the desktop was not sent", {"name": "Tab"}))
    controller, served = group_member(monkeypatch, tablets)
    controller.on_state = lambda state, template, args: states.append((state, template))
    left, right = socket.socketpair()
    with left, right:
        controller._serve(left, ("192.168.49.7", 5000), 1)
        assert served == [] and tablets.checked == ["192.168.49.7"]
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)
    assert states[-1] == (State.ERROR, "This is not {name}: the desktop was not sent")
    assert not controller.running


def test_the_tablet_in_use_is_served(monkeypatch):
    import socket
    tablets = FakeTablets()
    controller, served = group_member(monkeypatch, tablets)
    left, right = socket.socketpair()
    with left, right:
        controller._serve(left, ("192.168.49.7", 5000), 1)
    assert len(served) == 1 and tablets.checked == ["192.168.49.7"]


def test_stopped_while_the_tablet_is_verified_starts_no_session(monkeypatch):
    # Disconnect pressed during the seconds the tablet's check takes: no
    # session, no touch server, and the next Connect is not blocked by it.
    import socket
    tablets = FakeTablets()
    controller, served = group_member(monkeypatch, tablets)

    def verify(address):
        controller._running = False  # stop() meanwhile
        controller._generation += 1
        return None
    tablets.verify = verify
    left, right = socket.socketpair()
    with left, right:
        controller._serve(left, ("192.168.49.7", 5000), 1)
    assert served == [] and controller.session is None and controller.uibc is None
    assert controller._idle.is_set() and controller.link.connected is False


def test_a_session_that_cannot_start_leaves_nothing_behind(monkeypatch):
    import socket
    controller, served = group_member(monkeypatch, FakeTablets())

    def hung_up(*args, **kwargs):
        raise OSError(107, "Transport endpoint is not connected")
    monkeypatch.setattr(engine, "WfdSession", hung_up)
    left, right = socket.socketpair()
    with left, right:
        try:
            controller._serve(left, ("192.168.49.7", 5000), 1)
        except OSError:
            pass
    assert controller._idle.is_set() and controller.link.connected is False and controller.session is None


def test_a_lan_connection_is_hung_up_on_at_once(monkeypatch):
    # Over an interface the PC had before Wi-Fi Direct: not even waited on.
    import time
    controller, served = group_member(monkeypatch, FakeTablets())
    controller._lan = {"10.0.0.5"}
    controller.link.network = lambda: None  # the group not up: _from_tablet would wait 3 s
    closed = []
    conn = types.SimpleNamespace(getsockname=lambda: ("10.0.0.5", 7236), close=lambda: closed.append(1))
    start = time.monotonic()
    controller._serve(conn, ("10.0.0.9", 5000), 1)
    assert time.monotonic() - start < 0.5 and closed and served == [] and controller.tablets.checked == []


def test_the_pen_is_never_the_whole_desktop_s(monkeypatch):
    # Not mapped onto the virtual monitor, a uhid tablet would point and
    # click on the PC's own screens: the pointer pen, kept in the stream.
    monkeypatch.setattr(engine.pen, "uhid_available", lambda: True)
    monkeypatch.setattr(engine.pen, "virtual_monitor_edid", lambda: None)
    made = []
    monkeypatch.setattr(engine.pen, "UhidPen", lambda: made.append("uhid"))
    controller = Controller(Settings(), tablets=FakeTablets())
    controller.monitor, controller.mode = object(), (1920, 1080, 60)
    device = controller._make_pen()
    assert made == [] and isinstance(device, engine.pen.PointerPen)


class FakeAppDisplay:
    made = []

    def __init__(self, targets, app_id, mode, bitrate, encoder, **callbacks):
        self.targets, self.mode, self.callbacks, self.stopped = targets, mode, callbacks, False
        FakeAppDisplay.made.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def app_controller(monkeypatch, settings=None, app_at=(("10.0.0.7", 47236),)):
    from plusone import appdisplay
    FakeAppDisplay.made = []
    monkeypatch.setattr(appdisplay, "AppDisplay", FakeAppDisplay)
    monkeypatch.setattr(engine.media, "pick_encoder", lambda preferred: "x264")
    monkeypatch.setattr(engine.media, "warm_up", lambda name: None)
    second = []
    controller = Controller(settings or Settings(), tablets=FakeTablets(app_at=app_at))
    monkeypatch.setattr(controller, "_start_second_screen", lambda: second.append(1))
    return controller, second


def test_the_app_shows_the_desktop_when_it_answers(monkeypatch):
    controller, second = app_controller(monkeypatch)
    controller.start()
    assert second == [] and len(FakeAppDisplay.made) == 1
    shown = FakeAppDisplay.made[0]
    # The size once the tablet says its screen (about 1920x1200 at its proportions).
    assert shown.targets == [("10.0.0.7", 47236)] and shown.mode == (None, None, 60)
    assert controller.state == State.CONNECTING
    shown.callbacks["on_started"]((1920, 1200, 60), (2880, 1800))
    assert controller.state == State.STREAMING and controller.tablets.screen == (2880, 1800)
    shown.callbacks["on_paused"](True)
    assert "paused" in controller.detail[0] and controller.state == State.STREAMING
    controller.stop()
    assert shown.stopped and controller.state == State.IDLE and not controller.running


def test_second_screen_when_the_app_cannot(monkeypatch):
    controller, second = app_controller(monkeypatch)
    controller.start()
    FakeAppDisplay.made[0].callbacks["on_ended"](OSError("refused"), False)
    assert second == [1]  # nothing was shown yet: Second Screen instead


def test_the_tablet_ending_it_ends_it(monkeypatch):
    controller, second = app_controller(monkeypatch)
    controller.start()
    shown = FakeAppDisplay.made[0]
    shown.callbacks["on_started"]((1920, 1200, 60))
    shown.callbacks["on_ended"](EOFError(), True)
    assert second == [] and controller.state == State.IDLE and not controller.running


def test_second_screen_without_the_app_or_when_chosen(monkeypatch):
    controller, second = app_controller(monkeypatch, app_at=())
    controller.start()
    settings = Settings()
    settings.display_via = "second-screen"
    other, second_too = app_controller(monkeypatch, settings=settings)
    other.start()
    assert second == [1] and second_too == [1] and FakeAppDisplay.made == []


def test_a_late_callback_of_an_ended_session_changes_nothing(monkeypatch):
    controller, second = app_controller(monkeypatch)
    controller.start()
    old = FakeAppDisplay.made[0]
    controller.stop()
    old.callbacks["on_started"]((1920, 1200, 60))
    old.callbacks["on_ended"](OSError(), False)
    assert controller.state == State.IDLE and second == []


def test_a_size_chosen_for_the_app_is_asked_for(monkeypatch):
    settings = Settings()
    settings.app_size = "2880x1800"
    controller, second = app_controller(monkeypatch, settings=settings)
    controller.start()
    assert FakeAppDisplay.made[0].mode == (2880, 1800, 60)


def test_sizes_are_read_with_care():
    assert engine.parse_size("2880x1800") == (2880, 1800)
    for nonsense in ("auto", None, "", "12x9", "axb", "99999x2"):
        assert engine.parse_size(nonsense) is None
