"""Wi-Fi Direct group formation with a WFD sink through NetworkManager.

When Second Screen is waiting, the group forms about a second after we ask.
NetworkManager's peer list is not a reliable "ready" signal (peers linger
after the tablet closes the app), so we may ask before the tablet listens.
That is fine: wpa_supplicant keeps repeating the GO negotiation request for
the whole activation (~45 s) and the tablet answers as soon as Second Screen
is up. An attempt must never be cut short: if the tablet has just accepted,
it waits 30 s for us as group owner and then stops listening altogether.
The Intel AX211 firmware sometimes crashes while joining the tablet's group
(right after the PC changed Wi-Fi network): the tablet then sits in that
half-made group for a minute or more, deaf to new attempts, until Second
Screen is closed and reopened on it.
"""
import fcntl
import ipaddress
import socket
import struct
import time

from gi.repository import GLib, NM

from . import log

# WFD IE subelement 0 (device info): source, session available, RTSP 7236.
WFD_IES = b"\x00\x00\x06\x00\x90\x1c\x44\x00\xc8"
ATTEMPT_DEADLINE = 55  # seconds; only if NetworkManager never fails the attempt
# If Second Screen was still starting, the OS accepts the group but the app
# never dials our RTSP port; drop such a group and try again.
RTSP_DEADLINE = 8
MAX_FIND = 600  # seconds, NetworkManager's limit; a find ends after 30 s unless told
GIVE_UP_RECHECK = 5  # seconds, while an attempt runs past the time to give up

_client = None


def nm_client():
    """One NetworkManager client for the process (made on the main thread,
    which the links use): one per Connect fetched all of NetworkManager's
    objects again, on the main loop, and kept another copy in step."""
    global _client
    if _client is None:
        _client = NM.Client.new(None)
    return _client


def _path(obj):
    """D-Bus object path of a libnm object. NM.Device.get_path() is something
    else: the udev path, None for every virtual device (docker, VPN...)."""
    return NM.Object.get_path(obj)


SIOCGIFADDR, SIOCGIFNETMASK = 0x8915, 0x891B


def interface_network(name):
    """The IPv4 network of interface name, as the kernel has it now; None
    while it has no address (DHCP not done yet) or is gone."""
    request = struct.pack("256s", name.encode()[:15])
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            address = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, request)[20:24])
            mask = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFNETMASK, request)[20:24])
    except OSError:
        return None
    return ipaddress.ip_network(f"{address}/{mask}", strict=False)


def local_addresses():
    """This PC's IPv4 addresses, but on Wi-Fi Direct groups' interfaces (p2p-*)."""
    found = set()
    for _index, name in socket.if_nameindex():
        if name.startswith("p2p-"):
            continue
        network = interface_network(name)
        if network is not None:
            request = struct.pack("256s", name.encode()[:15])
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    found.add(socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, request)[20:24]))
            except OSError:
                pass
    return found


def is_wfd_sink(peer):
    ies = peer.get_wfd_ies()
    data = ies.get_data() if ies else b""
    # Subelement 0, length 6: device type in the low two bits (1 = primary sink).
    return len(data) >= 5 and data[0] == 0 and (data[4] & 0x3) in (1, 3)


class P2PLink:
    def __init__(self, match=None, on_found=None, on_hint=None, give_up_after=None, on_give_up=None):
        """match: peer MACs or names (a list), or one MAC or substring of a name;
        None picks the first WFD sink.

        on_found(name) fires when the tablet shows up; on_hint() after repeated
        failed attempts or a Wi-Fi driver reset, to ask the user to reopen
        Second Screen. Attempts go
        on until stop(), or until give_up_after seconds have passed without a
        connection: then on_give_up() fires. Searching keeps the Wi-Fi radio
        busy (other traffic sees ~100 ms spikes), so it should not run forever.
        """
        self.on_found = on_found or (lambda name: None)
        self.on_hint = on_hint or (lambda: None)
        self.on_give_up = on_give_up or (lambda: None)
        self.give_up_after = give_up_after
        self.stopped = False
        self._handler = None
        self._client_handlers = []
        self._give_up_source = None
        self._give_up_at = None
        self._polling = False
        self._reset = False  # the Wi-Fi device was recreated while we looked
        self.client = nm_client()
        self.device = next(d for d in self.client.get_devices() if isinstance(d, NM.DeviceWifiP2P))
        if isinstance(match, str):
            match = [match]
        # A list (the tablet in use's address and name): exact. A string
        # (--peer): an address, or part of a name.
        self.exact = not isinstance(match, str)
        if isinstance(match, str):
            match = [match]
        self.match = [m.lower() for m in match] if match else None
        self.peer = None
        # The group's network interface once it is up: the tablet's RTSP
        # connection must come from its network (read from other threads).
        self.iface = None
        self.ac = None
        self.attempt = 0
        self.connected = False

    def _choose(self, peers):
        """The peer to connect to: by its address first (learned, the
        tablet's own), then by its exact name and only a Miracast sink (two
        tablets of a model are both "Galaxy Tab S10 FE+"); None if not there."""
        if not self.match:
            return next((p for p in peers if is_wfd_sink(p)), None)

        def mac(p):
            return (p.get_hw_address() or "").lower()

        def name(p):
            return (p.get_name() or "").lower()
        chosen = next((p for p in peers if mac(p) in self.match), None)
        if chosen is None and self.exact:
            chosen = next((p for p in peers if name(p) in self.match and is_wfd_sink(p)), None)
        elif chosen is None:
            chosen = next((p for p in peers if any(m and m in name(p) for m in self.match)), None)
        return chosen

    def network(self):
        """The group's IPv4 network, None until the group is up with an address."""
        iface = self.iface
        return interface_network(iface) if iface else None

    def owns(self, address):
        """Whether address is on the group's network: the tablet's, not the LAN's."""
        network = self.network()
        try:
            return network is not None and ipaddress.ip_address(address) in network
        except ValueError:
            return False

    def start(self):
        # An Intel firmware crash makes NetworkManager drop the P2P device and
        # create a new one a second later; follow it instead of hanging.
        self._client_handlers = [self.client.connect("device-removed", self._device_removed),
                                 self.client.connect("device-added", self._device_added)]
        self._arm_give_up()
        self._watch(self.device)

    def _arm_give_up(self):
        if self._give_up_source:
            GLib.source_remove(self._give_up_source)
        self._give_up_source = self._give_up_at = None
        if self.give_up_after:
            self._give_up_at = time.monotonic() + self.give_up_after
            self._give_up_source = GLib.timeout_add_seconds(self.give_up_after, self._give_up)

    def _overdue(self):
        return self._give_up_at is not None and time.monotonic() >= self._give_up_at

    def _give_up(self):
        if self.stopped or self.connected:
            self._give_up_source = None
            return False
        if self.device is not None and self.device.get_state() in (
                NM.DeviceState.PREPARE, NM.DeviceState.CONFIG, NM.DeviceState.NEED_AUTH,
                NM.DeviceState.IP_CONFIG, NM.DeviceState.IP_CHECK, NM.DeviceState.ACTIVATED):
            # Never cut an attempt short: it ends within a minute, and no new
            # one starts once it is time to give up (see _retry). Look again
            # soon, not a whole window later.
            self._give_up_source = GLib.timeout_add_seconds(GIVE_UP_RECHECK, self._give_up)
            return False
        self._give_up_source = None
        self._stop_trying()
        return False

    def _stop_trying(self):
        if self._give_up_source:
            GLib.source_remove(self._give_up_source)
            self._give_up_source = None
        log("Wi-Fi Direct: no tablet, giving up")
        self.on_give_up()

    def _watch(self, device):
        self.device = device
        self._handler = device.connect("state-changed", self._state_changed)
        self._search()

    def _find(self):
        # As long as we wait for the tablet: Second Screen may be opened at
        # any time, and stop() ends the find.
        timeout = min(MAX_FIND, self.give_up_after or MAX_FIND)
        self.device.start_find(GLib.Variant("a{sv}", {"timeout": GLib.Variant("i", timeout)}),
                               None, self._find_started, None)

    def _find_started(self, device, result, data):
        try:
            device.start_find_finish(result)
        except GLib.Error as e:
            # The tablet is then never found: at least the log says why
            # (Wi-Fi off, the device busy, NetworkManager refusing).
            log("Wi-Fi Direct: could not look for the tablet:", e.message)

    def _search(self):
        """Look for the tablet (again)."""
        self.peer = None
        self._find()
        if not self._polling:
            self._polling = True
            GLib.timeout_add(500, self._poll_peers)

    def _device_removed(self, client, device):
        if self.device is not None and _path(device) == _path(self.device):
            log("Wi-Fi Direct: the Wi-Fi device went away (driver reset?), waiting for it")
            self.device.handler_disconnect(self._handler)
            self._handler = None
            self.device = self.peer = self.ac = None

    def _device_added(self, client, device):
        if self.device is None and not self.stopped and isinstance(device, NM.DeviceWifiP2P):
            log("Wi-Fi Direct: the Wi-Fi device is back, looking for the tablet again")
            # The tablet may be stuck in the group we were joining: the user
            # has to reopen Second Screen, so give them the whole time again.
            self._reset = True
            self._arm_give_up()
            self._watch(device)

    def _poll_peers(self):
        if self.peer or self.stopped or self.device is None:
            self._polling = False
            return False
        peer = self._choose(list(self.device.get_peers()))
        if peer is None:
            return True
        self.peer = peer
        self._polling = False
        log(f"found {peer.get_name()} ({peer.get_hw_address()})")
        self.on_found(peer.get_name())
        if self._reset:
            self._reset = False
            self.on_hint()
        GLib.timeout_add_seconds(1, self._activate)
        return False

    def _activate(self):
        if self.stopped or self.connected or self.device is None or self.peer is None:
            return False
        if self._overdue():
            # Attempts follow each other while the tablet stays listed: the
            # last one may end, but no new one starts after the time is up.
            self._stop_trying()
            return False
        if _path(self.peer) not in {_path(p) for p in self.device.get_peers()}:
            # Lost while the last attempt ran: activating it would only fail.
            log("Wi-Fi Direct: the tablet is no longer in sight, looking for it again")
            self._search()
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
        if attempt != self.attempt or self.connected or self.stopped or self.device is None:
            return False
        if self.device.get_state() in (NM.DeviceState.PREPARE, NM.DeviceState.CONFIG,
                                       NM.DeviceState.NEED_AUTH):
            log(f"Wi-Fi Direct: attempt {attempt} still pending, trying again")
            self.device.disconnect_async(None, lambda *a: self._retry())
        return False

    def _rtsp_deadline(self, attempt):
        if attempt == self.attempt and not self.connected and not self.stopped and self.device:
            log("Wi-Fi Direct: group up but Second Screen did not start a session, retrying")
            self.device.disconnect_async(None, lambda *a: self._retry())
        return False

    def _retry(self):
        if self.stopped or self.connected:
            return
        if self.attempt >= 2:
            self.on_hint()
        GLib.timeout_add_seconds(1, self._activate)

    def _activated(self, client, res):
        try:
            self.ac, _ = client.add_and_activate_connection2_finish(res)
            if self.stopped:  # stop() came first: no group after Disconnect
                client.deactivate_connection_async(self.ac, None, None)
                self.ac = None
        except GLib.Error as e:
            log("Wi-Fi Direct activation failed:", e.message)
            if not self.stopped and not self.connected and self.device is not None:
                self._search()  # most likely the peer went away meanwhile

    def _state_changed(self, dev, new, old, reason):
        if new == NM.DeviceState.DISCONNECTED and self.peer is None and not self.stopped:
            # A recreated device becomes usable here: search again.
            self._find()
        elif new == NM.DeviceState.ACTIVATED:
            # The group's interface (p2p-wlan0-0, say), not the P2P device's.
            self.iface = dev.get_ip_iface() or dev.get_iface()
            log("Wi-Fi Direct group up on", self.iface, self.network() or "(no address yet)")
            GLib.timeout_add_seconds(RTSP_DEADLINE, self._rtsp_deadline, self.attempt)
        elif new == NM.DeviceState.FAILED and not self.connected and self.peer and not self.stopped:
            log(f"Wi-Fi Direct attempt {self.attempt} failed (reason {reason})")
            self._retry()

    def stop(self):
        self.stopped = True
        self.iface = None
        if self._give_up_source:
            GLib.source_remove(self._give_up_source)
            self._give_up_source = None
        for handler in self._client_handlers:
            self.client.disconnect(handler)
        self._client_handlers = []
        if self.device is not None:
            if self._handler is not None:
                self.device.handler_disconnect(self._handler)
                self._handler = None
            self.device.stop_find(None, None, None)
        if self.ac is not None:
            self.client.deactivate_connection_async(self.ac, None, None)
            self.ac = None
