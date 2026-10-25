"""The controller's way out when something on the way to streaming fails."""
import types

from gi.repository import GLib

from plusone import engine
from plusone.engine import Controller, Settings, State


class FakeTablets:
    """The tablet in use (tablets.py), and what verify() says of an address."""

    def __init__(self, active=True, problem=None):
        self.tablet = types.SimpleNamespace(name="Tab") if active else None
        self.problem, self.checked = problem, []

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
    controller._running = True
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
        controller._serve(left, ("192.168.1.20", 5000))
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
        controller._serve(left, ("192.168.49.7", 5000))
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
        controller._serve(left, ("192.168.49.7", 5000))
    assert len(served) == 1 and tablets.checked == ["192.168.49.7"]
