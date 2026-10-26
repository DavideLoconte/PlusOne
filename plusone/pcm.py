"""The PC's sound to the tablet's speakers, as it is: 16-bit stereo PCM at
48 kHz (no codec at all), in UDP packets of 5 ms sealed with AES-GCM under a
key the paired link hands over (appdisplay.Channel). The app plays it with a
plain media AudioTrack, as the tablet's own players do (PcmPlayback.kt).
libwebrtc, which played it before, is made for calls: its jitter buffer
stretched and squeezed the music to keep the delay down, and it sounded so.

Digital silence (a player paused, its stream left open: a browser's, a
chat program's) goes for a second, then nothing until there is sound again:
the app pauses meanwhile and starts again with its little wait.

Shaped for devices.Feed as an rtc.Receiver is: offer() and answer() carry
the session's key and ports (JSON) where WebRTC's carry SDP; failed(),
losses() (here: the sound captured) and close().
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
SILENCE = bytes(BYTES)
QUIET_AFTER = 200        # packets of silence in a row (a second) after which none go
STATS_EVERY = 30         # the app's reports (one a second) logged: one in so many, and each with a loss


class PcmSender:
    def __init__(self, source):
        self.key = os.urandom(32)
        self.channel = Channel(self.key)
        self.peer = None      # the app's address: set by Feed from the paired link
        self.broken = None
        self.sent = 0
        self.captured = 0     # buffers of sound from PipeWire
        self._quiet = 0       # packets of silence in a row
        self._pending = b""
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
        self.captured += 1
        data = self._pending + buf.extract_dup(0, buf.get_size())
        whole = len(data) - len(data) % BYTES
        for at in range(0, whole, BYTES):
            packet = data[at:at + BYTES]
            if packet != SILENCE:
                self._quiet = 0
            elif self._quiet >= QUIET_AFTER:
                continue
            else:
                self._quiet += 1
            self.channel.send(HEADER.pack(PCM, self.sent & 0xFFFFFFFF) + packet)
            self.sent += 1
        self._pending = data[whole:]
        return Gst.FlowReturn.OK

    def _listen(self):
        """The app's reports, for the log: how much it holds, what it missed,
        how far behind the PC its speaker is (an app before 0.9: 13 bytes)."""
        reports = 0
        while self._running:
            body = self.channel.receive(1.0)
            if not body or body[0] != STATS or len(body) not in (13, 21):
                continue
            held, missed, late, dropped, behind = (struct.unpack_from("!IIIII", body, 1) if len(body) == 21
                                                   else struct.unpack_from("!III", body, 1) + (0, 0))
            reports += 1
            if missed or late or reports % STATS_EVERY == 1:
                log(f"speakers: the tablet holds {held / RATE * 1000:.0f} ms"
                    + (f", its speaker is {behind / RATE * 1000:.0f} ms behind" if behind else "")
                    + f"; missed {missed} packets, {late} came late"
                    + (f", {dropped} nearly silent left out" if dropped else ""))

    def losses(self):
        """(buffers captured, 0, 0, 0): the sound goes as long as this grows,
        silence too (not sent, but still coming from PipeWire)."""
        return self.captured, 0, 0, 0

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
