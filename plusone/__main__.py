"""plus-one: extend the GNOME desktop onto a Galaxy Tab running Second Screen."""
import argparse
import signal
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("NM", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from . import media, once  # noqa: E402
from .engine import Controller, Settings, State  # noqa: E402
from .rtsp import MODES  # noqa: E402


def parse_size(text):
    if text == "auto":
        return None
    w, _, h = text.partition("x")
    return int(w), int(h)


def main():
    if len(sys.argv) == 1 or sys.argv[1:] == ["--gui"]:
        from .gui import main as gui_main
        return gui_main()

    ap = argparse.ArgumentParser(prog="plus-one", description=__doc__)
    ap.add_argument("--cli", action="store_true", help="run without the window")
    ap.add_argument("--peer", help="tablet Wi-Fi Direct MAC or name (default: first WFD sink)")
    ap.add_argument("--size", type=parse_size, default=None,
                    help="WIDTHxHEIGHT of the virtual monitor (default: from the tablet's EDID)")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    ap.add_argument("--encoder", default="auto", choices=["auto", *media.ENCODERS])
    ap.add_argument("--test", action="store_true", help="stream a test pattern, no virtual monitor")
    ap.add_argument("--no-adb", action="store_true", help="do not drive the tablet over adb")
    ap.add_argument("--no-input", action="store_true", help="disable touch input (UIBC)")
    ap.add_argument("--once", action="store_true", help="exit when the tablet disconnects")
    args = ap.parse_args()
    if args.size and (*args.size, args.fps) not in MODES:
        ap.error("supported modes: " + ", ".join(f"{w}x{h}@{f}" for w, h, f in MODES))

    Gst.init(None)
    loop = GLib.MainLoop()
    width, height = args.size or (0, 0)
    settings = Settings(auto_mode=args.size is None, width=width, height=height, fps=args.fps,
                        bitrate=args.bitrate, encoder=args.encoder, use_adb=not args.no_adb,
                        touch=not args.no_input, test_pattern=args.test, peer=args.peer,
                        stay_ready=not args.once)

    def on_state(state, template, args):
        if state == State.IDLE or (state == State.ERROR and not controller.running):
            loop.quit()

    controller = Controller(settings, on_state)

    def shutdown(*_):
        controller.stop()
        GLib.timeout_add_seconds(2, loop.quit)
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    GLib.idle_add(once(controller.start))
    loop.run()


if __name__ == "__main__":
    main()
