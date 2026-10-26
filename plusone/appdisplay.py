"""The desktop in Plus One's app, instead of Second Screen.

Each encoded frame (H.264, no MPEG-TS) is cut into UDP packets and sent the
moment the encoder has it, sealed with AES-GCM under a key handed to the app
over the paired TLS link for this session (DisplaySession.kt has the
packets' layout). The app decodes each frame as soon as it is whole and
shows it as soon as it is decoded, and says how long that took: frames
carry the time they were captured, and pings match the two clocks.
"""
import argparse
import collections
import fcntl
import os
import select
import socket
import struct
import termios
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # noqa: E402

from . import companion, log, media, once, pen  # noqa: E402

VIDEO, KEYFRAME, PING, PONG, STATS, INPUT = 1, 2, 3, 4, 5, 6
FROM_PC, FROM_APP = 1, 2
NONCE = struct.Struct("!IQ")         # direction, count
VIDEO_HEADER = struct.Struct("!BBIHHq")  # kind, flags, frame, index, count, captured
# H.264 per packet: with the header, nonce and tag, one Ethernet frame.
PAYLOAD = 1472 - VIDEO_HEADER.size - NONCE.size - 16
MIN_KEYFRAME_GAP = 0.25  # seconds; the app asks again until one comes
STATS_FORMAT = struct.Struct("!BII6H")
FINGER = struct.Struct("!BHH")  # id, x, y (0..65535 of the screen)
PEN_SAMPLE = 9                  # the S Pen's report after its ID (DisplayInput.kt)
TOUCH_MAX = 65535
MAX_FINGERS = 10
INPUT_SILENCE = 0.4  # seconds: the app repeats what touches every 0.1 s; quiet this long, all is lifted
IDLE_WAIT = 0.25     # seconds the listener sleeps while nothing touches (stop() waits as long)
STATS_EVERY = 30     # the app's reports (one a second) logged: one in so many, and each with a loss
# A finger tapped (lifted within TAP_TIME, having moved less than TAP_SLOP
# of the screen) is the left button, as the pen's tip: a touch tapped is
# hover and focus to some programs (Qt's, Telegram's), which wanted a
# second tap. Two fingers tapped together (less than TAP_MOVE) are the right
# button. Anything else (a drag, a scroll, a pinch, a finger held) goes on
# as touches, from where it began: a finger waits until it is not a tap.
TAP_TIME = 0.3   # seconds
TAP_SLOP = 0.012
TAP_MOVE = 0.02
# The bitrate follows the network (RateControl): from the one set (its
# most) down to MIN_KBPS while the app reports frames lost or the delay
# growing, up again slowly while it does not.
MIN_KBPS = 1500
QUEUE_MS = 60    # data waiting to go out past this many ms of the bitrate: the next frames are not encoded


# Widths offered for the desktop in the app, each at the tablet's proportions
# where that comes out in whole, even pixels; the tablet's own size first.
WIDTHS = (3840, 2560, 2304, 1920, 1680, 1440, 1280)
REFERENCE = 1920 * 1200  # the size the bitrate presets are for
HIDPI_WIDTH = 2560       # from this wide, the monitor at scale 2 (text as at half the size, sharp)


def sizes(screen):
    """The sizes the desktop can have on a tablet of this screen (w, h),
    largest first, all at its proportions (to within 0.5%: even heights)."""
    width, height = max(screen), min(screen)
    found = [(width, height)]
    for w in WIDTHS:
        h = round(w * height / width / 2) * 2
        if w < width and abs(w / h - width / height) < 0.005 * width / height:
            found.append((w, h))
    return found


def auto_size(screen):
    """The size closest to 1920x1200 in pixels, at the tablet's proportions."""
    return min(sizes(screen), key=lambda size: abs(size[0] * size[1] - REFERENCE))


def scale_for(size):
    return 2 if size[0] >= HIDPI_WIDTH else 1


def bitrate_for(bitrate, size):
    """The presets' bitrate (kbit/s, for 1920x1200) for this many pixels."""
    return int(bitrate * max(1.0, size[0] * size[1] / REFERENCE))


class Input:
    """Fingers and the pen from the app, onto the virtual monitor: each
    packet says what touches the screen now (DisplayInput.kt), so a finger
    is put down, moved or lifted from the difference with the one before."""

    def __init__(self, monitor, size, make_pen, clock=time.monotonic):
        self.monitor, self.size, self.make_pen, self.clock = monitor, size, make_pen, clock
        self.down = {}       # finger id -> (x, y) in the monitor's pixels
        self.pen = None
        self.pen_last = None  # the last report, while the pen is in range
        self.heard = clock()
        # Fingers not on the desktop yet, while it may be a tap: (since,
        # [states]), a click, or replayed as touches once it is not one.
        self.waiting = None

    def packet(self, body):
        """body: an INPUT packet; False if it is not a well-formed one."""
        try:
            count, at = body[1], 2
            if count > MAX_FINGERS:
                return False
            fingers = {}
            for _ in range(count):
                finger, x, y = FINGER.unpack_from(body, at)
                fingers[finger] = (x / TOUCH_MAX * self.size[0], y / TOUCH_MAX * self.size[1])
                at += FINGER.size
            samples = body[at]
            at += 1
            if len(body) != at + samples * PEN_SAMPLE:
                return False
        except (IndexError, struct.error):
            return False
        self.heard = self.clock()
        self._touch(fingers)
        for i in range(samples):
            self._pen(bytes([pen.REPORT_ID]) + body[at + i * PEN_SAMPLE:at + (i + 1) * PEN_SAMPLE])
        return True

    def _touch(self, fingers):
        """Fingers as they are now: on the desktop, unless they may be a
        tap (the left button) or a two-finger tap (the right one)."""
        if self.waiting is None:
            if self.down or not fingers:
                self._fingers(fingers)
                return
            self.waiting = (self.clock(), [])
        self.waiting[1].append(fingers)
        self._decide()

    def _decide(self):
        since, states = self.waiting
        elapsed = self.clock() - since
        two = any(len(state) >= 2 for state in states)
        first = {}
        for state in states:
            for finger, where in state.items():
                first.setdefault(finger, where)
        moved = max((abs(x - first[f][0]) / self.size[0] + abs(y - first[f][1]) / self.size[1]
                     for state in states for f, (x, y) in state.items()), default=0)
        lifted = not states[-1]
        if not two:
            if lifted and elapsed < TAP_TIME and moved < TAP_SLOP:
                self._click(next(iter(first.values())), pen.BTN_LEFT)
            elif lifted or moved >= TAP_SLOP or elapsed >= TAP_TIME:
                self._replay()  # a drag, a scroll, a finger held: touches
            return
        if lifted and elapsed < TAP_TIME and moved < TAP_MOVE:
            self._click(next(iter(first.values())), pen.BTN_RIGHT)
        elif moved >= TAP_MOVE or elapsed >= TAP_TIME or len(states[-1]) > 2 or lifted:
            self._replay()  # a pinch, two fingers resting, three: touches

    def _click(self, where, button):
        self.waiting = None
        self.monitor.pointer_move(*where)
        self.monitor.pointer_button(button, True)
        self.monitor.pointer_button(button, False)

    def _replay(self):
        states, self.waiting = self.waiting[1], None
        for state in states:
            self._fingers(state)

    def _fingers(self, fingers):
        for finger in [f for f in self.down if f not in fingers]:
            self.monitor.touch_up(finger)
            del self.down[finger]
        for finger, where in fingers.items():
            if finger not in self.down:
                self.monitor.touch_down(finger, *where)
            elif where != self.down[finger]:
                self.monitor.touch_motion(finger, *where)
            self.down[finger] = where

    def _pen(self, report):
        if self.pen is None:
            try:
                self.pen = self.make_pen()
            except Exception as e:  # noqa: BLE001 - no pen, the fingers still work
                log("app display: no pen:", repr(e))
                self.pen = False
        if self.pen:
            self.pen.report(report)
        self.pen_last = report if report[1] & pen.IN_RANGE else None

    def busy(self):
        """Whether check() has something to look after: a finger down or
        waiting, the pen near."""
        return bool(self.down) or self.waiting is not None or self.pen_last is not None

    def check(self):
        """Every few ms: a first finger waited long enough for a second; and
        nothing heard for a while, whatever touches is lifted (the app's last
        packets were lost, or it is gone)."""
        if self.waiting is not None:
            self._decide()
        if self.clock() - self.heard < INPUT_SILENCE:
            return
        if self.waiting is not None:
            self.waiting = None  # never on the desktop: nothing to lift
        if self.down:
            self._fingers({})
        if self.pen_last is not None:
            self._pen(self.pen_last[:1] + b"\x00" + self.pen_last[2:])

    def close(self):
        self.waiting = None
        self._fingers({})
        if self.pen_last is not None:
            self._pen(self.pen_last[:1] + b"\x00" + self.pen_last[2:])
        if self.pen:
            self.pen.close()
        self.pen = None


class Channel:
    """The session's UDP socket, sealed both ways."""

    def __init__(self, key):
        # Here, not at the top: the window uses this module's sizes
        # where python3-cryptography may be missing (from the sources).
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.aead = AESGCM(key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Room for a keyframe's packets at once (as much of it as the kernel
        # allows: net.core.wmem_max; past it, sending waits for the Wi-Fi).
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
        self.sock.bind(("0.0.0.0", 0))
        self.port = self.sock.getsockname()[1]
        self.app = None          # (address, port), from the app's answer
        self._sent = 0
        self._lock = threading.Lock()
        self._heard = -1         # the app's highest count: older ones are replays
        self.errors = set()

    def send(self, body):
        with self._lock:
            nonce = NONCE.pack(FROM_PC, self._sent)
            self._sent += 1
        try:
            self.sock.sendto(nonce + self.aead.encrypt(nonce, body, None), self.app)
        except OSError as e:
            if e.errno not in self.errors:
                self.errors.add(e.errno)
                log("app display: cannot send:", e.strerror)

    def queued(self):
        """Bytes sent and not yet on the air: in the socket, the queue, the
        Wi-Fi driver (each packet counts until it is transmitted)."""
        try:
            return struct.unpack("i", fcntl.ioctl(self.sock.fileno(), termios.TIOCOUTQ, b"\0" * 4))[0]
        except (OSError, ValueError):
            return 0

    def receive(self, timeout):
        """A body from the app, or None (nothing in time, or not the app's).
        The socket stays blocking: a timeout on it would be the streaming
        thread's too, and a frame's tail dropped when the buffer is full."""
        try:
            if not select.select([self.sock], [], [], timeout)[0]:
                return None
            packet, address = self.sock.recvfrom(2048)
        except (OSError, ValueError):  # closed
            return None
        if address[0] != self.app[0] or len(packet) < NONCE.size + 17:
            return None
        direction, count = NONCE.unpack_from(packet)
        if direction != FROM_APP or count <= self._heard:
            return None
        try:
            body = self.aead.decrypt(packet[:NONCE.size], packet[NONCE.size:], None)
        except Exception:  # noqa: BLE001 - not sealed with this session's key
            return None
        self._heard = count
        return body

    def close(self):
        self.sock.close()


class RateControl:
    """The bitrate the network takes, from the app's report each second:
    down by a third at once when frames were lost or the delay through the
    network grew (a queue filling somewhere), up by a tenth after a few
    clean seconds, never above the one set (most) nor below MIN_KBPS.
    The delay through the network: the app's capture-to-decoded less its
    first-packet-to-decoded (decoding, which does not change with the
    network), against the least of the last seconds; only from a second
    with enough frames: a still desktop's one or two say little (Wi-Fi's
    own jitter), and it went down for nothing."""

    GROWN_MS = 25   # over the least: a queue
    CLEAN = 3       # reports in a row without loss nor queue before going up
    HISTORY = 10    # reports the least is taken over
    FRAMES = 10     # shown in the second, for its delay to count

    def __init__(self, most):
        self.most = most
        self.kbps = most
        self.clean = 0
        self.delays = collections.deque(maxlen=self.HISTORY)

    def report(self, shown, lost, capture_to_decoded, first_to_decoded):
        """A report; the new bitrate if it changed, else None."""
        delay = capture_to_decoded - first_to_decoded if shown >= self.FRAMES else None
        if delay is not None:
            self.delays.append(delay)
        grown = delay is not None and len(self.delays) >= 3 and delay > min(self.delays) + self.GROWN_MS
        if lost or grown:
            self.clean = 0
            return self._set(self.kbps * 2 // 3)
        self.clean += 1
        if self.clean >= self.CLEAN and self.kbps < self.most:
            self.clean = 0
            return self._set(self.kbps * 11 // 10 + 100)
        return None

    def _set(self, kbps):
        kbps = max(MIN_KBPS, min(self.most, kbps))
        if kbps == self.kbps:
            return None
        self.kbps = kbps
        return kbps


class AppStreamer:
    """The virtual monitor (or a test pattern), encoded, to the app."""

    def __init__(self, source, channel, width, height, fps, bitrate, encoder, on_stats=None, input=None,
                 on_visible=None, preview=False, on_error=None):
        """input: an Input for the fingers and the pen, or None.
        on_visible(shown): from the listening thread, when the app starts or
        stops showing the desktop (left for another app, its screen off).
        on_error(message): on the main loop, if the pipeline fails."""
        self.channel = channel
        self.on_stats = on_stats or (lambda stats: None)
        self.on_visible = on_visible or (lambda shown: None)
        self.on_error = on_error or (lambda message: None)
        self.input = input
        self.shown = True
        if source == "test":
            video = (f"videotestsrc is-live=true pattern=ball ! "
                     f"video/x-raw,format={media.CAPTURE_FORMAT},width={width},height={height},"
                     f"framerate={fps}/1 ! "
                     f"timeoverlay font-desc=\"Sans 90\" valignment=center halignment=center")
            if preview:
                # The same picture in a window here: a photo of both screens
                # reads the delay to the tablet's glass off the two timers.
                video += (" ! tee name=preview ! queue max-size-buffers=1 leaky=downstream ! "
                          "videoscale ! video/x-raw,width=960,height=600 ! videoconvert ! "
                          "autovideosink sync=false preview.")
        else:
            video = (f"pipewiresrc path={source} do-timestamp=true always-copy=false keepalive-time=100 ! "
                     f"video/x-raw,format={media.CAPTURE_FORMAT},width={width},height={height},"
                     f"max-framerate={fps}/1")
        element, options, raw_format = media.ENCODERS[encoder]
        enc = f"{element} name=enc " + options.format(bitrate=bitrate, gop=fps * media.KEYFRAME_SECONDS)
        convert = media.CONVERT.get(encoder, f"videoconvert n-threads=4 ! video/x-raw,format={raw_format}")
        self.pipeline = Gst.parse_launch(
            f"{video} ! queue max-size-buffers=1 leaky=downstream ! {convert} ! "
            f"{enc} ! video/x-h264,profile=high ! h264parse config-interval=-1 ! "
            f"video/x-h264,stream-format=byte-stream,alignment=au ! "
            f"appsink name=out emit-signals=true sync=false async=false max-buffers=0")
        enc = self.enc = self.pipeline.get_by_name("enc")
        media.cap_keyframes(enc, bitrate, fps)
        self.fps = fps
        self.rate = RateControl(bitrate)
        self.skipped = 0  # frames not encoded: the network behind
        # When each frame going into the encoder was captured, in order: one
        # comes out per one in (no B frames), and a flush drops those held.
        # (Its output timestamps could not be traced back after a flush.)
        self._captured = collections.deque()
        self.keyframes = media.Keyframes(enc, on_flush=self._captured.clear)
        self._in_pad = enc.get_static_pad("sink")
        self._in_probe = self._in_pad.add_probe(Gst.PadProbeType.BUFFER, self._frame_in)
        self._sink = self.pipeline.get_by_name("out")
        self._handler = self._sink.connect("new-sample", self._send)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_handler = bus.connect("message::error", self._failed)
        self._frame = 0
        self._last_keyframe = 0.0
        self._encoded = []  # ms from capture to sent, since the last stats
        self._running = True
        self._feedback = threading.Thread(target=self._listen, daemon=True)
        log(f"app display: {width}x{height}@{fps} {encoder} {bitrate} kbit/s -> {channel.app[0]}:{channel.app[1]}")

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)
        self._feedback.start()

    def stop(self):
        self._running = False
        self._feedback.join(timeout=2)
        if self.input:
            self.input.close()
        self.pipeline.set_state(Gst.State.NULL)
        self._sink.disconnect(self._handler)
        bus = self.pipeline.get_bus()
        bus.disconnect(self._bus_handler)
        bus.remove_signal_watch()
        self.keyframes.remove()
        self._in_pad.remove_probe(self._in_probe)

    def _failed(self, _bus, message):
        error, debug = message.parse_error()
        log("app display: pipeline error:", error.message, debug or "")
        self.on_error(f"{message.src.get_name()}: {error.message}")

    def _frame_in(self, pad, info):
        # The network behind (more waiting to go out than QUEUE_MS of the
        # bitrate): this frame is not encoded, rather than queued after the
        # others (the delay would grow, and grow). A frame that never went
        # in breaks nothing; the next one is encoded against the last.
        if self.channel.queued() > max(64 << 10, self.rate.kbps * QUEUE_MS // 8):
            self.skipped += 1
            return Gst.PadProbeReturn.DROP
        # pipewiresrc and a live test source stamp buffers with the running
        # time at capture, and the pipeline's clock is the monotonic one.
        buf = info.get_buffer()
        event = pad.get_sticky_event(Gst.EventType.SEGMENT, 0)
        running = event.parse_segment().to_running_time(Gst.Format.TIME, buf.pts) if event else Gst.CLOCK_TIME_NONE
        self._captured.append(self.pipeline.get_base_time() + running
                              if running != Gst.CLOCK_TIME_NONE else time.monotonic_ns())
        while len(self._captured) > 16:  # never out of step for long
            self._captured.popleft()
        return Gst.PadProbeReturn.OK

    def _send(self, appsink):
        """Streaming thread: one frame, out at once."""
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buf = sample.get_buffer()
        data = buf.extract_dup(0, buf.get_size())
        captured = self._captured.popleft() if self._captured else time.monotonic_ns()
        keyframe = not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)
        count = -(-len(data) // PAYLOAD)
        number = self._frame = self._frame + 1
        for index in range(count):
            header = VIDEO_HEADER.pack(VIDEO, int(keyframe), number, index, count, captured)
            self.channel.send(header + data[index * PAYLOAD:(index + 1) * PAYLOAD])
        self._encoded.append((time.monotonic_ns() - captured) / 1e6)
        return Gst.FlowReturn.OK

    def _listen(self):
        while self._running:
            # Input.check() every 20 ms while something touches or waits (a
            # first finger waits 80 ms at most for a second); else seldom.
            busy = self.input is not None and self.input.busy()
            body = self.channel.receive(0.02 if busy else IDLE_WAIT)
            if self.input:
                self.input.check()
            if not body:
                continue
            kind = body[0]
            if kind == INPUT:
                if self.input and not self.input.packet(body):
                    log("app display: a malformed input packet")
            elif kind == KEYFRAME:
                log("app display: the app asks for a keyframe")
                self.force_keyframe()
            elif kind == PING and len(body) in (9, 10):
                self.channel.send(struct.pack("!Bqq", PONG, struct.unpack_from("!q", body, 1)[0],
                                              time.monotonic_ns()))
                if len(body) == 10:
                    self.visible(bool(body[9]))
            elif kind == STATS and len(body) == STATS_FORMAT.size:
                _kind, shown, lost, *ms = STATS_FORMAT.unpack(body)
                encoded, self._encoded = sorted(self._encoded), []
                skipped, self.skipped = self.skipped, 0
                before = self.rate.kbps
                kbps = self.rate.report(shown, lost, ms[0] / 10, ms[3] / 10)
                if kbps is not None:
                    self.enc.set_property("bitrate", kbps)
                    media.cap_keyframes(self.enc, kbps, self.fps)
                self.on_stats({"shown": shown, "lost": lost,
                               "capture_to_decoded": [v / 10 for v in ms[:3]],
                               "whole_to_decoded": [v / 10 for v in ms[3:]],
                               "capture_to_sent": encoded[len(encoded) // 2] if encoded else None,
                               "skipped": skipped, "kbps": self.rate.kbps,
                               "changed": kbps is not None and (before, kbps)})

    def visible(self, shown):
        """The app shows the desktop, or not: nothing is captured or encoded
        while it does not (Mutter's monitor stays, windows and all)."""
        if shown == self.shown:
            return
        self.shown = shown
        if not shown and self.input:
            self.input.close()  # nothing left touching
        self.pipeline.set_state(Gst.State.PLAYING if shown else Gst.State.PAUSED)
        if shown:
            self.force_keyframe()
        self.on_visible(shown)

    def force_keyframe(self):
        now = time.monotonic()
        if now - self._last_keyframe < MIN_KEYFRAME_GAP:
            return
        self._last_keyframe = now
        self.keyframes.request()


def describe(stats):
    decoded = stats["capture_to_decoded"]
    sent = stats["capture_to_sent"]
    return (f"{stats['shown']} fps, lost {stats['lost']}; capture to decoded {decoded[0]:.1f} ms "
            f"(90%: {decoded[1]:.1f}, max {decoded[2]:.1f}); of which capture to sent "
            f"{sent if sent is None else round(sent, 1)} ms, first packet to decoded {stats['whole_to_decoded'][0]:.1f} ms"
            + (f"; {stats['skipped']} not encoded (the network behind)" if stats.get("skipped") else "")
            + (f"; {stats['kbps']} kbit/s" if "kbps" in stats else ""))


class AppDisplay:
    """A display session with the paired tablet's app: the virtual monitor
    (or a test pattern), its stream, the fingers and the pen, for as long as
    the app shows it. mode: (width, height, fps), the size None for the one
    closest to 1920x1200 at the tablet's proportions (also when the size
    asked for is not at them: another tablet). Callbacks run on the main
    loop: on_started((width, height, fps), tablet's screen),
    on_ended(error or None, started), on_paused(paused)."""

    def __init__(self, targets, app_id, mode, bitrate, encoder, touch=True, test=False, preview=False,
                 on_started=None, on_ended=None, on_paused=None):
        self.targets, self.app_id, self.mode = targets, app_id, mode
        self.bitrate, self.encoder, self.touch, self.test = bitrate, encoder, touch, test
        self.preview = preview
        self.on_started = on_started or (lambda mode, screen: None)
        self.on_ended = on_ended or (lambda error, started: None)
        self.on_paused = on_paused or (lambda paused: None)
        self.channel = self.monitor = self.streamer = self.sock = None
        self.tablet_size = None  # what the app said its screen is
        self.started = False
        self._reports = 0
        self._stopped = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        """On the main loop: everything ends now; the app is told by the
        link's thread (it sends "stop" and closes)."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._teardown()

    # -- the link's thread ------------------------------------------------------
    def _run(self):
        error = None
        try:
            self.sock, wire = companion.open_link(self.targets, self.app_id)
            if self._stopped.is_set():
                return
            key = os.urandom(32)
            self.channel = Channel(key)
            wire.send({"type": "display", "key": key.hex(), "port": self.channel.port})
            reply = wire.receive()
            if reply.get("type") == "refused":
                raise companion.Refused(str(reply.get("reason", "")))
            if reply.get("type") != "display-ready":
                raise ValueError(f"the app answered {reply.get('type')!r}")
            address = self.sock.getpeername()[0]
            self.channel.app = (address, int(reply["port"]))
            self.tablet_size = (int(reply["width"]), int(reply["height"]))
            log(f"app display: {address} shows {self.tablet_size[0]}x{self.tablet_size[1]}"
                f" at {float(reply.get('refresh', 0)):.0f} Hz")
            width, height, fps = self.mode
            if (width, height) not in sizes(self.tablet_size):
                width, height = auto_size(self.tablet_size)
            self.mode = (width, height, fps)
            GLib.idle_add(once(self._open))
            self._hold(wire)
        except Exception as e:  # noqa: BLE001 - not there, refused, or gone: said to the caller
            error = e
        finally:
            if self.sock is not None:
                self.sock.close()
            if not self._stopped.is_set():
                GLib.idle_add(once(self._ended, error))

    def _hold(self, wire):
        """While the session lasts: pings (the app lets go of a silent PC),
        and the end when the app hangs up (Back on the tablet) or stop()."""
        pinged = time.monotonic()
        while not self._stopped.is_set():
            if wire.readable(0.5):
                wire.receive()  # nothing is expected; EOF raises
            if time.monotonic() - pinged > 5:
                wire.send({"type": "ping"})
                pinged = time.monotonic()
        try:
            wire.send({"type": "stop"})
        except OSError:
            pass

    # -- the main loop ---------------------------------------------------------
    def _open(self):
        if self._stopped.is_set():
            return
        if self.test:
            self._stream("test")
            return
        from .display import VirtualMonitor
        self.monitor = VirtualMonitor(on_ready=self._stream,
                                      on_closed=lambda: self._ended(RuntimeError("the screen sharing was stopped")))
        try:
            self.monitor.start()
        except GLib.Error as e:
            self._ended(e)

    def _stream(self, source):
        if self._stopped.is_set() or self.streamer is not None:
            return
        width, height, fps = self.mode
        touch = None
        if self.touch and self.monitor is not None:
            monitor = self.monitor
            touch = Input(monitor, (width, height), lambda: pen.for_monitor(monitor, (width, height)))
        try:
            self.streamer = AppStreamer(source, self.channel, width, height, fps,
                                        bitrate_for(self.bitrate, (width, height)), self.encoder,
                                        on_stats=self._stats,
                                        input=touch,
                                        on_visible=lambda shown: GLib.idle_add(once(self.on_paused, not shown)),
                                        preview=self.preview,
                                        on_error=lambda message: self._ended(RuntimeError(message)))
            self.streamer.start()
        except Exception as e:  # noqa: BLE001 - said, not left half-started
            self._ended(e)
            return
        self.started = True
        if self.monitor is not None and scale_for((width, height)) != 1:
            GLib.timeout_add(300, self._scale, 5)
        self.on_started(self.mode, self.tablet_size)

    def _stats(self, stats):
        """From the listening thread: the app's report of the last second."""
        self._reports += 1
        if stats.get("changed"):
            before, now = stats["changed"]
            log(f"app display: {before} -> {now} kbit/s, as the network takes")
        if stats["lost"] or stats.get("skipped") or self._reports % STATS_EVERY == 1:
            log("app display:", describe(stats))

    def _scale(self, tries):
        """The virtual monitor appears in the display configuration a
        moment after its stream: a few tries."""
        if self._stopped.is_set():
            return False
        from .display import set_virtual_monitor_scale
        try:
            if set_virtual_monitor_scale(scale_for(self.mode[:2])):
                return False
        except GLib.Error as e:
            log("display: scale not set:", e.message)
            return False
        if tries > 1:
            GLib.timeout_add(500, self._scale, tries - 1)
        return False

    def _ended(self, error=None):
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._teardown()
        self.on_ended(error, self.started)

    def _teardown(self):
        if self.streamer is not None:
            self.streamer.stop()
            self.streamer = None
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        if self.channel is not None:
            self.channel.close()


def main(argv=None):
    """The desktop (or a test pattern) to the paired tablet's app, with its
    fingers and pen, its delay measured."""
    ap = argparse.ArgumentParser(prog="python3 -m plusone.appdisplay")
    ap.add_argument("address", help="the tablet's address on the network")
    ap.add_argument("--port", type=int, default=companion.APP_PORT)
    ap.add_argument("--size", default="auto", help="WxH at the tablet's proportions, or auto")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    ap.add_argument("--encoder", default="auto")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--test", action="store_true", help="a test pattern instead of a virtual monitor")
    ap.add_argument("--preview", action="store_true", help="the test pattern in a window here too, to photograph")
    args = ap.parse_args(argv)
    Gst.init(None)
    paired = companion.Paired().all()
    if len(paired) != 1:
        raise SystemExit("pair exactly one tablet first")
    width, height = (None, None) if args.size == "auto" else map(int, args.size.split("x"))
    loop = GLib.MainLoop()
    session = AppDisplay([(args.address, args.port)], next(iter(paired))[:16], (width, height, args.fps),
                         args.bitrate, media.pick_encoder(args.encoder), test=args.test, preview=args.preview,
                         on_ended=lambda error, started: (log("app display: ended", repr(error)), loop.quit()))
    session.start()
    GLib.timeout_add_seconds(args.seconds, loop.quit)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        session.stop()
        time.sleep(1)  # the link's thread tells the app


if __name__ == "__main__":
    main()
