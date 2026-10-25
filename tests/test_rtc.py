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
