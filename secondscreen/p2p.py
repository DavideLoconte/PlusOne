"""Wi-Fi Direct group formation with a WFD sink through NetworkManager.

Samsung's Second Screen approves an incoming connection only after our first
GO negotiation request has timed out; the tablet then negotiates back to us.
That works only while our activation is still pending and we are
discoverable, so we keep the activation alive and keep searching instead of
retrying quickly (which resets the exchange).
"""
from gi.repository import GLib, NM

from . import log

# WFD IE subelement 0 (device info): source, session available, RTSP 7236.
WFD_IES = b"\x00\x00\x06\x00\x90\x1c\x44\x00\xc8"
MAX_ATTEMPTS = 4


def is_wfd_sink(peer):
    ies = peer.get_wfd_ies()
    data = ies.get_data() if ies else b""
    # Subelement 0, length 6: device type in the low two bits (1 = primary sink).
    return len(data) >= 5 and data[0] == 0 and (data[4] & 0x3) in (1, 3)


class P2PLink:
    def __init__(self, match=None):
        """match: peer MAC or substring of its name; None picks the first WFD sink."""
        self.client = NM.Client.new(None)
        self.device = next(d for d in self.client.get_devices() if isinstance(d, NM.DeviceWifiP2P))
        self.match = match.lower() if match else None
        self.peer = None
        self.ac = None
        self.attempt = 0
        self.connected = False

    def _matches(self, peer):
        if self.match:
            return self.match in ((peer.get_hw_address() or "").lower(), ) or \
                self.match in (peer.get_name() or "").lower()
        return is_wfd_sink(peer)

    def start(self):
        self.device.connect("state-changed", self._state_changed)
        self.device.start_find(None, None, None, None)
        GLib.timeout_add(500, self._poll_peers)

    def _poll_peers(self):
        if self.peer:
            return False
        for peer in self.device.get_peers():
            if self._matches(peer):
                self.peer = peer
                log(f"found {peer.get_name()} ({peer.get_hw_address()})")
                # Give Second Screen a moment to finish setting up its listener.
                GLib.timeout_add_seconds(3, self._activate)
                return False
        return True

    def _activate(self):
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
        GLib.timeout_add_seconds(2, self._keep_discoverable, self.attempt)
        return False

    def _activated(self, client, res):
        try:
            self.ac, _ = client.add_and_activate_connection2_finish(res)
        except GLib.Error as e:
            log("Wi-Fi Direct activation failed:", e.message)

    def _keep_discoverable(self, attempt):
        if attempt != self.attempt or self.connected:
            return False
        if self.device.get_state() in (NM.DeviceState.PREPARE, NM.DeviceState.CONFIG):
            self.device.start_find(None, None, lambda *a: None, None)
            return True
        return False

    def _state_changed(self, dev, new, old, reason):
        if new == NM.DeviceState.ACTIVATED:
            log("Wi-Fi Direct group up")
        elif (new == NM.DeviceState.FAILED and not self.connected
              and self.attempt < MAX_ATTEMPTS and self.peer):
            log(f"Wi-Fi Direct attempt {self.attempt} failed (reason {reason}), retrying")
            GLib.timeout_add_seconds(2, self._activate)

    def stop(self, done=None):
        if self.ac is not None:
            self.client.deactivate_connection_async(self.ac, None,
                                                    lambda *a: done and done())
            self.ac = None
        elif done:
            done()
