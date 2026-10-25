"""Connection lifecycle shared by the command line and the GUI.

Runs on the GLib main loop of whoever owns it (GLib.MainLoop for the CLI,
the GTK application for the GUI). Blocking work (adb, the RTSP session)
happens on helper threads and reports back through GLib.idle_add.
"""
import socket
import threading
import time
from dataclasses import dataclass

from gi.repository import Gio, GLib

from . import log, media, once, pen, tablet
from .display import VirtualMonitor
from .p2p import P2PLink, local_addresses
from .rtsp import MODES, WfdSession
from .tablets import Tablets
from .uibc import UibcServer

RTSP_PORT = 7236
UIBC_PORT = 50000
# Searching for the tablet keeps the Wi-Fi radio busy, so it is time-boxed:
CONNECT_WINDOW = 120    # after the user asks to connect
RECONNECT_WINDOW = 90   # after the tablet closes Second Screen, to catch a reopen
# Only the user can help then: the tablet is stuck in a half-made group.
NOT_ANSWERING = "The tablet is not answering: close and reopen Second Screen"


@dataclass
class Settings:
    auto_mode: bool = True  # take the resolution from the tablet's EDID
    width: int = 1920       # used when auto_mode is off
    height: int = 1200
    fps: int = 60
    bitrate: int = 20000  # kbit/s
    encoder: str = "auto"
    use_adb: bool = False
    touch: bool = True
    test_pattern: bool = False
    peer: str | None = None  # the command line's --peer
    reconnect: bool = True  # look for the tablet again for a while after it disconnects
    # The app (not the engine): keep running without a window and start at
    # login, so the Quick Settings toggle connects at once.
    background: bool = True
    language: str = "auto"
    # "Sign on Tablet" in Files and Open With.
    sign_on_tablet: bool = True
    # The paired tablet in use (its app's certificate fingerprint, tablets.py):
    # the only one that gets the desktop and the PDFs.
    tablet: str | None = None
    # Its camera and microphone as the PC's own (devices.py).
    camera: bool = False
    camera_facing: str = "front"   # or "back"
    camera_quality: str = "720p"   # or "1080p"
    microphone: bool = False
    # Its noise taken out: "off", "light" (the tablet's libwebrtc) or "neural"
    # (DeepFilterNet's plugin, on this PC: devices.py).
    microphone_noise: str = "neural"
    # The Quick Settings toggle offered once, after the first pairing (gui.py).
    toggle_offered: bool = False
    # The second screen through Plus One's app when it answers on the
    # network ("auto"), or always through Samsung's Second Screen.
    display_via: str = "auto"


class State:
    IDLE = "idle"
    WAITING = "waiting"        # looking for the tablet
    CONNECTING = "connecting"  # Wi-Fi Direct / RTSP in progress
    STREAMING = "streaming"
    ERROR = "error"


# Programs known to fight over the Wi-Fi Direct device, by D-Bus name: unlike
# the process list, the session bus is visible from inside a Flatpak sandbox.
CONFLICTING_APPS = {"org.gnome.NetworkDisplays": "GNOME Network Displays"}


def conflicting_apps():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    running = []
    for name, label in CONFLICTING_APPS.items():
        owned = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (name,)), None,
                              Gio.DBusCallFlags.NONE, -1, None).unpack()[0]
        if owned:
            running.append(label)
    return running


class Controller:
    def __init__(self, settings, on_state=None, tablets=None):
        self.settings = settings
        self.on_state = on_state or (lambda state, template, args: None)
        self.tablets = tablets or Tablets(settings)
        self.state = State.IDLE
        self.detail = ("", {})
        self.link = None
        self.session = None
        self.streamer = None
        self.monitor = None
        self.uibc = None
        self.srv = None
        self.encoder = None
        self.app_display = None  # appdisplay.AppDisplay, when the app shows the desktop
        self.mode = None  # (width, height, fps) of the current session
        self._running = False
        # Each start() a new one: a connection accepted before stop() (and
        # verified for seconds after it) starts no session afterwards.
        self._generation = 0
        self._lan = set()  # start(): local_addresses()
        self._reconnecting = False  # waiting for the tablet again after it closed Second Screen
        self._lock = threading.Lock()  # _running, _generation, the session's start
        # No session (or its teardown done): the next connection may start one.
        self._idle = threading.Event()
        self._idle.set()

    @property
    def running(self):
        return self._running

    # -- state ---------------------------------------------------------------
    def _set_state(self, state, template="", **args):
        """template is an English message (see i18n) formatted with args."""
        self.state, self.detail = state, (template, args)
        log(f"[{state}] {template.format(**args)}")
        self.on_state(state, template, args)

    def _from_thread(self, fn, *args):
        GLib.idle_add(once(fn, *args))

    # -- public --------------------------------------------------------------
    def start(self):
        if self._running:
            return
        s = self.settings
        if not s.auto_mode and (s.width, s.height, s.fps) not in MODES:
            self._set_state(State.ERROR, "Mode {mode} is not supported", mode=f"{s.width}x{s.height}@{s.fps}")
            return
        tablet = self.tablets.active()
        if tablet is None:
            self._set_state(State.ERROR, "Pair a tablet first")
            return
        targets = self.tablets.addresses(tablet)
        if targets and s.display_via == "auto" and not s.test_pattern and not s.peer:
            self._start_app(tablet, targets)
        else:
            self._start_second_screen()

    def _start_second_screen(self):
        s = self.settings
        others = conflicting_apps()
        if others:
            self._set_state(State.ERROR, "Close {apps}: it also uses Wi-Fi Direct", apps=", ".join(others))
            return
        try:
            self.encoder = media.pick_encoder(s.encoder)
            # Meanwhile the tablet is found and connected, seconds at least.
            threading.Thread(target=media.warm_up, args=(self.encoder,), daemon=True).start()
            self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.srv.bind(("0.0.0.0", RTSP_PORT))
            self.srv.listen(1)
        except (OSError, RuntimeError, KeyError) as e:  # KeyError: an encoder not known
            self._set_state(State.ERROR, "{error}", error=str(e))
            return
        # This PC's addresses but Wi-Fi Direct's: a connection to them is the LAN's.
        self._lan = local_addresses()
        self._reconnecting = False  # the user asked for this connection
        with self._lock:
            self._running = True
            self._generation += 1
        if not s.test_pattern:
            self.monitor = VirtualMonitor(on_ready=self._start_stream)
        threading.Thread(target=self._accept_loop, args=(self.srv, self._generation), daemon=True).start()
        if s.use_adb:
            self._set_state(State.WAITING, "Opening Second Screen on the tablet…")
            threading.Thread(target=self._adb_open, daemon=True).start()
        else:
            self._wait_for_tablet(CONNECT_WINDOW)

    def stop(self, reason=""):
        """reason: English template shown with the idle state (see i18n)."""
        if not self._running:
            return
        with self._lock:
            self._running = False
            self._generation += 1
        self._end_app()
        self._end_session()
        if self.srv:
            try:
                self.srv.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.srv.close()
            self.srv = None
        self._set_state(State.IDLE, reason)

    # -- the desktop in Plus One's app (appdisplay.py) -----------------------------
    def _start_app(self, tablet, targets):
        """The app on the tablet shows the desktop: over the home Wi-Fi, no
        Second Screen. If it cannot (no app answering, refused, failed
        before showing anything), Second Screen as before."""
        try:
            from .appdisplay import AppDisplay
        except ImportError as e:  # python3-cryptography missing (from the sources)
            log("app display: not available:", e)
            self._start_second_screen()
            return
        s = self.settings
        try:
            self.encoder = media.pick_encoder(s.encoder)
        except (RuntimeError, KeyError) as e:
            self._set_state(State.ERROR, "{error}", error=str(e))
            return
        threading.Thread(target=media.warm_up, args=(self.encoder,), daemon=True).start()
        with self._lock:
            self._running = True
            self._generation += 1
        mode = (1920, 1200, 60) if s.auto_mode else (s.width, s.height, s.fps)
        self._set_state(State.CONNECTING, "Opening the desktop on {name}…", name=tablet.name)
        generation = self._generation

        def started(mode):
            if generation == self._generation:
                self.mode = mode
                self._set_state(State.STREAMING, "{name} · {width}×{height} @ {fps} Hz",
                                name=tablet.name, width=mode[0], height=mode[1], fps=mode[2])

        def paused(paused):
            if generation != self._generation or self.state != State.STREAMING:
                return
            if paused:
                self._set_state(State.STREAMING, "{name}: paused while Plus One is not on its screen",
                                name=tablet.name)
            else:
                started(self.mode)

        def ended(error, was_started):
            if generation != self._generation:
                return
            self.app_display = None
            self.mode = None
            if was_started:
                log("app display: the tablet ended it", repr(error) if error else "")
                self.stop("The tablet closed the desktop")
            else:
                log("app display: not through the app:", repr(error), "- Second Screen instead")
                with self._lock:
                    self._running = False
                self._start_second_screen()

        self.app_display = AppDisplay(targets, tablet.app_id, mode, s.bitrate, self.encoder, touch=s.touch,
                                      on_started=started, on_ended=ended, on_paused=paused)
        self.app_display.start()

    def _end_app(self):
        if self.app_display is not None:
            self.app_display.stop()
            self.app_display = None
            self.mode = None

    # -- internals -----------------------------------------------------------
    def _adb_open(self):
        serial = tablet.device()
        if serial:
            tablet.prepare(serial, restart=False)
        else:
            log("adb: no tablet attached, open Second Screen by hand")
        self._from_thread(self._wait_for_tablet, CONNECT_WINDOW)

    def _wait_for_tablet(self, window):
        if not self._running or self.link is not None or self.session is not None:
            return
        self._set_state(State.WAITING, "Tap “Second screen” on the tablet")
        try:
            match = self.settings.peer or self.tablets.match()
            self.link = P2PLink(match, on_found=self._on_found, on_hint=self._on_link_hint,
                                give_up_after=window, on_give_up=self._on_give_up)
        except StopIteration:
            self._fail("This PC's Wi-Fi cannot do Wi-Fi Direct")
            return
        except GLib.Error as e:
            self._fail("{error}", error=e.message)
            return
        self.link.start()

    def _fail(self, template, **args):
        """Stop, and say why (an English template, see i18n)."""
        log("failed:", template.format(**args))
        self.stop()
        self._set_state(State.ERROR, template, **args)

    def _on_give_up(self):
        if self._reconnecting:
            # The user closed Second Screen on the tablet: the wait for it to
            # be opened again ends quietly, not with a "did not answer".
            self.stop()
            return
        self.stop("The tablet did not answer: open Second Screen on it and connect again")

    def _on_found(self, name):
        self._set_state(State.CONNECTING, "Connecting to {name}…", name=name)

    def _on_link_hint(self):
        self._set_state(State.CONNECTING, NOT_ANSWERING)

    def _current(self, generation):
        return self._running and self._generation == generation

    def _accept_loop(self, srv, generation):
        while self._current(generation):
            try:
                conn, addr = srv.accept()
            except OSError:
                return
            try:
                self._serve(conn, addr, generation)
            except Exception as e:  # noqa: BLE001 - one bad connection must not end the listening
                log("RTSP connection dropped:", repr(e))
                conn.close()
            # The session's teardown runs on the main loop: the next
            # connection waits for it, so two sessions never overlap.
            self._idle.wait(10)

    def _from_tablet(self, address):
        """Whether address is the tablet's: on the Wi-Fi Direct group's
        network, which NetworkManager reports a moment after the group is up.
        Port 7236 listens on every interface; the LAN must not get the desktop."""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            link = self.link
            network = link.network() if link else None
            if network is not None:
                return link.owns(address)
            time.sleep(0.1)
        return False

    def _serve(self, conn, addr, generation):
        if not self._current(generation):  # stopped while the tablet dialled in
            conn.close()
            return
        try:
            name = conn.getsockname()
        except OSError:
            conn.close()
            return
        local = name[0] if isinstance(name, tuple) else ""  # "" for a test's Unix socket pair
        # Come in over an interface the PC had before Wi-Fi Direct (the LAN):
        # hung up on at once, not after waiting for the group's network.
        if local in self._lan or not self._from_tablet(addr[0]):
            log("RTSP: hung up on", addr[0], "(not on the Wi-Fi Direct group)")
            conn.close()
            return
        # Before anything of the desktop leaves: is it the tablet in use?
        problem = self.tablets.verify(addr[0])
        if problem:
            log("RTSP: hung up on", addr[0])
            conn.close()
            template, args = problem
            self._from_thread(lambda: self._fail(template, **args))
            return
        s = self.settings
        this = []  # the session, for on_close
        with self._lock:
            # Verifying took seconds: stopped meanwhile (or started again), no session.
            if not self._current(generation):
                conn.close()
                return
            self._idle.clear()
            try:
                if s.touch and self.monitor:
                    try:
                        self.uibc = UibcServer(self.monitor, UIBC_PORT, pen_factory=self._make_pen,
                                               peer=addr[0], address=local or "0.0.0.0")
                    except OSError as e:
                        log("touch disabled:", e)
                session = WfdSession(conn, self._on_play, self._on_idr,
                                     on_close=lambda: self._from_thread(self._on_session_closed, this[0]),
                                     mode=None if s.auto_mode else (s.width, s.height, s.fps),
                                     uibc_port=UIBC_PORT if self.uibc else None, sink_ip=addr[0])
            except Exception:
                # The tablet hung up meanwhile, say: nothing left half-started.
                if self.uibc:
                    self.uibc.close()
                    self.uibc = None
                self._idle.set()
                raise
            this.append(session)
            self.session = session
            if self.link:
                self.link.connected = True  # its give-up and retries end: the session runs
        log("sink connected from", addr[0])
        session.run()

    def _make_pen(self):
        # Called on the UIBC thread once the tablet says it has a pen.
        if self.mode is None:
            raise RuntimeError("no mode yet")
        return pen.for_monitor(self.monitor, self.mode[:2])

    def _on_play(self, session):
        # The session settled the mode in M3/M4 (from the EDID in auto mode).
        self.mode = session.mode

        def go():
            if not self._running:
                return
            try:
                if self.monitor:
                    self.monitor.start()
                else:
                    self._start_stream("test")
            except GLib.Error as e:
                self._fail("{error}", error=e.message)
        self._from_thread(go)

    def _start_stream(self, source):
        if not self._running or self.session is None or self.mode is None:
            return
        width, height, fps = self.mode
        try:
            self.streamer = media.Streamer(source, self.session.sink_ip, width, height, fps,
                                           self.settings.bitrate, self.encoder,
                                           on_error=lambda message: self._fail("{error}", error=message))
        except Exception as e:  # noqa: BLE001 - said to the user, not left half-started
            self._fail("{error}", error=getattr(e, "message", None) or str(e))
            return
        self.streamer.start()
        peer = self.link.peer if self.link else None
        name = peer.get_name() if peer else "tablet"
        if peer:
            # Proven the tablet in use (_serve): next time it is found by this.
            self.tablets.learned(peer.get_hw_address())
        self._set_state(State.STREAMING, "{name} · {width}×{height} @ {fps} Hz",
                        name=name, width=width, height=height, fps=fps)

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
        if self.uibc:
            self.uibc.close()
            self.uibc = None
        if self.monitor:
            self.monitor.stop()
        if self.link:
            self.link.stop()
            self.link = None
        self._idle.set()

    def _on_session_closed(self, session):
        if session is not self.session:
            return  # ended by stop(), or a newer session runs already
        log("tablet disconnected")
        self._end_session()
        if self._running and self.settings.reconnect:
            # Reopening Second Screen soon reconnects. Give the tablet a moment
            # to tear its group down before searching again.
            self._set_state(State.WAITING, "Tap “Second screen” on the tablet")
            self._reconnecting = True
            GLib.timeout_add_seconds(3, once(self._wait_for_tablet, RECONNECT_WINDOW))
        elif self._running:
            self.stop()
