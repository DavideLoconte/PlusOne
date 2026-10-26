"""The app display on macOS: a virtual display, captured by ScreenCaptureKit
and encoded by VideoToolbox, the tablet's fingers and pen as the Mac's
pointer; no GLib or GStreamer.

The display is CoreGraphics' CGVirtualDisplay, a private API (the one
Chromium's tests, BetterDisplay and Deskreen use): it can change with a
macOS update. Capture needs Screen Recording, input needs Accessibility
(System Settings → Privacy & Security), for whatever runs Plus One (the
terminal, from the sources). PyObjC is imported where used: the arithmetic
here is tested anywhere.
"""
import functools
import struct
import threading
import time

from . import log, pen

LEFT, RIGHT = 0, 1  # CGMouseButton
TABLET_POINT = 1    # kCGEventMouseSubtypeTabletPoint
KEYFRAME_SECONDS = 10  # as media.py's: periodic keyframes
VBV_FRAMES = 4         # as media.py's: a keyframe may be this many average frames
KEEPALIVE = 0.1        # seconds: the last frame again (pipewiresrc's keepalive-time on Linux)
START = b"\x00\x00\x00\x01"
SCREEN_RECORDING = ("macOS does not let Plus One record the screen: allow it in System Settings → "
                    "Privacy & Security → Screen Recording, then start it again")


def later(fn, *args):
    """fn(*args) on the main thread's run loop."""
    from PyObjCTools import AppHelper
    AppHelper.callAfter(fn, *args)


def run():
    from PyObjCTools import AppHelper
    AppHelper.runConsoleEventLoop(installInterrupt=True)


def quit():
    from PyObjCTools import AppHelper
    AppHelper.stopEventLoop()


def to_screen(bounds, size, x, y):
    """A point in the stream's pixels (size) to global display points:
    bounds is the display's (x, y, width, height) in points."""
    left, top, width, height = bounds
    return left + x * width / size[0], top + y * height / size[1]


def pen_sample(data):
    """(bits, x, y, pressure, tilt x, tilt y) of a pen report: x, y 0..1,
    pressure 0..1, tilts -1..1 (macOS' range for ±90°)."""
    bits, x, y, pressure, tilt_x, tilt_y = struct.unpack("<BHHHbb", data[1:10])
    return (bits, x / pen.AXIS_MAX, y / pen.AXIS_MAX, min(1.0, pressure / pen.PRESSURE_MAX),
            tilt_x / 90, tilt_y / 90)


def parameter_sets(avcc):
    """([SPS..., PPS...], NAL length size) of an avcC record."""
    length_size = (avcc[4] & 3) + 1
    sets, at = [], 5
    for mask in (0x1F, 0xFF):  # the SPS count is the low 5 bits, the PPS count a byte
        count = avcc[at] & mask
        at += 1
        for _ in range(count):
            n = int.from_bytes(avcc[at:at + 2], "big")
            sets.append(bytes(avcc[at + 2:at + 2 + n]))
            at += 2 + n
    return sets, length_size


def annex_b(data, length_size=4, before=()):
    """VideoToolbox's length-prefixed NAL units as the app's decoder takes
    them (start codes), the NAL units in before first."""
    out = bytearray()
    for nal in before:
        out += START + nal
    at = 0
    while at + length_size <= len(data):
        n = int.from_bytes(data[at:at + length_size], "big")
        at += length_size
        out += START + data[at:at + n]
        at += n
    return bytes(out)


def accessibility():
    """Whether Plus One may move the pointer; if not, macOS is asked to
    offer it (its dialog, once)."""
    import ApplicationServices as AS
    return AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})


class Monitor:
    """A virtual display of size pixels (at scale 2 its points are half),
    with the methods appdisplay.Input calls on Mutter's monitor."""

    def __init__(self, size, scale):
        import libdispatch
        import objc
        import Quartz
        self.Q = Quartz
        self.size = size
        points = (size[0] // scale, size[1] // scale)
        descriptor = objc.lookUpClass("CGVirtualDisplayDescriptor").alloc().init()
        descriptor.setQueue_(libdispatch.dispatch_get_global_queue(0, 0))
        descriptor.setName_("Plus One")
        descriptor.setMaxPixelsWide_(size[0])
        descriptor.setMaxPixelsHigh_(size[1])
        descriptor.setSizeInMillimeters_(Quartz.CGSizeMake(points[0] * 25.4 / 110, points[1] * 25.4 / 110))
        descriptor.setVendorID_(pen.VENDOR)
        descriptor.setProductID_(pen.PRODUCT)
        descriptor.setSerialNum_(1)
        self.display = objc.lookUpClass("CGVirtualDisplay").alloc().initWithDescriptor_(descriptor)
        if self.display is None:
            raise RuntimeError("macOS did not make the virtual display")
        settings = objc.lookUpClass("CGVirtualDisplaySettings").alloc().init()
        settings.setHiDPI_(1 if scale > 1 else 0)
        settings.setModes_([objc.lookUpClass("CGVirtualDisplayMode").alloc()
                            .initWithWidth_height_refreshRate_(points[0], points[1], 60.0)])
        if not self.display.applySettings_(settings):
            raise RuntimeError("macOS refused the virtual display's mode")
        self.id = self.display.displayID()
        self.finger = None   # the one finger that is the pointer
        self.buttons = set()
        self.last = (0, 0)   # where the pointer was put, in the stream's pixels
        if not accessibility():
            log("macOS: no Accessibility for Plus One: the tablet's touches do nothing until it is allowed")
        log(f"macOS: virtual display {self.id}, {points[0]}x{points[1]} points")

    def stop(self):
        for button in list(self.buttons):
            self.pointer_button(pen.BTN_RIGHT if button == RIGHT else pen.BTN_LEFT, False)
        self.display = None  # released: the display goes
        log("macOS: virtual display closed")

    def point(self, x, y):
        b = self.Q.CGDisplayBounds(self.id)
        return to_screen((b.origin.x, b.origin.y, b.size.width, b.size.height), self.size, x, y)

    def post(self, kind, x, y, button=LEFT, fields=()):
        event = self.Q.CGEventCreateMouseEvent(None, kind, self.point(x, y), button)
        for field, value in fields:
            if isinstance(value, float):
                self.Q.CGEventSetDoubleValueField(event, field, value)
            else:
                self.Q.CGEventSetIntegerValueField(event, field, value)
        self.Q.CGEventPost(self.Q.kCGHIDEventTap, event)
        self.last = (x, y)

    # ponytail: one finger is the pointer (macOS takes no injected touches);
    # two-finger scrolling would be CGEventCreateScrollWheelEvent, when wanted.
    def touch_down(self, slot, x, y):
        if self.finger is None:
            self.finger = slot
            self.pointer_move(x, y)
            self.pointer_button(pen.BTN_LEFT, True)

    def touch_motion(self, slot, x, y):
        if slot == self.finger:
            self.post(self.Q.kCGEventLeftMouseDragged, x, y)

    def touch_up(self, slot):
        if slot == self.finger:
            self.finger = None
            self.pointer_button(pen.BTN_LEFT, False)

    def pointer_move(self, x, y):
        self.post(self.Q.kCGEventMouseMoved, x, y)

    def pointer_button(self, button, pressed):
        Q = self.Q
        which = RIGHT if button == pen.BTN_RIGHT else LEFT
        kinds = {(LEFT, True): Q.kCGEventLeftMouseDown, (LEFT, False): Q.kCGEventLeftMouseUp,
                 (RIGHT, True): Q.kCGEventRightMouseDown, (RIGHT, False): Q.kCGEventRightMouseUp}
        (self.buttons.add if pressed else self.buttons.discard)(which)
        self.post(kinds[which, pressed], *self.last, button=which)


class Pen:
    """The S Pen as tablet events on the display: pressure and tilt for the
    apps that read them, the pointer for the others; barrel = right button.
    ponytail: no proximity events, so apps that wait for one before taking
    pressure (a few) see a mouse; add kCGEventTabletProximity if one matters."""

    def __init__(self, monitor):
        self.monitor = monitor
        self.bits = 0
        log("pen: tablet events on the virtual display (pressure, tilt)")

    def report(self, data):
        if not pen.is_report(data):
            return
        Q, m = self.monitor.Q, self.monitor
        bits, x, y, pressure, tilt_x, tilt_y = pen_sample(data)
        x, y = x * m.size[0], y * m.size[1]
        near = bits & pen.IN_RANGE
        tip, barrel = near and bits & pen.TIP, near and bits & pen.BARREL
        was_tip, was_barrel = self.bits & pen.TIP, self.bits & pen.BARREL
        tablet = ((Q.kCGMouseEventSubtype, TABLET_POINT), (Q.kCGTabletEventPointPressure, pressure if tip else 0.0),
                  (Q.kCGTabletEventTiltX, tilt_x), (Q.kCGTabletEventTiltY, tilt_y),
                  (Q.kCGMouseEventPressure, pressure if tip else 0.0))
        if tip and not was_tip:
            kind = Q.kCGEventLeftMouseDown
        elif was_tip and not tip:
            kind = Q.kCGEventLeftMouseUp
        elif tip:
            kind = Q.kCGEventLeftMouseDragged
        else:
            kind = Q.kCGEventMouseMoved
        if near or was_tip:
            m.post(kind, x, y, fields=tablet)
            if kind == Q.kCGEventLeftMouseDown:
                m.buttons.add(LEFT)
            elif kind == Q.kCGEventLeftMouseUp:
                m.buttons.discard(LEFT)
        if bool(barrel) != bool(was_barrel):
            m.pointer_button(pen.BTN_RIGHT, bool(barrel))
        self.bits = (pen.IN_RANGE if near else 0) | (pen.TIP if tip else 0) | (pen.BARREL if barrel else 0)

    def close(self):
        if self.bits & pen.TIP:
            self.monitor.pointer_button(pen.BTN_LEFT, False)
        if self.bits & pen.BARREL:
            self.monitor.pointer_button(pen.BTN_RIGHT, False)
        self.bits = 0


@functools.cache
def _capture_class():
    """The ScreenCaptureKit stream's output and delegate (defined once: an
    Objective-C class name is the process's)."""
    import objc
    from Foundation import NSObject

    class PlusOneCapture(NSObject, protocols=[objc.protocolNamed("SCStreamOutput"),
                                              objc.protocolNamed("SCStreamDelegate")]):
        def stream_didOutputSampleBuffer_ofType_(self, _stream, sample, _kind):
            self.video._captured(sample)

        def stream_didStopWithError_(self, _stream, error):
            self.video._failed(f"the capture stopped: {error.localizedDescription()}")

    return PlusOneCapture


def _wait(start, what, timeout=5):
    """start(handler) with a completion handler(*results): the results, once
    it is called (handlers run on ScreenCaptureKit's queue)."""
    done, box = threading.Event(), []

    def handler(*results):
        box.extend(results)
        done.set()
    start(handler)
    if not done.wait(timeout):
        raise TimeoutError(f"macOS did not {what}")
    return box


class Video:
    """The virtual display (its CGDirectDisplayID), captured and encoded:
    each frame to on_frame(data, keyframe, captured ns), Annex B, from
    VideoToolbox's thread. on_error(message) on the main thread."""

    def __init__(self, display_id, width, height, fps, bitrate, _encoder, _preview, on_frame, on_error):
        import CoreMedia as CM
        import objc
        import VideoToolbox as VT
        self.CM, self.VT = CM, VT
        self.display_id, self.size, self.fps = display_id, (width, height), fps
        self.on_frame, self.on_error = on_frame, on_error
        self.stream = None
        self.paused = False
        self._force = False
        self._last = None          # the last frame captured, and when (monotonic)
        self._last_at = 0.0
        self._lock = threading.Lock()
        self._done = threading.Event()
        # PyObjC knows the session's type by name only: made a CF object, its
        # properties can be set.
        objc.registerCFSignature("VTCompressionSessionRef", b"^{OpaqueVTCompressionSession=}",
                                 VT.VTCompressionSessionGetTypeID())

        @objc.callbackFor(VT.VTCompressionSessionCreate)
        def encoded(_refcon, captured, status, _flags, sample):
            if status or sample is None:
                return
            block = CM.CMSampleBufferGetDataBuffer(sample)
            _err, data = CM.CMBlockBufferCopyDataBytes(block, 0, CM.CMBlockBufferGetDataLength(block), None)
            attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample, False)
            keyframe = not (attachments and attachments[0].get(CM.kCMSampleAttachmentKey_NotSync))
            atoms = CM.CMFormatDescriptionGetExtension(
                CM.CMSampleBufferGetFormatDescription(sample),
                CM.kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms)
            sets, length_size = parameter_sets(bytes(atoms["avcC"]))
            self.on_frame(annex_b(data, length_size, sets if keyframe else ()), keyframe, captured)

        self._encoded = encoded  # kept: VideoToolbox calls it
        spec = {VT.kVTVideoEncoderSpecification_EnableLowLatencyRateControl: True}
        err, self.session = VT.VTCompressionSessionCreate(None, width, height, CM.kCMVideoCodecType_H264,
                                                          spec, None, None, encoded, None, None)
        if err:
            raise RuntimeError(f"VideoToolbox: no H.264 encoder ({err})")
        burst = VBV_FRAMES / fps  # seconds of the average rate a keyframe may take
        for key, value in ((VT.kVTCompressionPropertyKey_RealTime, True),
                           (VT.kVTCompressionPropertyKey_AllowFrameReordering, False),
                           (VT.kVTCompressionPropertyKey_ProfileLevel, VT.kVTProfileLevel_H264_High_AutoLevel),
                           (VT.kVTCompressionPropertyKey_AverageBitRate, bitrate * 1000),
                           (VT.kVTCompressionPropertyKey_DataRateLimits, [int(bitrate * 125 * burst), burst]),
                           (VT.kVTCompressionPropertyKey_MaxKeyFrameInterval, fps * KEYFRAME_SECONDS),
                           (VT.kVTCompressionPropertyKey_ExpectedFrameRate, fps)):
            if VT.VTSessionSetProperty(self.session, key, value):
                log("macOS: the encoder does not take", key)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self._open()
        except Exception as e:  # noqa: BLE001 - said to the session, which ends
            self._failed(str(e))
            return
        while not self._done.wait(KEEPALIVE):
            with self._lock:
                last, at = self._last, self._last_at
            if last is not None and not self.paused and time.monotonic() - at >= KEEPALIVE:
                self._encode(last, time.monotonic_ns())

    def _open(self):
        import libdispatch
        import Quartz
        import ScreenCaptureKit as SCK
        display = None
        for _ in range(30):  # the new display reaches ScreenCaptureKit a moment later
            content, error = _wait(SCK.SCShareableContent.getShareableContentWithCompletionHandler_,
                                   "list the displays")
            if error is not None:
                raise PermissionError(SCREEN_RECORDING if error.code() == -3801 else error.localizedDescription())
            display = next((d for d in content.displays() if d.displayID() == self.display_id), None)
            if display is not None or self._done.is_set():
                break
            time.sleep(0.1)
        if display is None:
            raise RuntimeError("the virtual display did not show up for capture")
        config = SCK.SCStreamConfiguration.alloc().init()
        config.setWidth_(self.size[0])
        config.setHeight_(self.size[1])
        config.setMinimumFrameInterval_(self.CM.CMTimeMake(1, self.fps))
        config.setPixelFormat_(Quartz.kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)  # NV12, VideoToolbox's own
        config.setShowsCursor_(True)
        config.setQueueDepth_(4)  # one kept for the keepalive
        self.output = _capture_class().alloc().init()
        self.output.video = self
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
        self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config,
                                                                                  self.output)
        added = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self.output, SCK.SCStreamOutputTypeScreen, libdispatch.dispatch_queue_create(b"plusone.capture", None),
            None)
        if not (added[0] if isinstance(added, tuple) else added):
            raise RuntimeError("ScreenCaptureKit did not take the stream's output")
        self._capture(True)

    def _capture(self, on):
        if self.stream is None:
            return
        method = (self.stream.startCaptureWithCompletionHandler_ if on
                  else self.stream.stopCaptureWithCompletionHandler_)
        error = _wait(method, "start the capture" if on else "stop the capture")[0]
        if error is not None and on:
            raise PermissionError(SCREEN_RECORDING if error.code() == -3801 else error.localizedDescription())

    def _captured(self, sample):
        """ScreenCaptureKit's queue: a frame, if it is a new picture."""
        import ScreenCaptureKit as SCK
        CM = self.CM
        attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample, False)
        if not attachments or attachments[0].get(SCK.SCStreamFrameInfoStatus) != SCK.SCFrameStatusComplete:
            return  # idle (nothing changed), blank, suspended
        image = CM.CMSampleBufferGetImageBuffer(sample)
        if image is None or self.paused:
            return
        with self._lock:
            self._last, self._last_at = image, time.monotonic()
        # Its presentation time is the host clock at capture, the clock of
        # time.monotonic_ns() (mach_absolute_time) that the pings match.
        pts = CM.CMSampleBufferGetPresentationTimeStamp(sample)
        captured = pts.value * 1_000_000_000 // pts.timescale if pts.timescale else time.monotonic_ns()
        self._encode(image, captured)

    def _encode(self, image, captured):
        VT, CM = self.VT, self.CM
        with self._lock:  # the capture's queue and the keepalive's thread
            options = {VT.kVTEncodeFrameOptionKey_ForceKeyFrame: True} if self._force else None
            self._force = False
            if self._done.is_set():
                return
            VT.VTCompressionSessionEncodeFrame(self.session, image, CM.CMTimeMake(time.monotonic_ns() // 1000, 1000000),
                                               CM.kCMTimeInvalid, options, captured, None)

    def keyframe(self):
        self._force = True  # the next frame, the keepalive's at the latest

    def pause(self, paused):
        """Nothing is captured or encoded while the app does not show it."""
        self.paused = paused
        threading.Thread(target=self._capture, args=(not paused,), daemon=True).start()

    def _failed(self, message):
        if not self._done.is_set():
            log("macOS: video:", message)
            later(self.on_error, message)

    def stop(self):
        self._done.set()
        if self.stream is not None:
            try:
                self._capture(False)
            except Exception as e:  # noqa: BLE001 - it ends anyway
                log("macOS: stopping the capture:", repr(e))
            self.stream = None
        with self._lock:
            self.VT.VTCompressionSessionInvalidate(self.session)
            self._last = None
