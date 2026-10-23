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
