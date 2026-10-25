"""The tablet's camera and microphone over WebRTC (android/.../Rtc.kt): the
PC offers to receive, over the paired TLS link (companion.py), which vouches
for the DTLS fingerprints in the offer and the answer; the app answers and
sends. webrtcbin does the rest: ICE (host addresses: the same network),
DTLS-SRTP, the jitter buffer, retransmissions and key frame requests, and
the transport-wide feedback the app's congestion control works from.
"""
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC  # noqa: E402

TWCC = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
CAPS = {
    "audio": ("application/x-rtp,media=audio,encoding-name=OPUS,payload=111,clock-rate=48000,"
              f"rtcp-fb-transport-cc=(boolean)true,extmap-3=(string){TWCC}"),
    # H.264, which the tablet encodes in hardware; no orientation extension,
    # so libwebrtc turns the frames upright itself.
    "video": ("application/x-rtp,media=video,encoding-name=H264,payload=102,clock-rate=90000,"
              "packetization-mode=(string)1,profile-level-id=(string)42e01f,"
              "rtcp-fb-nack=(boolean)true,rtcp-fb-nack-pli=(boolean)true,rtcp-fb-ccm-fir=(boolean)true,"
              f"rtcp-fb-transport-cc=(boolean)true,extmap-3=(string){TWCC}"),
}
SSRC = {"audio": 0x504C5341, "video": 0x504C5356}  # "PLSA", "PLSV"
JITTER = 50     # ms webrtcbin's jitter buffer holds (WebRTC's NetEq targets about 60)
GATHER = 3.0    # seconds for ICE's candidates, at most: host ones come at once
BUNDLE_MAX = 3  # GstWebRTCBundlePolicy max-bundle


class Receiver:
    """One device's webrtcbin, receiving: on_pad(pipeline, pad) links what
    it decodes once the media come."""

    def __init__(self, kind, on_pad):
        self.kind = kind
        self.watched = False
        self.pipeline = Gst.Pipeline.new(f"plusone-{kind}")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "webrtc")
        self.webrtc.set_property("bundle-policy", BUNDLE_MAX)
        self.webrtc.set_property("latency", JITTER)
        self.pipeline.add(self.webrtc)
        self.webrtc.connect("pad-added", lambda _webrtc, pad: on_pad(self.pipeline, pad))
        # An SSRC, or webrtcbin makes no offer (1.24); this side's, for its RTCP.
        transceiver = self.webrtc.emit("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
                                       Gst.Caps.from_string(f"{CAPS[kind]},ssrc=(uint){SSRC[kind]}"))
        if kind == "video":
            transceiver.set_property("do-nack", True)  # lost packets asked for again
        self.pipeline.set_state(Gst.State.PLAYING)

    def offer(self):
        """The offer, as SDP text, with this side's ICE candidates in it."""
        created = Gst.Promise.new()
        self.webrtc.emit("create-offer", None, created)
        created.wait()
        # The offer belongs to the promise's reply: both kept until it is set.
        reply = created.get_reply()
        offer = reply.get_value("offer")
        if offer is None:
            raise RuntimeError("webrtcbin made no offer")
        applied = Gst.Promise.new()
        self.webrtc.emit("set-local-description", offer, applied)
        applied.wait()
        deadline = time.monotonic() + GATHER
        while (self.webrtc.get_property("ice-gathering-state") != GstWebRTC.WebRTCICEGatheringState.COMPLETE
               and time.monotonic() < deadline):
            time.sleep(0.02)
        return self.webrtc.get_property("local-description").sdp.as_text()

    def answer(self, text):
        """The app's answer (SDP text)."""
        result, sdp = GstSdp.SDPMessage.new_from_text(text)
        # GStreamer's parser takes about anything: an answer has this one medium.
        if result != GstSdp.SDPResult.OK or sdp.medias_len() != 1 or sdp.get_media(0).get_media() != self.kind:
            raise ValueError("the app's answer is not one")
        applied = Gst.Promise.new()
        self.webrtc.emit("set-remote-description",
                         GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp), applied)
        applied.wait()
        if self.webrtc.get_property("remote-description") is None:
            raise ValueError("webrtcbin did not take the app's answer")

    def watch_bus(self):
        """The pipeline's bus, its messages on the main loop until close()."""
        bus = self.pipeline.get_bus()
        if not self.watched:
            bus.add_signal_watch()
            self.watched = True
        return bus

    def failed(self):
        """Whether the connection to the tablet failed or was lost."""
        return self.webrtc.get_property("connection-state") in (
            GstWebRTC.WebRTCPeerConnectionState.FAILED, GstWebRTC.WebRTCPeerConnectionState.CLOSED)

    def close(self):
        if self.watched:
            self.pipeline.get_bus().remove_signal_watch()
            self.watched = False
        self.pipeline.set_state(Gst.State.NULL)


def link(pipeline, pad, description):
    """pad into a bin made from description, added to pipeline: the bin."""
    chain = Gst.parse_bin_from_description(description, True)
    pipeline.add(chain)
    chain.sync_state_with_parent()
    pad.link(chain.get_static_pad("sink"))
    return chain
