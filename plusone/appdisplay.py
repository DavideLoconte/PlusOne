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
from gi.repository import Gst  # noqa: E402

from . import companion, log, media  # noqa: E402

VIDEO, KEYFRAME, PING, PONG, STATS = 1, 2, 3, 4, 5
FROM_PC, FROM_APP = 1, 2
NONCE = struct.Struct("!IQ")         # direction, count
VIDEO_HEADER = struct.Struct("!BBIHHq")  # kind, flags, frame, index, count, captured
# H.264 per packet: with the header, nonce and tag, one Ethernet frame.
PAYLOAD = 1472 - VIDEO_HEADER.size - NONCE.size - 16
MIN_KEYFRAME_GAP = 0.25  # seconds; the app asks again until one comes
STATS_FORMAT = struct.Struct("!BII6H")


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

    def __init__(self, source, channel, width, height, fps, bitrate, encoder, on_stats=None):
        self.channel = channel
        self.on_stats = on_stats or (lambda stats: None)
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
        self.pipeline.set_state(Gst.State.NULL)
        self._sink.disconnect(self._handler)
        self.keyframes.remove()
        self._in_pad.remove_probe(self._in_probe)
        self._feedback.join(timeout=2)

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
            body = self.channel.receive(0.5)
            if not body:
                continue
            kind = body[0]
            if kind == KEYFRAME:
                log("app display: the app asks for a keyframe")
                self.force_keyframe()
            elif kind == PING and len(body) == 9:
                self.channel.send(struct.pack("!Bqq", PONG, struct.unpack_from("!q", body, 1)[0],
                                              time.monotonic_ns()))
            elif kind == STATS and len(body) == STATS_FORMAT.size:
                _kind, shown, lost, *ms = STATS_FORMAT.unpack(body)
                encoded, self._encoded = sorted(self._encoded), []
                self.on_stats({"shown": shown, "lost": lost,
                               "capture_to_decoded": [v / 10 for v in ms[:3]],
                               "whole_to_decoded": [v / 10 for v in ms[3:]],
                               "capture_to_sent": encoded[len(encoded) // 2] if encoded else None})

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


def main(argv=None):
    """A test pattern to the paired tablet's app, its delay measured."""
    ap = argparse.ArgumentParser(prog="python3 -m plusone.appdisplay")
    ap.add_argument("address", help="the tablet's address on the network")
    ap.add_argument("--port", type=int, default=companion.APP_PORT)
    ap.add_argument("--size", default="1920x1200", help="WxH, or native")
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--bitrate", type=int, default=20000, help="kbit/s")
    ap.add_argument("--encoder", default="auto")
    ap.add_argument("--seconds", type=int, default=20)
    args = ap.parse_args(argv)
    Gst.init(None)
    paired = companion.Paired().all()
    if len(paired) != 1:
        raise SystemExit("pair exactly one tablet first")
    app_id = next(iter(paired))[:16]
    sock, wire = companion.open_link([(args.address, args.port)], app_id)
    key = os.urandom(32)
    channel = Channel(key)
    try:
        wire.send({"type": "display", "key": key.hex(), "port": channel.port})
        reply = wire.receive()
        if reply.get("type") != "display-ready":
            raise SystemExit(f"the app answered {reply}")
        channel.app = (args.address, int(reply["port"]))
        log(f"app display: the tablet is {reply['width']}x{reply['height']} at {reply['refresh']:.0f} Hz")
        width, height = ((int(reply["width"]), int(reply["height"])) if args.size == "native"
                         else map(int, args.size.split("x")))
        streamer = AppStreamer("test", channel, width, height, args.fps, args.bitrate,
                               media.pick_encoder(args.encoder),
                               on_stats=lambda stats: log("app display:", describe(stats)))
        streamer.start()
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            time.sleep(5)
            wire.send({"type": "ping"})
        wire.send({"type": "stop"})
        streamer.stop()
    finally:
        channel.close()
        sock.close()


if __name__ == "__main__":
    main()
