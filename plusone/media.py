"""Capture the virtual monitor and stream it the way the Samsung sink likes.

Video-only MPEG-TS: without an audio track the sink renders frames as they
arrive instead of syncing them to an audio clock (measured ~90 ms -> ~50 ms).
"""
from gi.repository import Gst

from . import log

ENCODERS = {
    "nvenc": ("nvh264enc name=enc preset=low-latency-hq zerolatency=true bframes=0 "
              "rc-mode=cbr bitrate={bitrate} gop-size={gop} aud=false"),
    "vaapi": ("vaapih264enc name=enc rate-control=cbr bitrate={bitrate} keyframe-period={gop} "
              "max-bframes=0 tune=low-power"),
    "x264": ("x264enc name=enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
             "key-int-max={gop} bframes=0"),
}
ENCODER_INPUT = {"nvenc": "BGRA", "vaapi": "NV12", "x264": "I420"}


def pick_encoder(preferred="auto"):
    names = list(ENCODERS) if preferred == "auto" else [preferred]
    factories = {"nvenc": "nvh264enc", "vaapi": "vaapih264enc", "x264": "x264enc"}
    for name in names:
        if Gst.ElementFactory.find(factories[name]):
            return name
    raise RuntimeError("no H.264 encoder available")


class Streamer:
    def __init__(self, source, host, width, height, fps, bitrate, encoder,
                 port=19000, bind_port=19000, preview=True):
        """source: a PipeWire node id, or "test" for a moving test pattern."""
        if source == "test":
            video = (f"videotestsrc is-live=true pattern=ball ! "
                     f"video/x-raw,width={width},height={height},framerate={fps}/1 ! "
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
                     f"video/x-raw,width={width},height={height},max-framerate={fps}/1")
        enc = ENCODERS[encoder].format(bitrate=bitrate, gop=fps * 2)
        self.pipeline = Gst.parse_launch(
            f"{video} ! queue max-size-buffers=1 leaky=downstream ! "
            f"videoconvert n-threads=4 ! video/x-raw,format={ENCODER_INPUT[encoder]} ! "
            f"{enc} ! video/x-h264,profile=high ! h264parse config-interval=-1 ! "
            f"queue max-size-buffers=2 ! mpegtsmux name=mux alignment=7 ! "
            f"rtpmp2tpay pt=33 mtu=1400 ! "
            f"udpsink host={host} port={port} bind-port={bind_port} sync=false async=false")
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

    def force_keyframe(self):
        s = Gst.Structure.new_from_string("GstForceKeyUnit, all-headers=(boolean)true")
        self.pipeline.get_by_name("enc").get_static_pad("sink").send_event(
            Gst.Event.new_custom(Gst.EventType.CUSTOM_DOWNSTREAM, s))
