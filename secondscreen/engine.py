"""Connection lifecycle shared by the command line and the GUI.

Runs on the GLib main loop of whoever owns it (GLib.MainLoop for the CLI,
the GTK application for the GUI). Blocking work (adb, the RTSP session)
happens on helper threads and reports back through GLib.idle_add.
"""
import socket
import subprocess
import threading
from dataclasses import dataclass

from gi.repository import GLib

from . import log, media, pen, tablet
from .display import VirtualMonitor, virtual_monitor_edid
from .p2p import P2PLink
from .rtsp import MODES, WfdSession
from .uibc import UibcServer

RTSP_PORT = 7236
UIBC_PORT = 50000


@dataclass
class Settings:
    width: int = 1920
    height: int = 1200
    fps: int = 60
    bitrate: int = 20000  # kbit/s
    encoder: str = "auto"
    use_adb: bool = False
    touch: bool = True
    test_pattern: bool = False
    peer: str | None = None
    stay_ready: bool = True  # go back to waiting after the tablet disconnects


class State:
    IDLE = "idle"
    WAITING = "waiting"        # looking for the tablet
    CONNECTING = "connecting"  # Wi-Fi Direct / RTSP in progress
    STREAMING = "streaming"
    ERROR = "error"


def conflicting_apps():
    """Programs known to fight over the Wi-Fi Direct device."""
    out = subprocess.run(["pgrep", "-x", "gnome-network-d"], capture_output=True, text=True).stdout
    return ["GNOME Network Displays"] if out.strip() else []


class Controller:
    def __init__(self, settings, on_state=None):
        self.settings = settings
        self.on_state = on_state or (lambda state, detail: None)
        self.state = State.IDLE
        self.detail = ""
        self.link = None
        self.session = None
        self.streamer = None
        self.monitor = None
        self.uibc = None
        self.srv = None
        self.encoder = None
        self.serial = None
        self._running = False

    # -- state ---------------------------------------------------------------
    def _set_state(self, state, detail=""):
        self.state, self.detail = state, detail
        log(f"[{state}] {detail}")
        self.on_state(state, detail)

    def _from_thread(self, fn, *args):
        GLib.idle_add(lambda: fn(*args) and False)

    # -- public --------------------------------------------------------------
    def start(self):
        if self._running:
            return
        s = self.settings
        if (s.width, s.height, s.fps) not in MODES:
            self._set_state(State.ERROR, f"modalità {s.width}x{s.height}@{s.fps} non supportata")
            return
        others = conflicting_apps()
        if others:
            self._set_state(State.ERROR, f"chiudi {', '.join(others)}: usa anche lui il Wi-Fi Direct")
            return
        try:
            self.encoder = media.pick_encoder(s.encoder)
            self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.srv.bind(("0.0.0.0", RTSP_PORT))
            self.srv.listen(1)
        except (OSError, RuntimeError) as e:
            self._set_state(State.ERROR, str(e))
            return
        self._running = True
        if not s.test_pattern:
            self.monitor = VirtualMonitor(on_ready=self._start_stream)
            if s.touch:
                try:
                    self.uibc = UibcServer(self.monitor, UIBC_PORT, pen_factory=self._make_pen)
                except OSError as e:
                    log("touch disabled:", e)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        if s.use_adb:
            self._set_state(State.WAITING, "apro Second Screen sul tablet…")
            threading.Thread(target=self._adb_open, daemon=True).start()
        else:
            self._wait_for_tablet()

    def stop(self):
        if not self._running:
            return
        self._running = False
        self._end_session()
        if self.uibc:
            self.uibc.close()
            self.uibc = None
        if self.srv:
            try:
                self.srv.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.srv.close()
            self.srv = None
        self._set_state(State.IDLE)

    # -- internals -----------------------------------------------------------
    def _adb_open(self):
        serial = tablet.device()
        if serial:
            self.serial = serial
            tablet.prepare(serial, restart=False)
        else:
            log("adb: nessun tablet collegato, apri Second Screen a mano")
        self._from_thread(self._wait_for_tablet)

    def _wait_for_tablet(self):
        if not self._running:
            return
        self._set_state(State.WAITING, "tocca «Second screen» sul tablet")
        self.link = P2PLink(self.settings.peer, on_found=self._on_found,
                            on_failed=self._on_link_failed, on_hint=self._on_link_hint)
        self.link.start()

    def _on_found(self, name):
        self._set_state(State.CONNECTING, f"collegamento a {name}…")

    def _on_link_hint(self):
        self._set_state(State.CONNECTING, "il tablet non risponde: chiudi e riapri Second Screen")

    def _on_link_failed(self):
        self._set_state(State.ERROR, "il tablet non risponde: riapri Second Screen")
        if self.link:
            self.link.stop()
            self.link = None
        GLib.timeout_add_seconds(5, lambda: self._wait_for_tablet() and False)

    def _accept_loop(self):
        while self._running:
            try:
                conn, addr = self.srv.accept()
            except OSError:
                return
            if self.link:
                self.link.connected = True
            log("sink connected from", addr[0])
            s = self.settings
            self.session = WfdSession(conn, self._on_play, self._on_idr,
                                      on_close=lambda: self._from_thread(self._on_session_closed),
                                      mode=(s.width, s.height, s.fps),
                                      uibc_port=UIBC_PORT if self.uibc else None)
            self.session.run()

    def _make_pen(self, descriptor):
        # Called on the UIBC thread once the tablet describes its pen.
        if pen.uhid_available():
            device = pen.UhidPen(descriptor)
            edid = virtual_monitor_edid()
            if edid:
                pen.map_to_monitor(edid)
            return device
        return pen.PointerPen(self.monitor, (self.settings.width, self.settings.height))

    def _on_play(self, session):
        def go():
            if self.monitor:
                self.monitor.start()
            else:
                self._start_stream("test")
        self._from_thread(go)

    def _start_stream(self, source):
        s = self.settings
        self.streamer = media.Streamer(source, self.session.sink_ip, s.width, s.height,
                                       s.fps, s.bitrate, self.encoder)
        self.streamer.start()
        name = self.link.peer.get_name() if self.link and self.link.peer else "tablet"
        self._set_state(State.STREAMING, f"{name} · {s.width}×{s.height} @ {s.fps} Hz")

    def _on_idr(self):
        if self.streamer:
            self.streamer.force_keyframe()

    def _end_session(self):
        if self.session:
            self.session.stop()
            self.session = None
        if self.streamer:
            self.streamer.stop()
            self.streamer = None
        if self.monitor:
            self.monitor.stop()
        if self.link:
            self.link.stop()
            self.link = None

    def _on_session_closed(self):
        log("tablet disconnected")
        self._end_session()
        if self._running and self.settings.stay_ready:
            # Give the tablet a moment to tear its group down before searching again.
            GLib.timeout_add_seconds(3, lambda: self._wait_for_tablet() and False)
        elif self._running:
            self.stop()
