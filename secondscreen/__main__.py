"""secondscreen: extend the GNOME desktop onto a Galaxy Tab running Second Screen."""
import argparse
import signal
import socket
import threading

import gi

gi.require_version("Gst", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from . import log, media, tablet  # noqa: E402
from .display import VirtualMonitor  # noqa: E402
from .p2p import P2PLink  # noqa: E402
from .rtsp import MODES, WfdSession  # noqa: E402
from .uibc import UibcServer  # noqa: E402

RTSP_PORT = 7236
UIBC_PORT = 50000


def parse_size(text):
    w, _, h = text.partition("x")
    return int(w), int(h)


def main():
    ap = argparse.ArgumentParser(prog="secondscreen", description=__doc__)
    ap.add_argument("--peer", help="tablet Wi-Fi Direct MAC or name (default: first WFD sink)")
    ap.add_argument("--size", type=parse_size, default=(1920, 1200), help="virtual monitor size")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    ap.add_argument("--encoder", default="auto", choices=["auto", *media.ENCODERS])
    ap.add_argument("--test", action="store_true", help="stream a test pattern, no virtual monitor")
    ap.add_argument("--no-adb", action="store_true", help="do not drive the tablet over adb")
    ap.add_argument("--no-input", action="store_true", help="disable touch input (UIBC)")
    args = ap.parse_args()
    if (*args.size, args.fps) not in MODES:
        ap.error("supported modes: " + ", ".join(f"{w}x{h}@{f}" for w, h, f in MODES))

    Gst.init(None)
    encoder = media.pick_encoder(args.encoder)
    loop = GLib.MainLoop()
    width, height = args.size
    st = {"session": None, "streamer": None, "monitor": None, "closing": False}

    serial = None if args.no_adb else tablet.device()
    if serial:
        tablet.prepare(serial)
    else:
        log("open Second Screen on the tablet (quick settings tile)")

    monitor = None if args.test else VirtualMonitor(on_ready=lambda node: start_stream(node))
    uibc = None
    if not args.no_input and monitor:
        uibc = UibcServer(monitor, UIBC_PORT)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", RTSP_PORT))
    srv.listen(1)
    link = P2PLink(args.peer)

    def start_stream(source):
        sink_ip = st["session"].sink_ip
        st["streamer"] = media.Streamer(source, sink_ip, width, height, args.fps,
                                        args.bitrate, encoder)
        st["streamer"].start()
        return False

    def on_play(session):
        log("sink is playing")
        if monitor:
            GLib.idle_add(lambda: monitor.start() and False)
        else:
            GLib.idle_add(start_stream, "test")

    def on_idr():
        if st["streamer"]:
            st["streamer"].force_keyframe()

    def shutdown(*_):
        if st["closing"]:
            return False
        st["closing"] = True
        log("shutting down")
        if st["session"]:
            st["session"].stop()
        if st["streamer"]:
            st["streamer"].stop()
        if monitor:
            monitor.stop()
        if uibc:
            uibc.close()
        srv.close()
        link.stop(loop.quit)
        GLib.timeout_add_seconds(5, loop.quit)
        return False

    def serve():
        try:
            conn, addr = srv.accept()
        except OSError:
            return
        link.connected = True
        log("sink connected from", addr[0])
        st["session"] = WfdSession(conn, on_play, on_idr,
                                   on_close=lambda: GLib.idle_add(shutdown),
                                   mode=(width, height, args.fps),
                                   uibc_port=UIBC_PORT if uibc else None)
        st["session"].run()

    threading.Thread(target=serve, daemon=True).start()
    link.start()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    loop.run()


if __name__ == "__main__":
    main()
