"""The controller's way out when something on the way to streaming fails."""
from plusone import engine
from plusone.engine import Controller, Settings, State


def test_no_wifi_direct_is_an_error_not_a_wait(monkeypatch):
    def no_p2p_device(*args, **kwargs):
        raise StopIteration  # next() over the devices found none
    monkeypatch.setattr(engine, "P2PLink", no_p2p_device)
    states = []
    controller = Controller(Settings(), on_state=lambda state, template, args: states.append((state, template)))
    controller._running = True
    controller._wait_for_tablet(10)
    assert states[-1] == (State.ERROR, "This PC's Wi-Fi cannot do Wi-Fi Direct")
    assert not controller.running and controller.link is None


def test_only_the_wi_fi_direct_group_gets_the_desktop(monkeypatch):
    # Port 7236 listens on every interface: a LAN host dialling in is hung up
    # on, the tablet on the group's network is served.
    import ipaddress
    import socket
    import types
    controller = Controller(Settings())
    controller._running = True
    controller.link = types.SimpleNamespace(networks=(ipaddress.ip_network("192.168.49.0/24"),),
                                            owns=lambda a: a.startswith("192.168.49."), connected=False)
    assert controller._from_tablet("192.168.49.7")
    assert not controller._from_tablet("192.168.1.20")
    served = []
    monkeypatch.setattr(engine, "WfdSession", lambda *a, **k: served.append(a) or types.SimpleNamespace(run=lambda: None))
    left, right = socket.socketpair()
    with left, right:
        controller._serve(left, ("192.168.1.20", 5000))
        assert served == [] and controller.link.connected is False and controller._idle.is_set()


def test_a_display_streamed_to_is_known_from_then_on():
    import types
    controller = Controller(Settings())
    peer = types.SimpleNamespace(get_name=lambda: "Tab S10 FE+", get_hw_address=lambda: "AA:BB:CC:DD:EE:FF")
    controller._learn(peer)
    controller._learn(peer)
    assert controller.settings.known_sinks == ["Tab S10 FE+", "aa:bb:cc:dd:ee:ff"]
