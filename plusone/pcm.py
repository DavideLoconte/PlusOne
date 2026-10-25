"""The PC's sound to the tablet's speakers, as it is: 16-bit stereo PCM at
48 kHz (no codec at all), in UDP packets of 5 ms sealed with AES-GCM under a
key the paired link hands over (appdisplay.Channel). The app plays it with a
plain media AudioTrack, as the tablet's own players do (PcmPlayback.kt).
libwebrtc, which played it before, is made for calls: its jitter buffer
stretched and squeezed the music to keep the delay down, and it sounded so.

Shaped for devices.Feed as an rtc.Receiver is: offer() and answer() carry
the session's key and ports (JSON) where WebRTC's carry SDP; failed(),
losses() (here: packets sent) and close().
"""
import json
import os
import struct
import threading

from gi.repository import Gst

from . import log
from .appdisplay import Channel

PCM = 7                  # the packets' kind (after appdisplay's)
STATS = 5                # the app's report (appdisplay.STATS's number, its own layout)
RATE, CHANNELS = 48000, 2
FRAMES = 240             # per packet: 5 ms
BYTES = FRAMES * CHANNELS * 2
HEADER = struct.Struct("!BI")  # kind, packet number


class PcmSender:
    kind = "speakers"

    def __init__(self, source):
        self.key = os.urandom(32)
        self.channel = Channel(self.key)
        self.peer = None      # the app's address: set by Feed from the paired link
        self.broken = None
        self.sent = 0
        self._pending = b""
        self._lock = threading.Lock()
        self._running = True
        self.pipeline = Gst.parse_launch(
            f"{source} ! audioconvert ! audioresample "
            f"! audio/x-raw,format=S16LE,rate={RATE},channels={CHANNELS},layout=interleaved "
            f"! appsink name=out emit-signals=true sync=false max-buffers=0")
        self._sink = self.pipeline.get_by_name("out")
        self._handler = self._sink.connect("new-sample", self._send)
        self._listener = threading.Thread(target=self._listen, daemon=True)

    def offer(self):
        return json.dumps({"key": self.key.hex(), "port": self.channel.port, "rate": RATE,
                           "channels": CHANNELS, "frames": FRAMES})

    def answer(self, text):
        """The app's answer: its port (JSON)."""
        reply = json.loads(text)
        port = int(reply["port"])
        if not self.peer or not 0 < port < 65536:
            raise ValueError("the app's answer is not one")
        self.channel.app = (self.peer, port)
        self.pipeline.set_state(Gst.State.PLAYING)
        self._listener.start()

    def _send(self, appsink):
        """Streaming thread: the sound cut into 5 ms packets, out at once."""
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.EOS
        buf = sample.get_buffer()
        with self._lock:
            data = self._pending + buf.extract_dup(0, buf.get_size())
            whole = len(data) - len(data) % BYTES
            for at in range(0, whole, BYTES):
                self.channel.send(HEADER.pack(PCM, self.sent & 0xFFFFFFFF) + data[at:at + BYTES])
                self.sent += 1
            self._pending = data[whole:]
        return Gst.FlowReturn.OK

    def _listen(self):
        """The app's reports, for the log: how much it holds, what it missed."""
        while self._running:
            body = self.channel.receive(1.0)
            if body and body[0] == STATS and len(body) == 13:
                held, missed, late = struct.unpack_from("!III", body, 1)
                if missed or late:
                    log(f"speakers: the tablet holds {held / RATE * 1000:.0f} ms, "
                        f"missed {missed} packets, {late} came late")

    def losses(self):
        """(packets sent, 0, 0, 0): the sound goes as long as this grows."""
        return self.sent, 0, 0, 0

    def fail(self, reason):
        self.broken = self.broken or reason

    def failed(self):
        bus = self.pipeline.get_bus()
        while (message := bus.pop()) is not None:
            if message.type == Gst.MessageType.ERROR and not self.broken:
                error, _debug = message.parse_error()
                self.broken = f"{message.src.get_name()}: {error.message}"
        return self.broken

    def close(self):
        if not self._running:
            return
        self._running = False
        self.pipeline.set_state(Gst.State.NULL)
        self._sink.disconnect(self._handler)
        if self._listener.is_alive():
            self._listener.join(timeout=2)
        self.channel.close()
