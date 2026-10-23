#!/usr/bin/env python3
"""Ask a Wi-Fi Display sink for every parameter we know of and print its answers.

Open Second Screen on the tablet, then from the checkout:
    PYTHONPATH=. python3 tools/re/wfd_probe.py [peer MAC or name]
Useful to see what another tablet model supports (modes, EDID, UIBC...).
"""
import socket
import sys
import threading

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib  # noqa: E402

from plusone.p2p import P2PLink  # noqa: E402
from plusone.rtsp import SERVER, Rtsp  # noqa: E402

PARAMS = [
    "wfd_video_formats", "wfd_audio_codecs", "wfd_client_rtp_ports", "wfd_display_edid",
    "wfd_connector_type", "wfd_uibc_capability", "wfd_standby_resume_capability",
    "wfd_idr_request_capability", "wfd_coupled_sink", "wfd_I2C",
    "wfd2_video_formats", "wfd2_audio_codecs", "wfd2_transport_switch", "wfd2_sec_version",
    "microsoft_cursor", "microsoft_rtcp_capability", "microsoft_latency_management_capability",
    "microsoft_format_change_capability", "microsoft_diagnostics_capability",
    "intel_friendly_name", "intel_sink_manufacturer_name", "intel_sink_model_name",
    "wfd_sec_low_latency_mode", "wfd_sec_sink_resolution", "wfd_sec_tv_panel_resolution",
    "wfd_sec_framerate", "wfd_sec_hw_rotation", "wfd_sec_portrait_display",
    "wfd_sec_tcptransport", "wfd_sec_rtp_transport", "wfd_sec_max_pes_size",
]


def probe(conn):
    """M1, answer M2 as a Windows source (so the sink shares its EDID), then M3."""
    rtsp = Rtsp(conn)
    rtsp.request("OPTIONS", "*", extra={"Require": "org.wfa.wfd1.0"})
    m3 = None
    while True:
        first, headers, body = rtsp.read()
        if first.startswith("OPTIONS"):
            rtsp.send("RTSP/1.0 200 OK", {"CSeq": headers["cseq"], "Server": SERVER,
                                          "Public": "org.wfa.wfd1.0, SET_PARAMETER, GET_PARAMETER"})
            m3 = rtsp.request("GET_PARAMETER", "rtsp://localhost/wfd1.0", "\r\n".join(PARAMS) + "\r\n")
        elif first.startswith("RTSP/1.0") and headers.get("cseq") == str(m3):
            return body


def main():
    loop = GLib.MainLoop()
    link = P2PLink(sys.argv[1] if len(sys.argv) > 1 else None)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 7236))
    srv.listen(1)
    srv.settimeout(120)

    def serve():
        try:
            conn, _ = srv.accept()
            link.connected = True
            print(probe(conn))
            conn.close()
        except (OSError, EOFError) as e:
            print("probe failed:", e)
        GLib.idle_add(lambda: link.stop(loop.quit) or False)

    threading.Thread(target=serve, daemon=True).start()
    link.start()
    loop.run()
    srv.close()


if __name__ == "__main__":
    main()
