"""Capture the virtual monitor and stream it the way the Samsung sink likes.

Video-only MPEG-TS: without an audio track the sink renders frames as they
arrive instead of syncing them to an audio clock (measured ~90 ms -> ~50 ms).
"""
import collections
import random
import socket
import struct
import time

from gi.repository import Gst

from . import log

# name -> (GStreamer element, settings, raw format it takes), in preference order.
# A keyframe of a page of text is 80-140 KB, sent as 60-100 RTP packets back
# to back (udpsink's max-bitrate does not pace with sync=false). Keep them
# smaller and rarer: the sink asks for one when it lost data.
KEYFRAME_SECONDS = 10      # periodic keyframe interval
VBV_FRAMES = 4             # a keyframe may be this many average frames
MIN_KEYFRAME_GAP = 1.0     # seconds between keyframes forced on request

ENCODERS = {
    "nvenc": ("nvh264enc", "preset=low-latency-hq zerolatency=true bframes=0 rc-mode=cbr "
              "bitrate={bitrate} gop-size={gop} aud=false", "BGRA"),
    "va": ("vah264enc", "rate-control=cbr bitrate={bitrate} key-int-max={gop} b-frames=0 "
           "target-usage=7", "NV12"),
    "vaapi": ("vaapih264enc", "rate-control=cbr bitrate={bitrate} keyframe-period={gop} "
              "max-bframes=0 tune=low-power", "NV12"),
    "x264": ("x264enc", "tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
             "key-int-max={gop} bframes=0", "I420"),
}
# Mutter hands out BGRA as readily as BGRx; asking for it makes the colour
# conversion a no-op for NVENC (it cost ~2.3 ms of CPU per 1920x1200 frame).
CAPTURE_FORMAT = "BGRA"
# Mutter gives the virtual monitor the stream's max-framerate as its refresh
# rate, and skips frames that come a little early for it: at 60 Hz, a window
# drawing 60 frames a second reached the encoder at about 47 (frames 21 ms
# apart, often 30). At 120 Hz it records them all, 6.5 ms from capture to
# encoded; pacing them back to 60 here cost 3-4 ms more on average.
CAPTURE_RATE_FACTOR = 2
TS_PER_RTP = 7  # 7 x 188 bytes of MPEG-TS per RTP packet, as WFD sinks expect
RTP_MP2T = 33
# Wi-Fi sends packets marked EF (DSCP 46) from its video queue (WMM AC_VI),
# which waits less for the air than best effort.
DSCP_EF = 46
# Pads a frame's last RTP packet to 7 TS packets, the size sinks always got
# from mpegtsmux alignment=7; demuxers drop PID 0x1FFF.
NULL_TS = b"\x47\x1f\xff\x10" + b"\xff" * 184


class RtpSender:
    """MPEG-TS over RTP to the sink, each frame sent whole as soon as it is
    muxed. mpegtsmux alignment=7 kept a frame's last packets until the next
    frame came, and rtpmp2tpay does the same with single TS packets: every
    frame reached the sink a frame late, and the last change on a still
    screen only with the next keepalive frame (100 ms)."""

    def __init__(self, host, port, bind_port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, DSCP_EF << 2)
        self.sock.bind(("0.0.0.0", bind_port))
        self.sock.connect((host, port))
        self.seq = random.getrandbits(16)
        self.ssrc = random.getrandbits(32)
        self.packets = 0

    def send(self, data, pts):
        """data: whole TS packets; pts: nanoseconds (RTP counts 90 kHz)."""
        stamp = (pts * 9 // 100000) & 0xFFFFFFFF
        size = 188 * TS_PER_RTP
        data += NULL_TS * (-(len(data) // 188) % TS_PER_RTP)
        for offset in range(0, len(data), size):
            header = struct.pack("!BBHII", 0x80, RTP_MP2T, self.seq, stamp, self.ssrc)
            try:
                self.sock.send(header + data[offset:offset + size])
            except OSError:
                pass  # the sink is not listening (yet): ICMP port unreachable
            self.seq = (self.seq + 1) & 0xFFFF
            self.packets += 1

    def close(self):
        self.sock.close()


def pick_encoder(preferred="auto"):
    names = list(ENCODERS) if preferred == "auto" else [preferred]
    for name in names:
        if Gst.ElementFactory.find(ENCODERS[name][0]):
            return name
    raise RuntimeError("no H.264 encoder available")


def warm_up(name):
    """Load the encoder's plugin ahead of the stream (blocking): the first
    nvh264enc in a process takes 1.5 s, CUDA probing the GPUs."""
    Gst.ElementFactory.make(ENCODERS[name][0])


class Streamer:
    def __init__(self, source, host, width, height, fps, bitrate, encoder,
                 port=19000, bind_port=19000, preview=True):
        """source: a PipeWire node id, or "test" for a moving test pattern."""
        if source == "test":
            video = (f"videotestsrc is-live=true pattern=ball ! "
                     f"video/x-raw,format={CAPTURE_FORMAT},width={width},height={height},"
                     f"framerate={fps}/1 ! "
                     f"timeoverlay font-desc=\"Sans 90\" valignment=center halignment=center")
            if preview:
                # Same picture in a local window: photograph both screens to
                # read the end-to-end latency off the two timers.
                video += (" ! tee name=preview ! queue max-size-buffers=1 leaky=downstream ! "
                          "videoconvert ! autovideosink sync=false preview.")
        else:
            # Mutter only sends frames on damage; keepalive repeats the last
            # one so the sink keeps receiving data on a static screen.
            video = (f"pipewiresrc path={source} do-timestamp=true always-copy=false keepalive-time=100 ! "
                     f"video/x-raw,format={CAPTURE_FORMAT},width={width},height={height},"
                     f"max-framerate={fps * CAPTURE_RATE_FACTOR}/1")
        element, options, raw_format = ENCODERS[encoder]
        enc = f"{element} name=enc " + options.format(bitrate=bitrate, gop=fps * KEYFRAME_SECONDS)
        self.pipeline = Gst.parse_launch(
            f"{video} ! queue max-size-buffers=1 leaky=downstream ! "
            f"videoconvert n-threads=4 ! video/x-raw,format={raw_format} ! "
            f"{enc} ! video/x-h264,profile=high ! h264parse config-interval=-1 ! "
            f"queue max-size-buffers=2 ! mpegtsmux alignment=-1 ! "
            f"appsink name=out emit-signals=true sync=false async=false buffer-list=true max-buffers=0")
        self.sender = RtpSender(host, port, bind_port)
        self.pipeline.get_by_name("out").connect("new-sample", self._send)
        self._cap_keyframes(self.pipeline.get_by_name("enc"), bitrate, fps)
        self._last_keyframe = 0.0
        # Encoded frames of the last seconds, (time, bytes, keyframe): when
        # the tablet loses data, the log says what was being sent.
        self._sent = collections.deque()
        self.pipeline.get_by_name("enc").get_static_pad("src").add_probe(
            Gst.PadProbeType.BUFFER, self._count)
        self.pipeline.get_bus().add_signal_watch()
        self.pipeline.get_bus().connect("message::error", self._on_error)
        log(f"pipeline: {width}x{height}@{fps} {encoder} {bitrate} kbit/s -> {host}:{port}")

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        log("pipeline error:", err.message, dbg or "")

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.sender.close()

    def _send(self, appsink):
        """Streaming thread: what the muxer made of one frame, out at once."""
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buffers = sample.get_buffer_list()
        if buffers is None:
            buffers = [sample.get_buffer()]
        else:
            buffers = [buffers.get(i) for i in range(buffers.length())]
        data = b"".join(b.extract_dup(0, b.get_size()) for b in buffers)
        pts = next((b.pts for b in buffers if b.pts != Gst.CLOCK_TIME_NONE), None)
        if pts is None:
            pts = time.monotonic_ns()
        self.sender.send(data, pts)
        return Gst.FlowReturn.OK

    @staticmethod
    def _cap_keyframes(enc, bitrate, fps):
        """Each encoder names its rate-control buffer differently; set the
        ones this one has (kbit or milliseconds)."""
        kbit, ms = int(bitrate / fps * VBV_FRAMES), int(1000 * VBV_FRAMES / fps)
        for prop, value in (("vbv-buffer-size", kbit), ("cpb-size", kbit),
                            ("cpb-length", ms), ("vbv-buf-capacity", ms)):
            if enc.find_property(prop):
                enc.set_property(prop, value)

    def _count(self, _pad, info):
        buf = info.get_buffer()
        now = time.monotonic()
        self._sent.append((now, buf.get_size(), not buf.has_flags(Gst.BufferFlags.DELTA_UNIT)))
        while self._sent[0][0] < now - 2:
            self._sent.popleft()
        return Gst.PadProbeReturn.OK

    def sent(self):
        """What the encoder put out over the last 2 seconds."""
        frames = list(self._sent)
        size = sum(f[1] for f in frames)
        return (f"{size * 8 / 2000:.0f} kbit/s, {len(frames) / 2:.0f} frames/s, "
                f"{sum(f[2] for f in frames)} keyframes, largest frame {max((f[1] for f in frames), default=0) // 1024} KB")

    def force_keyframe(self):
        # After a loss the sink keeps asking; one keyframe per second is
        # enough, more only add bursts that cause more loss.
        now = time.monotonic()
        log(f"stream before the loss: {self.sent()}")
        if now - self._last_keyframe < MIN_KEYFRAME_GAP:
            return
        self._last_keyframe = now
        s = Gst.Structure.new_from_string("GstForceKeyUnit, all-headers=(boolean)true")
        self.pipeline.get_by_name("enc").get_static_pad("sink").send_event(
            Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, s))
