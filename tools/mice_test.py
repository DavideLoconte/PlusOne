#!/usr/bin/env python3
"""Start a Second Screen session over MS-MICE + adb instead of Wi-Fi Direct.

Needs Second Screen's MICE listener up (tablet USB functions include mtp and
the receiver saw {"USB":1}); see docs/FINDINGS.md.
"""
import socket
import struct
import subprocess
import sys
import threading
import uuid

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from secondscreen import log, media  # noqa: E402
from secondscreen.rtsp import WfdSession  # noqa: E402

serial, tablet_ip = sys.argv[1], sys.argv[2]
seconds = int(sys.argv[3]) if len(sys.argv) > 3 else 60


def adb(*args):
    return subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True)


def source_ready(name, rtsp_port):
    tlv = b""
    fname = name.encode("utf-16-le")
    tlv += struct.pack(">BH", 0, len(fname)) + fname
    tlv += struct.pack(">BHH", 2, 2, rtsp_port)
    tlv += struct.pack(">BH", 3, 16) + uuid.uuid4().bytes
    body = bytes([1, 1]) + tlv  # version 1, command SOURCE_READY
    return struct.pack(">H", len(body) + 2) + body


Gst.init(None)
loop = GLib.MainLoop()
print(adb("forward", "tcp:7250", "tcp:7250").stdout, adb("reverse", "tcp:7236", "tcp:7236").stdout)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 7236))
srv.listen(1)
st = {}


def serve():
    conn, addr = srv.accept()
    log("sink connected via adb reverse from", addr)

    def on_play(session):
        st["streamer"] = media.Streamer("test", tablet_ip, 1920, 1200, 60, 15000, "nvenc")
        st["streamer"].start()

    sess = WfdSession(conn, on_play, lambda: st.get("streamer") and st["streamer"].force_keyframe(),
                      lambda: GLib.idle_add(loop.quit), sink_ip=tablet_ip)
    st["session"] = sess
    threading.Timer(seconds, sess.stop).start()
    sess.run()


threading.Thread(target=serve, daemon=True).start()
mice = socket.create_connection(("127.0.0.1", 7250))
mice.sendall(source_ready("Linux PC", 7236))
log("SOURCE_READY sent")
GLib.timeout_add_seconds(seconds + 30, loop.quit)
loop.run()
if st.get("streamer"):
    st["streamer"].stop()
mice.close()
