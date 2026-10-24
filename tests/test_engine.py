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
