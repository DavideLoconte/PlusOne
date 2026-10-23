import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib  # noqa: E402

from plusone.p2p import WFD_IES, is_wfd_sink  # noqa: E402


class Peer:
    def __init__(self, ies):
        self.ies = ies

    def get_wfd_ies(self):
        return GLib.Bytes.new(self.ies) if self.ies is not None else None


def test_samsung_second_screen_is_a_sink():
    # WFD IE advertised by a Galaxy Tab running Second Screen.
    assert is_wfd_sink(Peer(bytes.fromhex("0000060111c0060028")))


def test_sources_and_plain_peers_are_not_sinks():
    assert not is_wfd_sink(Peer(WFD_IES))      # our own source IE
    assert not is_wfd_sink(Peer(None))
    assert not is_wfd_sink(Peer(b""))


class Device:
    def __init__(self, state):
        self.state = state

    def get_state(self):
        return self.state


def link_in(state, connected=False):
    from plusone.p2p import P2PLink
    link = P2PLink.__new__(P2PLink)
    link.stopped, link.connected = False, connected
    link.device = Device(state)
    link.gave_up = False
    link.on_give_up = lambda: setattr(link, "gave_up", True)
    return link


def test_gives_up_when_idle():
    from gi.repository import NM
    link = link_in(NM.DeviceState.DISCONNECTED)
    assert link._give_up() is False and link.gave_up


def test_never_gives_up_during_an_attempt():
    from gi.repository import NM
    for state in (NM.DeviceState.CONFIG, NM.DeviceState.ACTIVATED):
        link = link_in(state)
        assert link._give_up() is True and not link.gave_up  # checks again later


def test_no_give_up_once_connected():
    from gi.repository import NM
    link = link_in(NM.DeviceState.ACTIVATED, connected=True)
    assert link._give_up() is False and not link.gave_up
