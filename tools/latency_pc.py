#!/usr/bin/env python3
"""PC-side latency: from a window's change to its frame complete on the wire.

A window on a Mutter virtual monitor changes colour every 0.5 s; Plus One's
Streamer (from the checkout given) captures, encodes and sends it to a
local UDP port, where each frame's PES is reassembled. Prints the time from
the window's commit (after-paint) to its frame complete, over CHANGES
changes. Needs a GNOME Wayland session and NVENC; a window shows briefly.

usage: tools/latency_pc.py CHECKOUT [CHANGES]    (e.g. tools/latency_pc.py . 20)"""
import socket
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, GLib, Gst, Gtk  # noqa: E402

sys.path.insert(0, sys.argv[1])
CHANGES = int(sys.argv[2]) if len(sys.argv) > 2 else 20
Gst.init(None)
from plusone import media  # noqa: E402
from plusone.display import VirtualMonitor  # noqa: E402

rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("127.0.0.1", 0))
rx.settimeout(0.2)
got, commits = [], []
running = True


def receive():
    while running:
        try:
            got.append((time.monotonic(), rx.recv(4096)))
        except socket.timeout:
            pass


threading.Thread(target=receive, daemon=True).start()
streamer = None


def on_ready(node):
    global streamer
    streamer = media.Streamer(node, "127.0.0.1", 1920, 1200, 60, 20000, "nvenc",
                              port=rx.getsockname()[1], bind_port=0)
    streamer.start()


monitor = VirtualMonitor(on_ready)
monitor.start()
app = Gtk.Application(application_id="io.github.test.E2EPC")


def activate(app):
    app.hold()

    def place():
        monitors = Gdk.Display.get_default().get_monitors()
        tablet = None
        for i in range(monitors.get_n_items()):
            m = monitors.get_item(i)
            if (m.get_connector() or "").startswith("Meta-"):
                tablet = m
        if tablet is None or streamer is None:
            return True
        win = Gtk.Window(application=app)
        area = Gtk.DrawingArea()
        state = {"n": 0, "pending": False}

        def draw(a, cr, w, h):
            v = state["n"] % 2
            cr.set_source_rgb(v, 1 - v, 0.3)
            cr.paint()
        area.set_draw_func(draw)
        win.set_child(area)
        win.fullscreen_on_monitor(tablet)
        win.present()

        def after_paint(clock):
            if state["pending"]:
                state["pending"] = False
                commits.append(time.monotonic())

        def change():
            if state["n"] == 0:
                win.get_frame_clock().connect("after-paint", after_paint)
            if state["n"] >= CHANGES:
                app.quit()
                return False
            state["n"] += 1
            state["pending"] = True
            area.queue_draw()
            return True
        GLib.timeout_add(2000, lambda: GLib.timeout_add(500, change) and False)
        return False
    GLib.timeout_add(200, place)


app.connect("activate", activate)
app.run([])
running = False
streamer.stop()
monitor.stop()

video_pid, cur, done = None, None, []
for t, pkt in got:
    ts = pkt[12:]
    for i in range(0, len(ts) - 187, 188):
        p = ts[i:i + 188]
        pid = ((p[1] & 0x1f) << 8) | p[2]
        afc = (p[3] >> 4) & 3
        off = 4 + (1 + p[4] if afc & 2 else 0)
        pay = p[off:] if afc & 1 else b""
        if p[1] & 0x40 and pay[:3] == b"\x00\x00\x01" and pay[3] >= 0xe0:
            video_pid = pid
            cur = [int.from_bytes(pay[4:6], "big") + 6, 0, t]
        if pid == video_pid and cur:
            cur[1] += len(pay)
            if cur[1] >= cur[0]:
                done.append(t)
                cur = None
res = []
for c in commits:
    after = [d for d in done if d > c]
    if after:
        res.append((after[0] - c) * 1000)
res.sort()
print(f"{sys.argv[1]}: commit -> frame on the wire n={len(res)} p50 {res[len(res)//2]:.1f} "
      f"min {res[0]:.1f} max {res[-1]:.1f} mean {sum(res)/len(res):.1f} ms" if res else f"no data: packets {len(got)} commits {len(commits)} frames {len(done)}")
