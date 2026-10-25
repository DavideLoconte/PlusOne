"""The PC's side of WebRTC: an offer to receive the tablet's camera or microphone."""
import pytest

from plusone import devices

rtc = devices.webrtc()
if rtc is None:
    pytest.skip("no WebRTC for GStreamer here (gstreamer1.0-nice, gir1.2-gst-plugins-bad-1.0)",
                allow_module_level=True)


@pytest.mark.parametrize("kind, codec", [("audio", "OPUS/48000/2"), ("video", "H264/90000")])
def test_an_offer_to_receive(kind, codec):
    receiver = rtc.Receiver(kind, lambda receiver, pad: None)
    try:
        offer = receiver.offer()
    finally:
        receiver.close()
    assert f"m={kind} " in offer and "a=recvonly" in offer and codec in offer
    assert "a=fingerprint:sha-256" in offer and "transport-wide-cc" in offer
    assert "a=candidate:" in offer  # gathered: no trickling over the paired link
    if kind == "video":
        assert "nack pli" in offer and "packetization-mode=1" in offer


def test_an_answer_that_is_not_sdp():
    receiver = rtc.Receiver("audio", lambda receiver, pad: None)
    try:
        receiver.offer()
        with pytest.raises(ValueError):
            receiver.answer("not SDP at all")
    finally:
        receiver.close()


def test_what_breaks_in_the_pipeline_ends_the_session():
    from gi.repository import GLib, Gst
    receiver = rtc.Receiver("audio", lambda receiver, pad: None)
    try:
        assert receiver.failed() is None
        error = GLib.Error.new_literal(Gst.ResourceError.quark(), "no stream", Gst.ResourceError.FAILED)
        receiver.pipeline.post_message(Gst.Message.new_error(receiver.webrtc, error, "debug"))
        assert "no stream" in receiver.failed()
        receiver.fail("could not link")  # the first reason stays
        assert "no stream" in receiver.failed()
    finally:
        receiver.close()


def test_receivers_leave_nothing_behind():
    # Each session's webrtcbin, ICE agent and sockets go with it (a signal
    # handler holding the Receiver once kept about 56 fds a session).
    import gc
    import os

    def fds():
        return len(os.listdir("/proc/self/fd"))
    rtc.Receiver("audio", lambda receiver, pad: None).close()  # what is made once per process
    gc.collect()
    before = fds()
    for _ in range(4):
        receiver = rtc.Receiver("audio", lambda receiver, pad: None)
        receiver.offer()
        receiver.close()
    del receiver
    gc.collect()
    assert fds() - before < 8


def test_media_only_from_the_certificate_the_answer_names():
    import base64
    import hashlib
    import types
    der = b"the app's certificate"
    pem = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(der).decode() + "\n-----END CERTIFICATE-----\n"
    digest = hashlib.sha256(der).hexdigest().upper()
    fingerprint = ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    linked = []
    receiver = rtc.Receiver("audio", lambda receiver, pad: linked.append(pad))
    try:
        transport = types.SimpleNamespace(get_property=lambda name: pem)
        receiver.transceiver = types.SimpleNamespace(
            get_property=lambda name: types.SimpleNamespace(get_property=lambda name: transport))
        receiver.fingerprint = fingerprint
        receiver._pad_added(None, "pad")
        assert linked == ["pad"] and receiver.broken is None
        receiver.fingerprint = "00:" + fingerprint[3:]
        receiver._pad_added(None, "pad 2")
        assert linked == ["pad"] and "another certificate" in receiver.broken
    finally:
        receiver.transceiver = None
        receiver.pipeline.set_state(rtc.Gst.State.NULL)
