#!/usr/bin/env python3
"""Measure Wi-Fi Direct connection reliability against Second Screen.

Each trial restarts Second Screen over adb, forms the P2P group with
P2PLink and waits for the sink's RTSP connection; prints attempts and time.
"""
import socket
import sys
import threading
import time

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib  # noqa: E402

from plusone import log, tablet  # noqa: E402
from plusone.p2p import P2PLink  # noqa: E402

trials = int(sys.argv[1]) if len(sys.argv) > 1 else 5
RESTART = "--restart" in sys.argv
timeout = 75
serial = tablet.device()
results = []

for trial in range(1, trials + 1):
    tablet.prepare(serial, restart=RESTART)
    loop = GLib.MainLoop()
    link = P2PLink()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", 7236))
    srv.listen(1)
    srv.settimeout(timeout)
    outcome = {}
    t0 = time.time()

    def serve():
        try:
            conn, _ = srv.accept()
            outcome["t"] = time.time() - t0
            link.connected = True
            conn.close()
        except socket.timeout:
            pass
        GLib.idle_add(lambda: link.stop(loop.quit) or False)

    threading.Thread(target=serve, daemon=True).start()
    link.start()
    GLib.timeout_add_seconds(timeout + 10, loop.quit)
    loop.run()
    srv.close()
    ok = "t" in outcome
    results.append((ok, link.attempt, outcome.get("t")))
    log(f"TRIAL {trial}: {'OK' if ok else 'FAIL'} attempts={link.attempt} "
        f"time={outcome.get('t', 0):.1f}s")
    time.sleep(6)

okc = sum(1 for r in results if r[0])
first = sum(1 for r in results if r[0] and r[1] == 1)
log(f"SUMMARY: {okc}/{trials} connected, {first}/{trials} on first attempt, "
    f"times={[round(r[2], 1) for r in results if r[0]]}")
