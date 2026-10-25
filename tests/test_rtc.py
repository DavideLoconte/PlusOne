"""The PC's side of WebRTC: an offer to receive the tablet's camera or microphone."""
import pytest

from plusone import devices

rtc = devices.webrtc()
if rtc is None:
    pytest.skip("no WebRTC for GStreamer here (gstreamer1.0-nice, gir1.2-gst-plugins-bad-1.0)",
                allow_module_level=True)


@pytest.mark.parametrize("kind, codec", [("audio", "OPUS/48000/2"), ("video", "H264/90000")])
def test_an_offer_to_receive(kind, codec):
    receiver = rtc.Receiver(kind, lambda pipeline, pad: None)
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
    receiver = rtc.Receiver("audio", lambda pipeline, pad: None)
    try:
        receiver.offer()
        with pytest.raises(ValueError):
            receiver.answer("not SDP at all")
    finally:
        receiver.close()
