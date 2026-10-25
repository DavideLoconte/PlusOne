"""The tablet's camera and microphone as the PC's own: a webcam ("Plus One
tablet camera", v4l2loopback's device) and a microphone ("Tablet
microphone", a PipeWire source fed through a pipe), each switched on in the
window. Nothing goes over the network until a program on the PC uses one:
then the tablet in use is asked for it (companion.capture), captures and
encodes it there (H.264, Opus), and stops when the program lets go.
"""
import array
import ctypes
import fcntl
import glob
import math
import os
import shutil
import struct
import subprocess
import termios
import threading
import time

from gi.repository import Gio, GLib, Gst

from . import companion, log, once

CAMERA_LABEL = "Plus One tablet camera"  # data/plus-one-camera-options.conf
MIC_NAME = "plusone_mic"
QUALITIES = {"720p": (1280, 720, 2_500_000), "1080p": (1920, 1080, 5_000_000)}
FPS = 30
RETRY = 3          # seconds between attempts to reach the tablet while wanted
REFUSED_RETRY = 10  # seconds, after the app said no (the user may change their mind there)
IN_USE_AFTER = 0.5  # seconds a program holds the device before the tablet is asked
IDLE_AFTER = 3      # seconds without one before the tablet stops
MIC_BACKLOG = 48000 * 2 // 10  # bytes in the pipe (100 ms) past which a piece is dropped, not queued
FLIP = {0: "none", 90: "clockwise", 180: "rotate-180", 270: "counterclockwise"}
# Software first: little work at these sizes, and decodebin's pick (NVIDIA's,
# here) held the whole pipeline still until the tablet's first frame.
H264_DECODERS = ("avdec_h264", "openh264dec", "vah264dec")


def h264_decoder():
    return next((name for name in H264_DECODERS if Gst.ElementFactory.find(name)), None)


class Feed:
    """The tablet's pieces for one device, on a thread of their own while
    wanted, connecting again when the link drops. Callbacks run on that
    thread: on_info(what the app sends), on_piece(header, payload),
    on_problem(reason: "off", "permission", "background", "unavailable",
    "away")."""

    def __init__(self, request, where, on_info, on_piece, on_problem):
        self.request = request
        self.where = where  # () -> (addresses, app id) of the tablet in use, or None
        self.on_info, self.on_piece, self.on_problem = on_info, on_piece, on_problem
        self._wanted = threading.Event()
        self._sock = None
        self._thread = None

    def start(self):
        if self._wanted.is_set():
            return
        self._wanted.set()
        self._thread = threading.Thread(target=self._run, name=f"plusone-{self.request['type']}", daemon=True)
        self._thread.start()

    def stop(self):
        self._wanted.clear()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(2)
            except OSError:
                pass

    def _wait(self, seconds):
        end = time.monotonic() + seconds
        while self._wanted.is_set() and time.monotonic() < end:
            time.sleep(0.1)

    def _run(self):
        kind = self.request["type"]
        while self._wanted.is_set():
            where = self.where()
            if not where or not where[0]:
                self.on_problem("away")
                self._wait(RETRY)
                continue
            try:
                sock, wire, info = companion.capture(where[0], self.request, where[1])
            except companion.Refused as e:
                log(f"{kind}: the tablet said no ({e})")
                self.on_problem(str(e))
                # Turned off or not allowed there: the user has to act; else soon.
                self._wait(REFUSED_RETRY if str(e) in ("off", "permission") else RETRY)
                continue
            except Exception as e:  # noqa: BLE001 - not there, or not answering: again in a while
                log(f"{kind}: the tablet's app did not answer:", repr(e))
                self.on_problem("away")
                self._wait(RETRY)
                continue
            self._sock = sock
            log(f"{kind}: from the tablet", {k: v for k, v in info.items() if k != "type"})
            self.on_info(info)
            try:
                while self._wanted.is_set():
                    self.on_piece(*wire.receive_piece())
            except Exception as e:  # noqa: BLE001 - the link went, or it was stopped
                if self._wanted.is_set():
                    log(f"{kind}: lost the tablet:", repr(e))
            finally:
                sock.close()
                self._sock = None
        log(f"{kind}: the tablet stopped")


class Opened:
    """How many times a device file is open now, by anyone, from inotify's
    open and close events; on_change(count) on the main loop. baseline: how
    many of those are Plus One's own."""
    IN_CLOSE_WRITE, IN_CLOSE_NOWRITE, IN_OPEN = 0x08, 0x10, 0x20

    def __init__(self, path, on_change):
        self.on_change = on_change
        self.count = self.baseline = 0
        libc = ctypes.CDLL(None, use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0 or libc.inotify_add_watch(self.fd, path.encode(), self.IN_OPEN | self.IN_CLOSE_WRITE
                                                  | self.IN_CLOSE_NOWRITE) < 0:
            raise OSError(ctypes.get_errno(), "inotify")
        self.source = GLib.io_add_watch(GLib.IOChannel.unix_new(self.fd), GLib.PRIORITY_DEFAULT,
                                        GLib.IOCondition.IN, self._read)

    def drain(self):
        """The events waiting, counted now."""
        self._read(None, None)

    def _read(self, _channel, _condition):
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return True
        offset = 0
        while offset + 16 <= len(data):
            _wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
            offset += 16 + length
            if mask & self.IN_OPEN:
                self.count += 1
            elif mask & (self.IN_CLOSE_WRITE | self.IN_CLOSE_NOWRITE):
                self.count = max(0, self.count - 1)
        self.on_change(self.count)
        return True

    def close(self):
        GLib.source_remove(self.source)
        os.close(self.fd)


def camera_devices():
    """/dev/videoN of Plus One's v4l2loopback devices (data/plus-one-camera-options.conf):
    the one for programs that read cameras themselves, and the one for PipeWire."""
    found = []
    for entry in sorted(glob.glob("/sys/class/video4linux/video*")):
        try:
            with open(os.path.join(entry, "name")) as f:
                if f.read().strip().startswith(CAMERA_LABEL):
                    found.append("/dev/" + os.path.basename(entry))
        except OSError:
            pass
    return found


class Device:
    """What camera and microphone share: on and off with the switch, the
    tablet asked only while a program uses the device, and a state for the
    window: "off", "setup" (missing on this PC), "ready", "in use", or why
    the tablet did not give it ("away", "tablet-off", "tablet-permission"...)."""

    def __init__(self, app, kind):
        self.app = app
        self.kind = kind
        self.state = "off"
        self.feed = None
        self._in_use = False
        self._timer = None

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.app.devices_changed()

    def where(self):
        """The tablet in use's addresses and app id: from the feed's thread."""
        tablet = self.app.tablets.active()
        return (self.app.tablets.addresses(tablet), tablet.app_id) if tablet else None

    def _problem(self, reason):
        """The tablet could not give it: "away", or the app's reason as "tablet-<reason>"."""
        state = reason if reason == "away" else "tablet-" + reason
        GLib.idle_add(once(lambda: self.feed and self._set_state(state)))

    def _used(self, in_use):
        """A program took the device, or let it go: the tablet follows, a little later."""
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add(int(1000 * (IN_USE_AFTER if in_use else IDLE_AFTER)),
                                       once(self._settle, in_use))

    def _settle(self, in_use):
        self._timer = None
        if in_use == self._in_use or self.state in ("off", "setup"):
            return
        self._in_use = in_use
        if in_use:
            log(f"{self.kind}: a program uses it")
            self._set_state("in use")
            self.begin()
        else:
            log(f"{self.kind}: no program uses it")
            self.end()
            self._set_state("ready")

    def begin(self):
        raise NotImplementedError

    def end(self):
        raise NotImplementedError

    def stop_feed(self):
        if self.feed:
            self.feed.stop()
            self.feed = None


class Camera(Device):
    """The webcam: Plus One keeps writing to v4l2loopback's device while the
    switch is on (programs list it only then), black while no program uses
    it, the tablet's camera while one does.

    Two pipelines: the writer only ever sees frames of one format, so the
    device is never asked to change (v4l2loopback refuses while a program
    reads: "busy"); the producer picks black or the tablet's picture, and is
    made again should it break."""

    def __init__(self, app):
        super().__init__(app, "camera")
        self.writers, self.opened = [], []
        self.producer = None
        self.rotation = 0

    def enable(self):
        if self.writers:
            return
        paths = camera_devices()
        self.decoder = h264_decoder()
        if not paths or self.decoder is None:
            self._set_state("setup")
            return
        width, height, _bitrate = QUALITIES.get(self.app.settings.camera_quality, QUALITIES["720p"])
        self.out = (f"video/x-raw,format=YUY2,width={width},height={height},framerate={FPS}/1,"
                    f"pixel-aspect-ratio=1/1,interlace-mode=progressive,colorimetry=bt601,chroma-site=jpeg")
        for path in paths:
            writer = Gst.parse_launch(f"appsrc name=frames is-live=true do-timestamp=true format=time "
                                      f"caps={self.out} ! v4l2sink device={path} sync=false")
            bus = writer.get_bus()
            bus.add_signal_watch()
            bus.connect("message::error", self._error)
            # Watched before the writer opens it, which it keeps open: what is
            # open once it plays is Plus One's, anything more another program's.
            opened = Opened(path, self._opened)
            writer.set_state(Gst.State.PLAYING)
            writer.get_state(2 * Gst.SECOND)
            opened.drain()
            opened.baseline = opened.count
            self.writers.append(writer)
            self.opened.append(opened)
        self.frames = [writer.get_by_name("frames") for writer in self.writers]
        self._produce()
        self._set_state("ready")
        log("camera: on, as", ", ".join(paths))

    def _produce(self):
        """Black, or the tablet's picture once it decodes: into the writer."""
        width, height, _bitrate = QUALITIES.get(self.app.settings.camera_quality, QUALITIES["720p"])
        raw = f"video/x-raw,format=I420,width={width},height={height},framerate={FPS}/1,pixel-aspect-ratio=1/1"
        producer = Gst.parse_launch(
            f"input-selector name=selector ! videoconvert ! {self.out} "
            f"! appsink name=sink emit-signals=true sync=false max-buffers=2 drop=true "
            f"videotestsrc is-live=true pattern=black ! {raw} ! selector.sink_0 "
            f"appsrc name=src is-live=true do-timestamp=true format=time "
            f"caps=video/x-h264,stream-format=byte-stream,alignment=au "
            f"! h264parse ! {self.decoder} ! queue leaky=downstream max-size-buffers=2 "
            f"! videoflip method={FLIP.get(self.rotation, 'none')} name=flip "
            f"! videoconvert ! videoscale add-borders=true ! videorate ! {raw} ! selector.sink_1")
        self.src = producer.get_by_name("src")
        self.flip = producer.get_by_name("flip")
        self.selector = producer.get_by_name("selector")
        self.black, self.live = self.selector.get_static_pad("sink_0"), self.selector.get_static_pad("sink_1")
        self.selector.set_property("active-pad", self.black)
        # The tablet's picture shows once it is decoded, not before (black until then).
        self.live.add_probe(Gst.PadProbeType.BUFFER, self._decoded)
        producer.get_by_name("sink").connect("new-sample", self._frame)
        bus = producer.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._error)
        self.producer = producer
        producer.set_state(Gst.State.PLAYING)

    def _frame(self, sink):
        buffer = sink.emit("pull-sample").get_buffer()
        for frames in self.frames:
            frames.emit("push-buffer", buffer)
        return Gst.FlowReturn.OK

    def disable(self):
        self.stop_feed()
        for opened in self.opened:
            opened.close()
        for pipeline in [self.producer] + self.writers:
            if pipeline:
                pipeline.get_bus().remove_signal_watch()
                pipeline.set_state(Gst.State.NULL)
        self.producer, self.writers, self.opened = None, [], []
        self._in_use = False
        self._set_state("off")

    def _opened(self, _count):
        self._used(any(opened.count > opened.baseline for opened in self.opened))

    def begin(self):
        settings = self.app.settings
        width, height, bitrate = QUALITIES.get(settings.camera_quality, QUALITIES["720p"])
        request = {"type": "camera", "facing": settings.camera_facing, "width": width, "height": height,
                   "fps": FPS, "bitrate": bitrate}
        self.feed = Feed(request, self.where, self._info, self._piece, self._problem)
        self.feed.start()

    def end(self):
        self.stop_feed()
        if self.producer:
            self.selector.set_property("active-pad", self.black)

    def _info(self, info):
        self.rotation = info.get("rotation", 0)
        self.flip.set_property("method", FLIP.get(self.rotation, "none"))
        GLib.idle_add(once(lambda: self.feed and self._set_state("in use")))

    def _piece(self, _header, payload):
        self.src.emit("push-buffer", Gst.Buffer.new_wrapped(payload))

    def _decoded(self, _pad, _info):
        if self.feed and self.selector.get_property("active-pad") != self.live:
            self.selector.set_property("active-pad", self.live)
        return Gst.PadProbeReturn.OK

    def _error(self, bus, message):
        error, _debug = message.parse_error()
        log("camera:", error.message)
        if self.producer and bus is self.producer.get_bus():
            # The tablet's picture broke it: made again, black until the next key frame.
            bus.remove_signal_watch()
            self.producer.set_state(Gst.State.NULL)
            self._produce()


class Microphone(Device):
    """The microphone: a PipeWire source read from a pipe (module-pipe-source),
    which Plus One writes the tablet's sound into while a program records."""

    def __init__(self, app):
        super().__init__(app, "microphone")
        self.module = None
        self.links = set()
        self.watch = None
        self.pipeline = None
        self.pipe = None
        self.fifo = os.path.join(GLib.get_user_runtime_dir(), "plusone-mic.fifo")

    def enable(self, description="Tablet microphone (Plus One)"):
        if self.module:
            return
        if not (shutil.which("pactl") and shutil.which("pw-link")):
            self._set_state("setup")
            return
        unload_stale()
        try:
            out = subprocess.run(["pactl", "load-module", "module-pipe-source", f"source_name={MIC_NAME}",
                                  f"file={self.fifo}", "format=s16le", "rate=48000", "channels=1",
                                  f"source_properties=\"device.description='{description}'\""],
                                 capture_output=True, text=True, timeout=5, check=True).stdout
            self.module = out.strip()
        except (OSError, subprocess.SubprocessError) as e:
            log("microphone: no PipeWire source:", repr(e))
            self._set_state("setup")
            return
        self._watch_links()
        self._set_state("ready")
        log("microphone: on, as", MIC_NAME)

    def disable(self):
        self.end()
        if self.watch:
            self.watch.force_exit()
            self.watch = None
        if self.module:
            subprocess.run(["pactl", "unload-module", self.module], capture_output=True, timeout=5)
            self.module = None
        self.links.clear()
        self._in_use = False
        self._set_state("off")

    # -- who records from it -----------------------------------------------------
    def _watch_links(self):
        """pw-link tells each link made or removed: a program recording from
        the source is a link from its ports."""
        self._parser = LinkParser()
        self.watch = Gio.Subprocess.new(["pw-link", "--monitor", "--links", "--output"],
                                        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        self._lines = Gio.DataInputStream.new(self.watch.get_stdout_pipe())
        self._lines.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, self.watch)

    def _line(self, stream, result, watch):
        try:
            line, _length = stream.read_line_finish_utf8(result)
        except GLib.Error:
            line = None
        if line is None or watch is not self.watch:
            return  # pw-link ended, or the microphone was switched off
        changed = self._parser(line)
        if changed:
            sign, link = changed
            if link[0].startswith(MIC_NAME + ":"):
                (self.links.add if sign in "=+" else self.links.discard)(link)
                self._used(bool(self.links))
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, watch)

    # -- the tablet's sound into the pipe ---------------------------------------
    def begin(self):
        self.feed = Feed({"type": "microphone", "bitrate": 32000}, self.where, self._info, self._piece,
                         self._problem)
        self.feed.start()

    def end(self):
        self.stop_feed()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        if self.pipe is not None:
            os.close(self.pipe)
            self.pipe = None

    def _info(self, info):
        """The app's sound (Opus, or plain samples): decoded into the pipe."""
        caps = ("audio/x-opus,rate=48000,channels=1,channel-mapping-family=0" if info.get("codec") == "opus"
                else "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved")
        decode = "opusdec plc=true ! " if info.get("codec") == "opus" else ""
        pipeline = Gst.parse_launch(
            f"appsrc name=src is-live=true do-timestamp=true format=time caps={caps} ! {decode}"
            f"audioconvert ! audioresample ! audio/x-raw,format=S16LE,rate=48000,channels=1 "
            f"! volume name=gain ! appsink name=out emit-signals=true sync=false")
        pipeline.get_by_name("out").connect("new-sample", self._write)
        # The tablet's microphone is far too quiet as it comes: brought to a voice's level.
        leveller = Leveller()
        gain = pipeline.get_by_name("gain")

        def level(pad, probe):
            buffer = probe.get_buffer()
            gain.set_property("volume", leveller(array.array("h", buffer.extract_dup(0, buffer.get_size()))))
            return Gst.PadProbeReturn.OK
        gain.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, level)
        old, self.pipeline = self.pipeline, pipeline
        if old:
            old.set_state(Gst.State.NULL)
        self.src = pipeline.get_by_name("src")
        pipeline.set_state(Gst.State.PLAYING)
        GLib.idle_add(once(lambda: self.feed and self._set_state("in use")))

    def _piece(self, _header, payload):
        if self.pipeline:
            self.src.emit("push-buffer", Gst.Buffer.new_wrapped(payload))

    def _write(self, sink):
        sample = sink.emit("pull-sample")
        data = sample.get_buffer().extract_dup(0, sample.get_buffer().get_size())
        try:
            if self.pipe is None:
                self.pipe = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            # Behind (the source reads at its own pace): this piece goes, the sound stays live.
            waiting = struct.unpack("i", fcntl.ioctl(self.pipe, termios.FIONREAD, b"\0\0\0\0"))[0]
            if waiting < MIC_BACKLOG:
                os.write(self.pipe, data)
        except BlockingIOError:
            pass
        except OSError as e:
            log("microphone: the pipe:", repr(e))
        return Gst.FlowReturn.OK


class Leveller:
    """A microphone's level kept at a voice's: the gain for each piece of
    16-bit samples, rising slowly while someone speaks, falling at once for
    anything louder, never making a piece clip, and left alone in the quiet
    between words (else it would bring the room's noise up)."""
    TARGET = 3300     # RMS of a voice, about -20 dBFS
    FLOOR = 120       # RMS below which it is the room, not a voice
    MAX_GAIN = 12.0   # +21.6 dB
    RISE = 0.05       # of the way to the gain wanted, per piece (20 ms)

    def __init__(self, gain=4.0):
        self.gain = gain

    def __call__(self, samples):
        if not samples:
            return self.gain
        rms = math.sqrt(sum(x * x for x in samples) / len(samples))
        if rms >= self.FLOOR:
            wanted = min(self.MAX_GAIN, self.TARGET / rms)
            self.gain = wanted if wanted < self.gain else self.gain + (wanted - self.gain) * self.RISE
        peak = max(max(samples), -min(samples))
        if peak:
            self.gain = min(self.gain, 32000 / peak)
        return self.gain


class LinkParser:
    """Links from pw-link --monitor --links --output, line by line: ("+",
    (output port, input port)) for one made, "-" removed, "=" there already.
    Those there already come as a port's line, then "=   |-> input" lines."""

    def __init__(self):
        self.port = None

    def __call__(self, line):
        sign, _space, rest = line.rstrip().partition(" ")
        rest = rest.strip()
        if sign not in ("=", "+", "-"):
            return None
        if rest.startswith("|->"):
            return (sign, (self.port, rest[3:].strip())) if self.port else None
        if " -> " in rest:
            source, _arrow, target = rest.partition(" -> ")
            return sign, (source.strip(), target.strip())
        self.port = rest
        return None


def unload_stale():
    """A source left by a Plus One that did not end cleanly."""
    try:
        modules = subprocess.run(["pactl", "list", "modules", "short"], capture_output=True, text=True,
                                 timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return
    for line in modules.splitlines():
        if f"source_name={MIC_NAME}" in line:
            subprocess.run(["pactl", "unload-module", line.split()[0]], capture_output=True, timeout=5)


class Devices:
    """Camera and microphone, as the settings and the tablet in use say."""

    def __init__(self, app):
        self.app = app
        self.camera = Camera(app)
        self.microphone = Microphone(app)

    def apply(self):
        settings = self.app.settings
        tablet = self.app.tablets.active() is not None
        for device, on in ((self.camera, settings.camera), (self.microphone, settings.microphone)):
            if on and tablet:
                device.enable()
            else:
                device.disable()

    def restart_camera(self):
        """Its quality changed: the device's format with it."""
        self.camera.disable()
        self.apply()

    def shutdown(self):
        self.camera.disable()
        self.microphone.disable()
