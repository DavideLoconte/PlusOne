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
import os
import socket
import struct
import threading
import time

import gi
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
# Two fingers tapped together are the right button: a first finger waits
# this long for a second before it goes down on the desktop; both lifted
# within TAP_TIME, having moved less than TAP_MOVE of the screen, click.
SECOND_FINGER = 0.08  # seconds
TAP_TIME = 0.3
TAP_MOVE = 0.02


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
        # Fingers not on the desktop yet, while it may be a two-finger tap:
        # (since, [states]), replayed as touches once it is not.
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
        two-finger tap (the right button)."""
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
        if not two:
            # One finger: down at once if it went (a tap), or once the
            # second has not come.
            if not states[-1] or elapsed >= SECOND_FINGER:
                self._replay()
            return
        first = {}
        for state in states:
            for finger, where in state.items():
                first.setdefault(finger, where)
        moved = max(abs(x - first[f][0]) / self.size[0] + abs(y - first[f][1]) / self.size[1]
                    for state in states for f, (x, y) in state.items())
        if not states[-1] and elapsed < TAP_TIME and moved < TAP_MOVE:
            self.waiting = None
            x, y = next(iter(first.values()))
            self.monitor.pointer_move(x, y)
            self.monitor.pointer_button(pen.BTN_RIGHT, True)
            self.monitor.pointer_button(pen.BTN_RIGHT, False)
        elif moved >= TAP_MOVE or elapsed >= TAP_TIME or len(states[-1]) > 2 or not states[-1]:
            self._replay()  # a pinch, two fingers resting, three: touches

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
        self.aead = AESGCM(key)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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

    def receive(self, timeout):
        """A body from the app, or None (nothing in time, or not the app's)."""
        self.sock.settimeout(timeout)
        try:
            packet, address = self.sock.recvfrom(2048)
        except (socket.timeout, OSError):
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


class AppStreamer:
    """The virtual monitor (or a test pattern), encoded, to the app."""

    def __init__(self, source, channel, width, height, fps, bitrate, encoder, on_stats=None, input=None,
                 on_visible=None):
        """input: an Input for the fingers and the pen, or None.
        on_visible(shown): from the listening thread, when the app starts or
        stops showing the desktop (left for another app, its screen off)."""
        self.channel = channel
        self.on_stats = on_stats or (lambda stats: None)
        self.on_visible = on_visible or (lambda shown: None)
        self.input = input
        self.shown = True
        if source == "test":
            video = (f"videotestsrc is-live=true pattern=ball ! "
                     f"video/x-raw,format={media.CAPTURE_FORMAT},width={width},height={height},"
                     f"framerate={fps}/1 ! "
                     f"timeoverlay font-desc=\"Sans 90\" valignment=center halignment=center")
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
        enc = self.pipeline.get_by_name("enc")
        media.Streamer._cap_keyframes(enc, bitrate, fps)
        # When each frame going into the encoder was captured, in order: one
        # comes out per one in (no B frames), and a flush drops those held.
        # (Its output timestamps could not be traced back after a flush.)
        self._captured = collections.deque()
        self.keyframes = media.Keyframes(enc, on_flush=self._captured.clear)
        self._in_pad = enc.get_static_pad("sink")
        self._in_probe = self._in_pad.add_probe(Gst.PadProbeType.BUFFER, self._frame_in)
        self._sink = self.pipeline.get_by_name("out")
        self._handler = self._sink.connect("new-sample", self._send)
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
        self.keyframes.remove()
        self._in_pad.remove_probe(self._in_probe)

    def _frame_in(self, pad, info):
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
            body = self.channel.receive(0.02)  # Input.check(): a first finger waits 80 ms at most
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
                self.on_stats({"shown": shown, "lost": lost,
                               "capture_to_decoded": [v / 10 for v in ms[:3]],
                               "whole_to_decoded": [v / 10 for v in ms[3:]],
                               "capture_to_sent": encoded[len(encoded) // 2] if encoded else None})

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
            f"{sent if sent is None else round(sent, 1)} ms, first packet to decoded {stats['whole_to_decoded'][0]:.1f} ms")


class AppDisplay:
    """A display session with the paired tablet's app: the virtual monitor
    (or a test pattern), its stream, the fingers and the pen, for as long as
    the app shows it. Callbacks run on the main loop:
    on_started((width, height, fps)), on_ended(error or None, started),
    on_paused(paused)."""

    def __init__(self, targets, app_id, mode, bitrate, encoder, touch=True, test=False,
                 on_started=None, on_ended=None, on_paused=None):
        self.targets, self.app_id, self.mode = targets, app_id, mode
        self.bitrate, self.encoder, self.touch, self.test = bitrate, encoder, touch, test
        self.on_started = on_started or (lambda mode: None)
        self.on_ended = on_ended or (lambda error, started: None)
        self.on_paused = on_paused or (lambda paused: None)
        self.channel = self.monitor = self.streamer = self.sock = None
        self.tablet_size = None  # what the app said its screen is
        self.started = False
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
        self.monitor = VirtualMonitor(on_ready=self._stream)
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
            self.streamer = AppStreamer(source, self.channel, width, height, fps, self.bitrate, self.encoder,
                                        on_stats=lambda stats: log("app display:", describe(stats)),
                                        input=touch,
                                        on_visible=lambda shown: GLib.idle_add(once(self.on_paused, not shown)))
            self.streamer.start()
        except Exception as e:  # noqa: BLE001 - said, not left half-started
            self._ended(e)
            return
        self.started = True
        self.on_started(self.mode)

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
    ap.add_argument("--size", default="1920x1200", help="WxH")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    ap.add_argument("--encoder", default="auto")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--test", action="store_true", help="a test pattern instead of a virtual monitor")
    args = ap.parse_args(argv)
    Gst.init(None)
    paired = companion.Paired().all()
    if len(paired) != 1:
        raise SystemExit("pair exactly one tablet first")
    width, height = map(int, args.size.split("x"))
    loop = GLib.MainLoop()
    session = AppDisplay([(args.address, args.port)], next(iter(paired))[:16], (width, height, args.fps),
                         args.bitrate, media.pick_encoder(args.encoder), test=args.test,
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
