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
    # Intel's low-power encoder (VDENC), the frame turned into NV12 on the
    # GPU too: first, for the battery. Measured at 1920x1200@60: 3.8 ms to the
    # encoder's output and 3.5% of a core, against NVENC's 4.7 ms, 7% and the
    # NVIDIA GPU woken (about 8 W more).
    "va-lp": ("vah264lpenc", "rate-control=cbr bitrate={bitrate} key-int-max={gop} b-frames=0 "
              "ref-frames=1 target-usage=7 aud=false", "NV12"),
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
# How each encoder gets its raw format: VA's on the GPU (vapostproc; on the
# CPU it was +30% of a core), the others' by videoconvert.
VA_CONVERT = "vapostproc ! video/x-raw(memory:VAMemory),format=NV12"
CONVERT = {"va-lp": VA_CONVERT, "va": VA_CONVERT}
TS_PER_RTP = 7  # 7 x 188 bytes of MPEG-TS per RTP packet, as WFD sinks expect
RTP_MP2T = 33
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
        self.sock.bind(("0.0.0.0", bind_port))
        # Not connect()ed: a connected UDP socket fails the next send after
        # any ICMP error (the packet is dropped, and the sink counts a loss).
        self.dest = (host, port)
        self.seq = random.getrandbits(16)
        self.ssrc = random.getrandbits(32)
        self.errors = set()  # kinds of send errors logged already

    def send(self, data, pts):
        """data: whole TS packets; pts: nanoseconds (RTP counts 90 kHz)."""
        stamp = (pts * 9 // 100000) & 0xFFFFFFFF
        size = 188 * TS_PER_RTP
        data += NULL_TS * (-(len(data) // 188) % TS_PER_RTP)
        for offset in range(0, len(data), size):
            header = struct.pack("!BBHII", 0x80, RTP_MP2T, self.seq, stamp, self.ssrc)
            try:
                self.sock.sendto(header + data[offset:offset + size], self.dest)
            except OSError as e:
                # Once in the log (the link went away, say): the RTSP session
                # notices. A packet not sent keeps its sequence number, so the
                # sink does not count it as lost.
                if e.errno not in self.errors:
                    self.errors.add(e.errno)
                    log("RTP: cannot send:", e.strerror)
                continue
            self.seq = (self.seq + 1) & 0xFFFF

    def close(self):
        self.sock.close()


class Keyframes:
    """Keyframes on request that a decoder can start from (an IDR, its SPS
    and PPS before it). GStreamer 1.24's VA encoders answer a force-key-unit
    event with an I frame that is not an IDR, without the SPS, and only the
    first time: a decoder that had lost its place waited for the periodic
    keyframe (KEYFRAME_SECONDS), and one that had none could not start.
    Flushing them starts their GOP again, with an IDR, at the next frame. It
    is done on the streaming thread, just before a frame goes in: from
    another thread the flush could reach the frame being pushed, and the
    queue in front would stop. NVENC ignores the flush and honours the
    event; the others get the event."""

    FLUSHED = {"vah264lpenc", "vah264enc"}

    def __init__(self, enc, on_flush=None):
        """on_flush(): on the streaming thread, after a flush threw away the
        frames the encoder held."""
        self.pad = enc.get_static_pad("sink")
        self.flush = enc.get_factory().get_name() in self.FLUSHED
        self.on_flush = on_flush or (lambda: None)
        self.wanted = False
        self.probe = self.pad.add_probe(Gst.PadProbeType.BUFFER, self._before_frame) if self.flush else None

    def request(self):
        if self.flush:
            self.wanted = True
            return
        s = Gst.Structure.new_from_string("GstForceKeyUnit, all-headers=(boolean)true")
        self.pad.send_event(Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, s))

    def _before_frame(self, pad, _info):
        if self.wanted:
            self.wanted = False
            segment = pad.get_sticky_event(Gst.EventType.SEGMENT, 0)
            pad.send_event(Gst.Event.new_flush_start())
            pad.send_event(Gst.Event.new_flush_stop(False))  # the running time goes on
            if segment is not None:
                pad.send_event(segment)  # the flush took it
            self.on_flush()
        return Gst.PadProbeReturn.OK

    def remove(self):
        if self.probe is not None:
            self.pad.remove_probe(self.probe)
            self.probe = None


def cap_keyframes(enc, bitrate, fps):
    """Each encoder names its rate-control buffer differently; set the
    ones this one has (kbit or milliseconds)."""
    kbit, ms = int(bitrate / fps * VBV_FRAMES), int(1000 * VBV_FRAMES / fps)
    for prop, value in (("vbv-buffer-size", kbit), ("cpb-size", kbit),
                        ("cpb-length", ms), ("vbv-buf-capacity", ms)):
        if enc.find_property(prop):
            enc.set_property(prop, value)


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
                 port=19000, bind_port=19000, preview=True, on_error=None):
        """source: a PipeWire node id, or "test" for a moving test pattern.
        on_error(message) runs on the main loop if the pipeline fails."""
        self.on_error = on_error or (lambda message: None)
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
            # Mutter gives the virtual monitor the stream's max-framerate as
            # its refresh rate. At 60 Hz it skips frames that come a little
            # early (a window drawing at 60 fps reached the encoder at about
            # 47), but asking for 120 is worse: the stream then ran at up to
            # 90 fps, which Second Screen (showing 60) queued, so the delay
            # grew, and it lost packets and asked for keyframes every few
            # seconds. Keep the monitor at the stream's rate.
            video = (f"pipewiresrc path={source} do-timestamp=true always-copy=false keepalive-time=100 ! "
                     f"video/x-raw,format={CAPTURE_FORMAT},width={width},height={height},"
                     f"max-framerate={fps}/1")
        element, options, raw_format = ENCODERS[encoder]
        enc = f"{element} name=enc " + options.format(bitrate=bitrate, gop=fps * KEYFRAME_SECONDS)
        convert = CONVERT.get(encoder, f"videoconvert n-threads=4 ! video/x-raw,format={raw_format}")
        self.pipeline = Gst.parse_launch(
            f"{video} ! queue max-size-buffers=1 leaky=downstream ! "
            f"{convert} ! "
            f"{enc} ! video/x-h264,profile=high ! h264parse config-interval=-1 ! "
            f"queue max-size-buffers=2 ! mpegtsmux alignment=-1 ! "
            f"appsink name=out emit-signals=true sync=false async=false buffer-list=true max-buffers=0")
        self.sender = RtpSender(host, port, bind_port)
        self._sink = self.pipeline.get_by_name("out")
        self._sink_handler = self._sink.connect("new-sample", self._send)
        cap_keyframes(self.pipeline.get_by_name("enc"), bitrate, fps)
        self.keyframes = Keyframes(self.pipeline.get_by_name("enc"))
        self._last_keyframe = 0.0
        # Encoded frames of the last seconds, (time, bytes, keyframe): when
        # the tablet loses data, the log says what was being sent.
        self._sent = collections.deque()
        self._probe_pad = self.pipeline.get_by_name("enc").get_static_pad("src")
        self._probe = self._probe_pad.add_probe(Gst.PadProbeType.BUFFER, self._count)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        self._bus_handler = bus.connect("message::error", self._on_error)
        log(f"pipeline: {width}x{height}@{fps} {encoder} {bitrate} kbit/s -> {host}:{port}")

    def _on_error(self, bus, msg):
        err, dbg = msg.parse_error()
        log("pipeline error:", err.message, dbg or "")
        self.on_error(err.message)

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)
        self.sender.close()
        # The bus watch and the handlers hold this Streamer, and it holds the
        # pipeline: without this, every session left one behind.
        bus = self.pipeline.get_bus()
        bus.disconnect(self._bus_handler)
        bus.remove_signal_watch()
        self._sink.disconnect(self._sink_handler)
        self._probe_pad.remove_probe(self._probe)
        self.keyframes.remove()

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
        self.keyframes.request()
