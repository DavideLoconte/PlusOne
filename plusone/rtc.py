"""The tablet's camera and microphone over WebRTC (android/.../Rtc.kt): the
PC offers to receive, over the paired TLS link (link.py), which vouches
for the DTLS fingerprints in the offer and the answer; the app answers and
sends. webrtcbin does the rest: ICE (host addresses: the same network),
DTLS-SRTP, the jitter buffer, retransmissions and key frame requests, and
the transport-wide feedback the app's congestion control works from.
"""
import base64
import hashlib
import time

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gst, GstSdp, GstWebRTC  # noqa: E402

from . import log  # noqa: E402

TWCC = "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01"
CAPS = {
    # opus/48000/2 whatever the channels (RFC 7587): libwebrtc rejects the
    # medium otherwise, and gathers no candidates for it.
    # In-band FEC: a lost packet rebuilt from the next one (libwebrtc adds it
    # as losses are reported), before concealment has to guess.
    "audio": ("application/x-rtp,media=audio,encoding-name=OPUS,payload=111,clock-rate=48000,"
              "encoding-params=(string)2,useinbandfec=(string)1,"
              f"rtcp-fb-transport-cc=(boolean)true,extmap-3=(string){TWCC}"),
    # H.264, which the tablet encodes in hardware; no orientation extension,
    # so libwebrtc turns the frames upright itself.
    "video": ("application/x-rtp,media=video,encoding-name=H264,payload=102,clock-rate=90000,"
              "packetization-mode=(string)1,profile-level-id=(string)42e01f,"
              "rtcp-fb-nack=(boolean)true,rtcp-fb-nack-pli=(boolean)true,rtcp-fb-ccm-fir=(boolean)true,"
              f"rtcp-fb-transport-cc=(boolean)true,extmap-3=(string){TWCC}"),
}
SSRC = {"audio": 0x504C5341, "video": 0x504C5356}  # "PLSA", "PLSV"
# ms webrtcbin's jitter buffer holds: sound as WebRTC's NetEq aims for; a
# video key frame takes longer over Wi-Fi, and what comes after this is dropped.
JITTER = {"audio": 60, "video": 150}
GATHER = 3.0    # seconds for ICE's candidates, at most: host ones come at once
BUNDLE_MAX = 3  # GstWebRTCBundlePolicy max-bundle


def local_only(webrtc):
    """The app is on the same network: UDP host addresses are all it takes.
    libnice's UPnP asked the router to open the port to the internet (and
    looked for one on every call); TCP candidates were only more sockets."""
    ice = webrtc.get_property("ice-agent")
    if ice.ref_count == 1:
        # GStreamer 1.24's getter hands over webrtcbin's own reference: the
        # agent went with this wrapper, under webrtcbin. One back for it.
        ice._ref()
    ice.set_property("ice-tcp", False)
    ice.get_property("agent").set_property("upnp", False)


class Receiver:
    """One device's webrtcbin, receiving: on_pad(receiver, pad) links what
    it decodes once the media come."""

    def __init__(self, kind, on_pad):
        self.kind = kind
        self.broken = None
        self.on_pad = on_pad
        self.fingerprint = None  # the app's DTLS certificate's, from its answer
        self.pipeline = Gst.Pipeline.new(f"plusone-{kind}")
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "webrtc")
        self.webrtc.set_property("bundle-policy", BUNDLE_MAX)
        self.webrtc.set_property("latency", JITTER[kind])
        local_only(self.webrtc)
        self.pipeline.add(self.webrtc)
        # Disconnected in close(): a handler holding this Receiver kept it,
        # its webrtcbin, ICE agent and sockets (about 56 fds) alive for good.
        self._handlers = [self.webrtc.connect("pad-added", self._pad_added)]
        for state in ("connection-state", "ice-connection-state"):
            self._handlers.append(self.webrtc.connect(f"notify::{state}", lambda w, spec: log(
                f"{kind}: WebRTC {spec.name}", w.get_property(spec.name).value_nick)))
        # An SSRC, or webrtcbin makes no offer (1.24); this side's, for its RTCP.
        self.transceiver = self.webrtc.emit("add-transceiver", GstWebRTC.WebRTCRTPTransceiverDirection.RECVONLY,
                                            Gst.Caps.from_string(f"{CAPS[kind]},ssrc=(uint){SSRC[kind]}"))
        if kind == "video":
            self.transceiver.set_property("do-nack", True)  # lost packets asked for again
        self.pipeline.set_state(Gst.State.PLAYING)

    def _pad_added(self, _webrtc, pad):
        # The media come once DTLS is done: from the app only if its
        # certificate is the one its answer named (the paired link vouches
        # for the answer; webrtcbin itself checks no fingerprint), else
        # whoever won the handshake on the network would be the camera.
        problem = self._peer_problem()
        if problem:
            log(f"{self.kind}: {problem}")
            self.fail(problem)
            return
        self.on_pad(self, pad)

    def _peer_problem(self):
        return certificate_problem(self.transceiver.get_property("receiver").get_property("transport"),
                                   self.fingerprint)

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
        sdp, self.fingerprint = answer_fingerprint(text, self.kind)
        applied = Gst.Promise.new()
        self.webrtc.emit("set-remote-description",
                         GstWebRTC.WebRTCSessionDescription.new(GstWebRTC.WebRTCSDPType.ANSWER, sdp), applied)
        applied.wait()
        if self.webrtc.get_property("remote-description") is None:
            raise ValueError("webrtcbin did not take the app's answer")

    def losses(self):
        """(packets received, lost, retransmissions asked for, key frames asked
        for) so far, from webrtcbin's statistics."""
        promise = Gst.Promise.new()
        self.webrtc.emit("get-stats", None, promise)
        promise.wait()
        reply = promise.get_reply()
        received = lost = nacks = plis = 0
        if reply is not None:
            def each(_field, value):
                nonlocal received, lost, nacks, plis
                if isinstance(value, Gst.Structure) and value.get_value("type") == GstWebRTC.WebRTCStatsType.INBOUND_RTP:
                    received += value.get_value("packets-received") or 0
                    lost += max(0, value.get_value("packets-lost") or 0)
                    nacks += value.get_value("nack-count") or 0
                    plis += value.get_value("pli-count") or 0
                return True
            reply.foreach(each)
        return received, lost, nacks, plis

    def fail(self, reason):
        """What links the media found it could not: the session is over."""
        self.broken = self.broken or reason

    def failed(self):
        """Why the session is over, or None: the connection to the tablet
        failed or was lost, or what plays the media broke (its error, from
        the bus, which is read here: else its messages pile up)."""
        bus = self.pipeline.get_bus()
        while (message := bus.pop()) is not None:
            if message.type == Gst.MessageType.ERROR and not self.broken:
                error, _debug = message.parse_error()
                self.broken = f"{message.src.get_name()}: {error.message}"
        if self.broken:
            return self.broken
        state = self.webrtc.get_property("connection-state")
        if state in (GstWebRTC.WebRTCPeerConnectionState.FAILED, GstWebRTC.WebRTCPeerConnectionState.CLOSED):
            return f"WebRTC {state.value_nick}"
        return None

    def close(self):
        if self.pipeline is None:
            return
        self.pipeline.set_state(Gst.State.NULL)
        for handler in self._handlers:
            self.webrtc.disconnect(handler)
        self._handlers = []
        self.on_pad = None
        self.transceiver = self.webrtc = self.pipeline = None


def certificate_problem(transport, fingerprint):
    """Why the DTLS peer on transport is not the app whose answer named
    fingerprint, or None."""
    pem = transport.get_property("remote-certificate") if transport else None
    if not pem or not fingerprint:
        return "no certificate to check the media's other end by"
    body = "".join(line for line in pem.strip().splitlines() if not line.startswith("-----"))
    digest = hashlib.sha256(base64.b64decode(body)).hexdigest().upper()
    if ":".join(digest[i:i + 2] for i in range(0, len(digest), 2)) != fingerprint:
        return "the media's other end has another certificate than the app's"
    return None


def answer_fingerprint(text, kind):
    """(SDP message, the one SHA-256 fingerprint) of the app's answer, which
    has this one medium; ValueError if it is not one."""
    result, sdp = GstSdp.SDPMessage.new_from_text(text)
    # GStreamer's parser takes about anything: an answer has this one medium.
    if result != GstSdp.SDPResult.OK or sdp.medias_len() != 1 or sdp.get_media(0).get_media() != kind:
        raise ValueError("the app's answer is not one")
    fingerprints = {line.split(None, 1)[1].strip().upper() for line in text.splitlines()
                    if line.lower().startswith("a=fingerprint:sha-256 ") and len(line.split(None, 1)) == 2}
    if len(fingerprints) != 1:
        raise ValueError("the app's answer has no one SHA-256 fingerprint")
    return sdp, fingerprints.pop()


def link(pipeline, pad, description):
    """pad into a bin made from description, added to pipeline: the bin."""
    chain = Gst.parse_bin_from_description(description, True)
    pipeline.add(chain)
    chain.sync_state_with_parent()
    pad.link(chain.get_static_pad("sink"))
    return chain
