"""WFD (Miracast) RTSP source session, tuned for Samsung Second Screen sinks."""
import socket
import threading
import time

from . import log

# Samsung's sink classifies the source from its M2 reply. A Windows source
# gets the PC profile and the sink's 16:10 EDID; GStreamer's default server
# string gets the high-latency TV profile.
SERVER = "MSMiracastSource/10.00.26100.1 (Linux)"
# (width, height, fps) -> (table, bit) from the sink's VideoFormats table.
# The sink lays the picture out by the size negotiated here, so the stream
# must match it exactly or it gets letterboxed and squashed.
MODES = {
    (1920, 1080, 60): ("CEA", 8), (1920, 1080, 30): ("CEA", 7),
    (1280, 720, 60): ("CEA", 6),
    (1280, 800, 60): ("VESA", 9), (1440, 900, 60): ("VESA", 19),
    (1680, 1050, 60): ("VESA", 27), (1920, 1200, 60): ("VESA", 29),
    (1920, 1200, 30): ("VESA", 28),
}


def video_formats(width, height, fps):
    table, bit = MODES[(width, height, fps)]
    cea = 1 << bit if table == "CEA" else 0
    vesa = 1 << bit if table == "VESA" else 0
    # native, preferred, profile CHP, level, CEA, VESA, HH, latency, slices...
    return f"00 00 02 01 {cea:08x} {vesa:08x} 00000000 00 0000 0000 00 none none"


class Rtsp:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.cseq = 0
        self.lock = threading.Lock()

    def _fill(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise EOFError("sink closed the connection")
        self.buf += chunk

    def read(self):
        while b"\r\n\r\n" not in self.buf:
            self._fill()
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length", "0"))
        while len(self.buf) < length:
            self._fill()
        body, self.buf = self.buf[:length], self.buf[length:]
        log("<==", lines[0], repr(body.decode("latin1"))[:200])
        return lines[0], headers, body.decode("latin1")

    def send(self, first, headers, body=""):
        msg = first + "\r\n"
        for k, v in headers.items():
            msg += f"{k}: {v}\r\n"
        if body:
            msg += "Content-Type: text/parameters\r\n"
            msg += f"Content-Length: {len(body)}\r\n"
        msg += "\r\n" + body
        with self.lock:
            self.sock.sendall(msg.encode("latin1"))

    def request(self, method, url, body="", extra=None):
        with self.lock:
            self.cseq += 1
            cseq = self.cseq
        headers = {"CSeq": cseq}
        headers.update(extra or {})
        log("==>", method, repr(body)[:200])
        self.send(f"{method} {url} RTSP/1.0", headers, body)
        return cseq


def parse_params(body):
    params = {}
    for line in body.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            params[k.strip()] = v.strip()
    return params


class WfdSession:
    """Runs one sink connection; callbacks fire on the session thread."""

    def __init__(self, conn, on_play, on_idr, on_close, mode=(1920, 1200, 60), uibc_port=None,
                 sink_ip=None):
        self.conn = conn
        self.rtsp = Rtsp(conn)
        self.on_play = on_play
        self.on_idr = on_idr
        self.on_close = on_close
        self.uibc_port = uibc_port
        self.mode = mode
        # Over an adb tunnel the RTSP peer is 127.0.0.1; RTP then goes to the
        # tablet's LAN address instead.
        self.sink_ip = sink_ip or conn.getpeername()[0]
        self.our_ip = conn.getsockname()[0]
        self.session_id = "SS%08d" % (int(time.time()) % 10**8)
        self.caps = {}
        self.stop_event = threading.Event()

    def _m4(self):
        lines = [
            f"wfd_video_formats: {video_formats(*self.mode)}",
            f"wfd_presentation_URL: rtsp://{self.our_ip}/wfd1.0/streamid=0 none",
            "wfd_client_rtp_ports: RTP/AVP/UDP;unicast 19000 0 mode=play",
        ]
        # The sink's answer ends in "port=none" (the source picks the port),
        # so only a bare "none" means no UIBC support.
        if self.uibc_port and self.caps.get("wfd_uibc_capability", "none") != "none":
            # Fingers arrive as generic touch events; the S Pen only as an HIDC
            # "Gesture" device (libsecuibc: "Source Device supports HIDC Gesture(pen)").
            lines += [
                "wfd_uibc_capability: input_category_list=GENERIC, HIDC;"
                "generic_cap_list=Mouse, SingleTouch, MultiTouch, Keyboard;"
                f"hidc_cap_list=Gesture/USB;port={self.uibc_port}",
                "wfd_uibc_setting: enable",
            ]
        return "\r\n".join(lines) + "\r\n"

    def _keepalive(self):
        while not self.stop_event.wait(20):
            self.pending[self.rtsp.request("GET_PARAMETER", "rtsp://localhost/wfd1.0",
                                           extra={"Session": self.session_id})] = "M16"

    def _rtcp_sink(self):
        # The sink sends RTCP receiver reports to server_port + 1; with no
        # socket there the PC answers port-unreachable and the sink tears down.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 19001))
        sock.settimeout(1)
        last_lost = 0
        while not self.stop_event.is_set():
            try:
                pkt, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            if len(pkt) >= 32 and pkt[1] == 201:
                lost = int.from_bytes(pkt[13:16], "big", signed=True)
                if 0 <= last_lost < lost:
                    log(f"RTCP: {lost - last_lost} packets lost")
                last_lost = lost
        sock.close()

    def run(self):
        threading.Thread(target=self._rtcp_sink, daemon=True).start()
        rtsp = self.rtsp
        self.pending = {rtsp.request("OPTIONS", "*", extra={"Require": "org.wfa.wfd1.0"}): "M1"}
        m3_query = ("wfd_video_formats\r\nwfd_audio_codecs\r\nwfd_client_rtp_ports\r\n"
                    "wfd_uibc_capability\r\nwfd_display_edid\r\nwfd_sec_sink_resolution\r\n")
        state = {"m1": False, "m2": False}

        def send_m3():
            self.pending[rtsp.request("GET_PARAMETER", "rtsp://localhost/wfd1.0", m3_query)] = "M3"

        try:
            while not self.stop_event.is_set():
                first, headers, body = rtsp.read()
                if first.startswith("RTSP/1.0"):
                    step = self.pending.pop(int(headers.get("cseq", "-1")), "?")
                    if not first.startswith("RTSP/1.0 200"):
                        log(f"{step} rejected: {first}")
                    if step == "M1":
                        state["m1"] = True
                        if state["m2"]:
                            send_m3()
                    elif step == "M3":
                        self.caps = parse_params(body)
                        self.pending[rtsp.request("SET_PARAMETER", "rtsp://localhost/wfd1.0",
                                                  self._m4())] = "M4"
                    elif step == "M4":
                        self.pending[rtsp.request("SET_PARAMETER", "rtsp://localhost/wfd1.0",
                                                  "wfd_trigger_method: SETUP\r\n")] = "M5"
                    continue

                method = first.split()[0]
                reply = {"CSeq": headers.get("cseq", "0")}
                if method == "OPTIONS":
                    reply["Public"] = ("org.wfa.wfd1.0, SETUP, TEARDOWN, PLAY, PAUSE, "
                                       "GET_PARAMETER, SET_PARAMETER")
                    reply["Server"] = SERVER
                    rtsp.send("RTSP/1.0 200 OK", reply)
                    # Query capabilities only after the sink has seen our Server
                    # header: it withholds its EDID from non-Windows sources.
                    state["m2"] = True
                    if state["m1"]:
                        send_m3()
                    continue
                if method == "SETUP":
                    reply["Session"] = f"{self.session_id};timeout=30"
                    reply["Transport"] = "RTP/AVP/UDP;unicast;client_port=19000;server_port=19000-19001"
                elif method == "PLAY":
                    reply["Session"] = self.session_id
                    rtsp.send("RTSP/1.0 200 OK", reply)
                    self.on_play(self)
                    threading.Thread(target=self._keepalive, daemon=True).start()
                    continue
                elif method == "SET_PARAMETER" and "wfd_idr_request" in body:
                    self.on_idr()
                elif method == "TEARDOWN":
                    rtsp.send("RTSP/1.0 200 OK", reply)
                    break
                rtsp.send("RTSP/1.0 200 OK", reply)
        except (EOFError, ConnectionError, OSError) as e:
            log("session ended:", e)
        finally:
            self.stop_event.set()
            self.on_close()

    def stop(self):
        if not self.stop_event.is_set():
            try:
                self.rtsp.request("SET_PARAMETER", "rtsp://localhost/wfd1.0",
                                  "wfd_trigger_method: TEARDOWN\r\n", {"Session": self.session_id})
            except OSError:
                pass
        self.stop_event.set()
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
