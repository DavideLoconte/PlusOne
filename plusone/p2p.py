"""Wi-Fi Direct group formation with a WFD sink through NetworkManager.

When Second Screen is waiting, the group forms about a second after we ask.
NetworkManager's peer list is not a reliable "ready" signal (peers linger
after the tablet closes the app), so we may ask before the tablet listens.
That is fine: wpa_supplicant keeps repeating the GO negotiation request for
the whole activation (~45 s) and the tablet answers as soon as Second Screen
is up. An attempt must never be cut short: if the tablet has just accepted,
it waits 30 s for us as group owner and then stops listening altogether.
"""
from gi.repository import GLib, NM

from . import log

# WFD IE subelement 0 (device info): source, session available, RTSP 7236.
WFD_IES = b"\x00\x00\x06\x00\x90\x1c\x44\x00\xc8"
ATTEMPT_DEADLINE = 55  # seconds; only if NetworkManager never fails the attempt
# If Second Screen was still starting, the OS accepts the group but the app
# never dials our RTSP port; drop such a group and try again.
RTSP_DEADLINE = 8


def is_wfd_sink(peer):
    ies = peer.get_wfd_ies()
    data = ies.get_data() if ies else b""
    # Subelement 0, length 6: device type in the low two bits (1 = primary sink).
    return len(data) >= 5 and data[0] == 0 and (data[4] & 0x3) in (1, 3)


class P2PLink:
    def __init__(self, match=None, on_found=None, on_hint=None):
        """match: peer MAC or substring of its name; None picks the first WFD sink.

        on_found(name) fires when the tablet shows up; on_hint() after repeated
        failed attempts, to ask the user to reopen Second Screen. Attempts go
        on until stop().
        """
        self.on_found = on_found or (lambda name: None)
        self.on_hint = on_hint or (lambda: None)
        self.stopped = False
        self._handler = None
        self.client = NM.Client.new(None)
        self.device = next(d for d in self.client.get_devices() if isinstance(d, NM.DeviceWifiP2P))
        self.match = match.lower() if match else None
        self.peer = None
        self.ac = None
        self.attempt = 0
        self.connected = False

    def _matches(self, peer):
        if self.match:
            return (self.match == (peer.get_hw_address() or "").lower()
                    or self.match in (peer.get_name() or "").lower())
        return is_wfd_sink(peer)

    def start(self):
        self._handler = self.device.connect("state-changed", self._state_changed)
        self.device.start_find(None, None, None, None)
        GLib.timeout_add(500, self._poll_peers)

    def _poll_peers(self):
        if self.peer or self.stopped:
            return False
        for peer in self.device.get_peers():
            if self._matches(peer):
                self.peer = peer
                log(f"found {peer.get_name()} ({peer.get_hw_address()})")
                self.on_found(peer.get_name())
                GLib.timeout_add_seconds(1, self._activate)
                return False
        return True

    def _activate(self):
        if self.stopped or self.connected:
            return False
        self.attempt += 1
        con = NM.SimpleConnection.new()
        s_con = NM.SettingConnection.new()
        s_con.add_permission("user", GLib.get_user_name(), None)
        con.add_setting(s_con)
        s_p2p = NM.SettingWifiP2P.new()
        s_p2p.set_property(NM.SETTING_WIFI_P2P_WFD_IES, GLib.Bytes.new(WFD_IES))
        con.add_setting(s_p2p)
        s_ip4 = NM.SettingIP4Config.new()
        s_ip4.set_property(NM.SETTING_IP_CONFIG_METHOD, NM.SETTING_IP4_CONFIG_METHOD_AUTO)
        s_ip4.set_property(NM.SETTING_IP_CONFIG_NEVER_DEFAULT, True)
        con.add_setting(s_ip4)
        s_ip6 = NM.SettingIP6Config.new()
        s_ip6.set_property(NM.SETTING_IP_CONFIG_METHOD, NM.SETTING_IP6_CONFIG_METHOD_AUTO)
        s_ip6.set_property(NM.SETTING_IP_CONFIG_NEVER_DEFAULT, True)
        s_ip6.set_property(NM.SETTING_IP_CONFIG_MAY_FAIL, True)
        con.add_setting(s_ip6)
        options = GLib.Variant("a{sv}", {
            "bind-activation": GLib.Variant("s", "dbus-client"),
            "persist": GLib.Variant("s", "volatile"),
        })
        log(f"Wi-Fi Direct: connecting (attempt {self.attempt})")
        self.client.add_and_activate_connection2(con, self.device, self.peer.get_path(),
                                                 options, None, self._activated)
        GLib.timeout_add_seconds(ATTEMPT_DEADLINE, self._deadline, self.attempt)
        return False

    def _deadline(self, attempt):
        if attempt != self.attempt or self.connected or self.stopped:
            return False
        if self.device.get_state() in (NM.DeviceState.PREPARE, NM.DeviceState.CONFIG,
                                       NM.DeviceState.NEED_AUTH):
            log(f"Wi-Fi Direct: attempt {attempt} still pending, trying again")
            self.device.disconnect_async(None, lambda *a: self._retry())
        return False

    def _rtsp_deadline(self, attempt):
        if attempt == self.attempt and not self.connected and not self.stopped:
            log("Wi-Fi Direct: group up but Second Screen did not start a session, retrying")
            self.device.disconnect_async(None, lambda *a: self._retry())
        return False

    def _retry(self):
        if self.attempt >= 2:
            self.on_hint()
        GLib.timeout_add_seconds(1, self._activate)

    def _activated(self, client, res):
        try:
            self.ac, _ = client.add_and_activate_connection2_finish(res)
        except GLib.Error as e:
            log("Wi-Fi Direct activation failed:", e.message)

    def _state_changed(self, dev, new, old, reason):
        if new == NM.DeviceState.ACTIVATED:
            log("Wi-Fi Direct group up")
            GLib.timeout_add_seconds(RTSP_DEADLINE, self._rtsp_deadline, self.attempt)
        elif new == NM.DeviceState.FAILED and not self.connected and self.peer and not self.stopped:
            log(f"Wi-Fi Direct attempt {self.attempt} failed (reason {reason})")
            self._retry()

    def stop(self, done=None):
        self.stopped = True
        if self._handler is not None:
            self.device.handler_disconnect(self._handler)
            self._handler = None
        self.device.stop_find(None, None, None)
        if self.ac is not None:
            self.client.deactivate_connection_async(self.ac, None,
                                                    lambda *a: done and done())
            self.ac = None
        elif done:
            done()
