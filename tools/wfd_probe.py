#!/usr/bin/env python3
"""Probe a Samsung Second Screen WFD sink for its extended RTSP capabilities.

Forms a Wi-Fi Direct group through libnm the same way gnome-network-displays
does (peer passed as the activation's specific object), waits for the sink to
dial our RTSP port, runs M1/M2 and an extended M3 GET_PARAMETER, logs the
answers verbatim, then tears everything down.
"""
import socket
import sys
import threading

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM  # noqa: E402

PEER_MAC = sys.argv[1] if len(sys.argv) > 1 else "56:DD:4F:07:78:78"
LOG = sys.argv[2] if len(sys.argv) > 2 else "wfd_probe.log"
RTSP_PORT = 7236
# Same static WFD IEs as gnome-network-displays: source, RTSP port 7236.
WFD_IES = b"\x00\x00\x06\x00\x90\x1c\x44\x00\xc8"

PARAMS = [
    "wfd_video_formats", "wfd_audio_codecs", "wfd_client_rtp_ports",
    "wfd_display_edid", "wfd_connector_type", "wfd_uibc_capability",
    "wfd_standby_resume_capability", "wfd_idr_request_capability",
    "wfd_coupled_sink", "wfd_I2C",
    "wfd2_video_formats", "wfd2_audio_codecs", "wfd2_transport_switch",
    "wfd2_sec_version",
    "microsoft_cursor", "microsoft_rtcp_capability",
    "microsoft_latency_management_capability",
    "microsoft_format_change_capability", "microsoft_diagnostics_capability",
    "intel_friendly_name", "intel_sink_manufacturer_name", "intel_sink_model_name",
    "wfd_sec_low_latency_mode", "wfd_sec_sink_resolution",
    "wfd_sec_tv_panel_resolution", "wfd_sec_framerate", "wfd_sec_hw_rotation",
    "wfd_sec_sink_hw_rotation", "wfd_sec_portrait_display", "wfd_sec_rotation",
    "wfd_sec_tcptransport", "wfd_sec_rtp_transport", "wfd_sec_max_pes_size",
    "wfd_sec_screensharing", "wfd_sec_mobile_state", "wfd_sec_volume_support",
    "wfd_sec_wcg_support", "wfd_sec_dmr_support", "wfd_sec_tv_init_mirroring_support",
    "wfd_sec_rendering_status", "wfd_sec_nit",
]

log_file = open(LOG, "w")


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    log_file.write(line + "\n")
    log_file.flush()


class Rtsp:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.cseq = 0

    def _fill(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise EOFError("sink closed the connection")
        self.buf += chunk

    def read(self):
        while b"\r\n\r\n" not in self.buf:
            self._fill()
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0"))
        while len(self.buf) < length:
            self._fill()
        body, self.buf = self.buf[:length], self.buf[length:]
        log("<==", lines[0], headers, repr(body.decode("latin1")))
        return lines[0], headers, body.decode("latin1")

    def send(self, first, headers, body=""):
        msg = first + "\r\n"
        for k, v in headers.items():
            msg += f"{k}: {v}\r\n"
        if body:
            msg += "Content-Type: text/parameters\r\n"
            msg += f"Content-Length: {len(body)}\r\n"
        msg += "\r\n" + body
        log("==>", repr(msg))
        self.sock.sendall(msg.encode("latin1"))

    def answer(self, first, headers):
        reply = {"CSeq": headers.get("cseq", "0")}
        if first.startswith("OPTIONS"):
            reply["Public"] = "org.wfa.wfd1.0, SET_PARAMETER, GET_PARAMETER"
        self.send("RTSP/1.0 200 OK", reply)

    def request(self, method, url, body=""):
        self.cseq += 1
        self.send(f"{method} {url} RTSP/1.0", {"CSeq": self.cseq}, body)
        while True:
            first, headers, rbody = self.read()
            if first.startswith("RTSP/1.0"):
                return first, rbody
            self.answer(first, headers)


def probe(conn):
    rtsp = Rtsp(conn)
    conn.settimeout(10)
    rtsp.request("OPTIONS", "*")
    conn.settimeout(2)
    try:
        first, headers, _ = rtsp.read()
        rtsp.answer(first, headers)
    except socket.timeout:
        pass
    conn.settimeout(10)

    status, body = rtsp.request("GET_PARAMETER", "rtsp://localhost/wfd1.0",
                                "\r\n".join(PARAMS) + "\r\n")
    log("M3 bulk status:", status)
    answered = {line.split(":", 1)[0].strip() for line in body.splitlines() if ":" in line}
    for p in PARAMS:
        if p in answered:
            continue
        s, b = rtsp.request("GET_PARAMETER", "rtsp://localhost/wfd1.0", p + "\r\n")
        log(f"single {p}: {s} | {b.strip()!r}")


def rtsp_server(srv, done):
    try:
        conn, addr = srv.accept()
        log("sink connected from", addr)
        try:
            probe(conn)
        except (EOFError, socket.timeout, ConnectionError) as e:
            log("probe ended:", e)
        conn.close()
    except socket.timeout:
        log("sink never connected")
    finally:
        GLib.idle_add(done)


def main():
    loop = GLib.MainLoop()
    client = NM.Client.new(None)
    device = next(d for d in client.get_devices() if isinstance(d, NM.DeviceWifiP2P))
    state = {"ac": None}

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", RTSP_PORT))
    srv.listen(1)
    srv.settimeout(120)

    def finish():
        if state["ac"] is not None:
            client.deactivate_connection_async(state["ac"], None, lambda *a: loop.quit())
            GLib.timeout_add_seconds(5, loop.quit)
        else:
            loop.quit()
        return False

    def activated(cl, res):
        try:
            state["ac"], _ = cl.add_and_activate_connection2_finish(res)
            log("activation started:", state["ac"].get_path())
        except GLib.Error as e:
            log("activation failed:", e.message)
            loop.quit()

    def connect(peer):
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
        client.add_and_activate_connection2(con, device, peer.get_path(), options,
                                            None, activated)
        threading.Thread(target=rtsp_server, args=(srv, finish), daemon=True).start()

    def poll_peers():
        for peer in device.get_peers():
            if peer.get_hw_address().lower() == PEER_MAC.lower():
                log("peer found:", peer.get_path(), peer.get_name())
                connect(peer)
                return False
        return True

    device.start_find(None, None, None, None)
    GLib.timeout_add(500, poll_peers)
    GLib.timeout_add_seconds(180, loop.quit)
    loop.run()
    srv.close()


if __name__ == "__main__":
    main()
