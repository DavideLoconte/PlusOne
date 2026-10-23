"""WFD source session against a scripted Samsung-like sink."""
import socket
import threading

import pytest

from conftest import free_port, wait_for
from plusone.rtsp import (DEFAULT_MODE, MODES, SERVER, Rtsp, WfdSession, advertised_modes,
                          choose_mode, parse_edid, parse_params, video_formats)

# EDID that a Galaxy Tab S10 FE+ sends to sources it takes for Windows.
SAMSUNG_EDID = (
    "0002 00FFFFFFFFFFFF004C2D00000000000002210103801E147807EE95A"
    "3544C99260F505400000001010101010101010101010101010101343C80A070B"
    "023403020360000000000001A023A801871382D40582C450000000000001E000"
    "000FC005365636F6E6453637265656E0A0000001000000000000000000000000"
    "0000001960203084223090607000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000078"
)
SINK_FORMATS = "00 00 01 01 0001bde1 00300000 000003c0 00 0000 0000 0f none none"

SINK_CAPS = (
    "wfd_video_formats: 00 00 01 01 0001bde1 00300000 000003c0 00 0000 0000 0f none none\r\n"
    "wfd_audio_codecs: LPCM 00000002 00, AAC 00000001 00\r\n"
    "wfd_client_rtp_ports: RTP/AVP/UDP;unicast 19000 0 mode=play\r\n"
    "wfd_uibc_capability: input_category_list=GENERIC, HIDC;generic_cap_list=Keyboard, Mouse, "
    "SingleTouch, MultiTouch;hidc_cap_list=Keyboard/USB, Gesture/USB;port=none\r\n"
    f"wfd_display_edid: {SAMSUNG_EDID}\r\n"
)


def make_edid(width, height, hz):
    """A minimal EDID whose preferred timing is width x height @ hz."""
    hblank, vblank = 160, 35
    clock = round((width + hblank) * (height + vblank) * hz / 10000)
    dtd = bytes([clock & 0xFF, clock >> 8, width & 0xFF, hblank & 0xFF,
                 (width >> 8) << 4 | hblank >> 8, height & 0xFF, vblank & 0xFF,
                 (height >> 8) << 4 | vblank >> 8]) + bytes(10)
    edid = b"\x00\xff\xff\xff\xff\xff\xff\x00" + bytes(46) + dtd + bytes(56)
    return "0001 " + edid.hex()


def test_parse_samsung_edid():
    assert parse_edid(SAMSUNG_EDID) == (1920, 1200, 60)


@pytest.mark.parametrize("value", ["none", "", "0001 zz", "0001 00ff", "0001 " + "00" * 128])
def test_parse_edid_rejects_garbage(value):
    assert parse_edid(value) is None


def test_advertised_modes_from_cea_bitmap():
    assert set(advertised_modes(SINK_FORMATS)) == {(1920, 1080, 60), (1920, 1080, 30), (1280, 720, 60)}


@pytest.mark.parametrize("edid, expected", [
    (SAMSUNG_EDID, (1920, 1200, 60)),              # preferred timing we support
    (make_edid(1680, 1050, 60), (1680, 1050, 60)),
    (make_edid(2880, 1800, 60), (1920, 1080, 60)),  # unsupported: largest advertised
    ("none", (1920, 1080, 60)),                     # no EDID shared
])
def test_choose_mode(edid, expected):
    assert choose_mode({"wfd_display_edid": edid, "wfd_video_formats": SINK_FORMATS}) == expected


def test_choose_mode_without_caps():
    assert choose_mode({}) == DEFAULT_MODE


def test_video_formats_vesa_1920x1200():
    fields = video_formats(1920, 1200, 60).split()
    assert fields[4] == "00000000"   # CEA
    assert fields[5] == "20000000"   # VESA bit 29
    assert fields[2] == "02"         # constrained high profile


def test_video_formats_cea_1080p60():
    fields = video_formats(1920, 1080, 60).split()
    assert fields[4] == "00000100" and fields[5] == "00000000"


@pytest.mark.parametrize("mode", list(MODES))
def test_every_mode_sets_exactly_one_bit(mode):
    fields = video_formats(*mode).split()
    bits = bin(int(fields[4], 16)).count("1") + bin(int(fields[5], 16)).count("1")
    assert bits == 1


def test_parse_params():
    params = parse_params(SINK_CAPS)
    assert params["wfd_display_edid"] == SAMSUNG_EDID
    assert params["wfd_uibc_capability"].endswith("port=none")


def run_sink(sock, log):
    """Play the sink side of M1-M7, an IDR request and a teardown."""
    rtsp = Rtsp(sock)

    def reply_ok(headers, body=""):
        rtsp.send("RTSP/1.0 200 OK", {"CSeq": headers["cseq"]}, body)

    first, headers, _ = rtsp.read()                                   # M1
    log.append(("M1", first))
    reply_ok(headers)
    rtsp.request("OPTIONS", "*", extra={"Require": "org.wfa.wfd1.0"})  # M2
    first, headers, _ = rtsp.read()
    log.append(("M2 reply", headers.get("server")))
    first, headers, body = rtsp.read()                                # M3
    log.append(("M3", body))
    reply_ok(headers, SINK_CAPS)
    first, headers, body = rtsp.read()                                # M4
    log.append(("M4", body))
    reply_ok(headers)
    first, headers, body = rtsp.read()                                # M5
    log.append(("M5", body))
    reply_ok(headers)
    rtsp.request("SETUP", "rtsp://src/wfd1.0/streamid=0",
                 extra={"Transport": "RTP/AVP/UDP;unicast;client_port=19000-19001"})
    first, headers, _ = rtsp.read()
    log.append(("SETUP reply", headers))
    session = headers["session"].split(";")[0]
    rtsp.request("PLAY", "rtsp://src/wfd1.0/streamid=0", extra={"Session": session})
    rtsp.read()
    rtsp.request("SET_PARAMETER", "rtsp://localhost/wfd1.0", "wfd_idr_request\r\n",
                 {"Session": session})
    rtsp.read()
    rtsp.request("TEARDOWN", "rtsp://localhost/wfd1.0", extra={"Session": session})
    first, _, _ = rtsp.read()
    log.append(("TEARDOWN reply", first))


def test_session_handshake():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    sink_sock = socket.create_connection(srv.getsockname())
    conn, _ = srv.accept()
    log, events = [], []
    sink = threading.Thread(target=run_sink, args=(sink_sock, log), daemon=True)
    sink.start()
    session = WfdSession(conn, on_play=lambda s: events.append("play"),
                         on_idr=lambda: events.append("idr"),
                         on_close=lambda: events.append("close"),
                         mode=None, uibc_port=50123,
                         rtcp_port=free_port(socket.SOCK_DGRAM))
    threading.Thread(target=session.run, daemon=True).start()
    assert wait_for(lambda: "close" in events), log
    sink.join(2)
    steps = dict((k, v) for k, v in log)

    assert steps["M1"].startswith("OPTIONS * RTSP/1.0")
    # Samsung's sink picks its PC profile (and EDID) from this header.
    assert steps["M2 reply"] == SERVER
    assert "wfd_display_edid" in steps["M3"] and "wfd_uibc_capability" in steps["M3"]
    m4 = parse_params(steps["M4"])
    # Automatic mode: the preferred timing from the tablet's EDID.
    assert session.mode == (1920, 1200, 60)
    assert m4["wfd_video_formats"] == video_formats(1920, 1200, 60)
    assert "wfd_audio_codecs" not in m4          # video-only: no A/V sync on the sink
    assert "hidc_cap_list=Gesture/USB" in m4["wfd_uibc_capability"]
    assert m4["wfd_uibc_capability"].endswith("port=50123")
    assert m4["wfd_uibc_setting"] == "enable"
    assert steps["M5"].strip() == "wfd_trigger_method: SETUP"
    assert "server_port=19000-19001" in steps["SETUP reply"]["transport"]
    assert steps["TEARDOWN reply"] == "RTSP/1.0 200 OK"
    assert events == ["play", "idr", "close"]


def test_no_uibc_when_sink_lacks_it():
    session = WfdSession.__new__(WfdSession)
    session.mode, session.uibc_port, session.our_ip = (1920, 1200, 60), 50000, "1.2.3.4"
    session.caps = {"wfd_uibc_capability": "none"}
    assert "wfd_uibc" not in session._m4()
