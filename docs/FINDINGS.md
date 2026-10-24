# Samsung Second Screen: reverse-engineering notes

Device: Galaxy Tab S10 FE+ (SM-X620), One UI 8.5 / Android 16, Second Screen
`com.samsung.android.secondscreen` 1.0.17.9. Sink engine:
`/system/lib64/libremotedisplay_wfd.so`. Analysed with REA (JADX) for the APK
and `aarch64-linux-gnu-objdump` + `tools/re/arm64_strrefs.py` for the library.
Prior art for the Windows side: paulorochagodoi/segunda-tela-linux.

## Protocol

Plain Wi-Fi Display (Miracast) sink: the source runs the RTSP server on 7236,
sends M1, queries capabilities (M3), sets formats (M4), triggers SETUP (M5).
Media is H.264 in MPEG-TS over RTP (payload 33) to UDP 19000. The sink sends
RTCP receiver reports to the source's `server_port + 1`; if nothing listens
there the PC answers port-unreachable and the sink tears the session down.

## Source classification (sets sink latency)

`WifiDisplaySink::onOptionsRequest` / `onReceiveM2Response` look at the
source's messages:

| Text seen | Source type | latencyMode |
|---|---|---|
| `GStreamer RTSP server`, `AllShareCast/TizenTV` (M1) | 1, TV | 2 (high) |
| `Server: MSMiracastSource…` (reply to M2) | 2, Windows | 1 (mid) |
| `AllShareCast/Galaxy` (M1) | 3, Galaxy phone | 1 (mid) |
| anything else | 0 | 2 (default) |

`DirectRenderer::setSourceType` then applies `/sdcard/Documents/wfd/wfdconfig.txt`
(`WFDConfigParser`, lines of `key value`): `sink_latency_mode low` gives
latencyMode 0 (audio buffer 5 frames instead of 30). Other keys include
`width`, `height`, `fps`, `force_tcp`, `r2`, `hdcp`.

Only for type 2 does the sink answer `wfd_display_edid` (preferred timing
1920×1200@60, 30×20 cm).

## Resolution

The sink lays the picture out by the M4 mode, so the stream must match it.
`VideoFormats::mResolutionTable` (48 entries per table) includes VESA[29]
1920×1200@60 and VESA[33] 2560×1600@60 (the latter needs 48-bit R2
bitmaps / `wfd2_video_formats`). M4 with VESA bit 29 gives a full-screen 16:10
picture even though M3 does not advertise it.

## Latency (photo of PC preview vs tablet, 1920×1200@60 NVENC)

- gnome-network-displays (TV profile, 30 fps): clearly worst.
- Windows identity + `sink_latency_mode low`, with silent AAC: ~90 ms.
- Same, video-only MPEG-TS: ~45–50 ms. The sink accepts video-only streams.

`wfd_sec_low_latency_mode` and LLWFD are source-side (phone → TV) only. UIBC
input sets `UIBCDetected` and `DirectRenderer::setNonAVSyncMode(true)`.

### PC side (2026-10-24, GNOME 46, GStreamer 1.24, PipeWire 1.0.7)

- `mpegtsmux alignment=7` keeps a frame's last TS packets until it has 7,
  i.e. until the next frame; `rtpmp2tpay` does the same with the single-packet
  buffers of `alignment=0/-1` (it flushes at once only for buffers of 2+
  packets). Measured at 4 fps: the PES of most frames was complete 250 ms
  after encoding (one frame later), and ~1 ms with Plus One's own RTP
  sender. The video PES carry their length, so a sink can finish a frame
  without waiting for the next PES.
- Packet loss on an idle stream (desktop still, keepalive frames), 120 s
  each, one after the other on the same link (2026-10-24, 5745 MHz, PC and
  tablet on the same channel): mpegtsmux alignment=7 + rtpmp2tpay +
  udpsink 35 packets lost / 12 IDR requests; Plus One's sender with DSCP EF
  5 / 3; without DSCP 1 / 1. A connect()ed UDP socket had made it worse
  (each ICMP error failed the next send: 4-7 IDR requests a minute).
- Mutter makes a virtual monitor's refresh rate the stream's
  `max-framerate` and skips frames that come a little early: a window
  drawing at 60 fps reached the encoder at ~47 fps (frames 21 ms apart, p90
  30). With `max-framerate=120/1` (a 120 Hz monitor) the same window arrived
  at 60 fps (gaps p50 16.8, p90 19). Capture to encoded frame with NVENC:
  6.5 ms. A token-bucket pacer back to 60 fps added 3-4 ms on average.
  On the tablet, though, the 120 Hz monitor made the stream run at up to
  88-90 fps: Second Screen (60 Hz) queued frames, the delay grew, and it lost
  packets and asked for an IDR every 3-5 s. Back to 60 Hz.
- `pipewiresrc` 1.0.7 cannot negotiate DMA-BUF caps, so Mutter copies each
  frame to shared memory; the VA-API encoder gets no zero-copy path.
- From a window's change committed on the virtual monitor to its frame
  complete on the wire (local UDP, single changes 0.5 s apart on a still
  screen, 20 each): before, mean 108-115 ms, p50 31-33, max 230 (the frame
  waited for a keepalive repeat); now mean 27-36 ms, p50 27-36 (it varies
  from run to run), max 29-78. `tools/latency_pc.py` measures it.
  The capture at 120 instead of 60 Hz changes single changes little (1-6 ms).
- The signing window as one `GtkDrawingArea`: GTK spent ~10 ms a frame
  (p90 17) painting and uploading the whole window, so it ran at 33-40 fps
  with the pen; as a texture for the page plus a cairo node for the newest
  segments, 3.5 ms a frame and a steady 60 fps.

## Starting the receiver

- `adb shell cmd statusbar click-tile com.samsung.android.secondscreen/.tile.ScreenSharingTile`
  opens it (the first click after a force-stop is sometimes ignored).
- Wi-Fi Direct: Second Screen approves an incoming GO negotiation late; the
  tablet completes it (GO-NEG-SUCCESS) but the PC often misses the
  confirmation (status=-1), likely because its single radio also serves the
  2.4 GHz AP while the group runs on 5745 MHz. Flaky on this setup.
- MS-MICE (cable mode): `InfrastructureServer` listens on 127.0.0.1:7250 when
  `sys.usb.config` contains `mtp` and the receiver saw `{"USB":1}` in
  `Android/data/com.samsung.android.secondscreen/files/config/secondscreen.data`
  (it deletes the file after reading). It also switches USB to `conn_gadget`.
  Over adb: `svc usb setFunctions mtp`, write the flag, `adb forward tcp:7250`,
  `adb reverse tcp:7236`, send SOURCE_READY = `len(2) 01 01` + TLVs
  (0: UTF-16LE friendly name, 2: RTSP port, 3: 16-byte source id). The sink
  dials back to 127.0.0.1:7236 within a second; RTP goes to the tablet's LAN
  IP. Each USB function change restarts adbd (new wireless-debugging port).

## Wi-Fi Direct after a change of network (2026-10-24)

- NetworkManager's `StartFind` ends after 30 s unless given a `timeout`
  (1-600 s): measured, `P2P-FIND-STOPPED` 30 s after the call.
- `NM.Device.get_path()` in libnm is the udev `ID_PATH`, not the D-Bus path:
  None for the P2P device and for every virtual interface, so comparing it
  made each docker/VPN interface that went away look like the Wi-Fi device
  going away. `NM.Object.get_path()` is the D-Bus path.
- Three times (09:20, 11:46, 14:25), the Intel AX211 firmware (89.123cf747.0)
  crashed while associating to the tablet's group (`NMI_INTERRUPT_UMAC_FATAL`,
  `FW error in SYNC CMD LINK_CONFIG_CMD`) and the driver was reprobed. Each
  time it was the first Wi-Fi Direct join after the PC moved from a network on
  2462 MHz to one on 5745 MHz (80 MHz wide, the channel the tablet's group
  then used too), with no firmware restart in between; joins after the
  restart, on the same network and channel, worked. The crash also drops the
  PC from its network, and NetworkManager may join the previous one. The
  tablet then stays group owner of that half-made group (`P2P-DEVICE-FOUND`
  from its interface address with `group_capab=0xab`) and ignores GO
  negotiation until `P2P-DEVICE-LOST`: 70-90 s the first two times, 2.5 min
  (three attempts) the third, which connected 1 s after it. Its peer object
  path then changes, and an activation on the old one fails with "The P2P
  peer ... is unknown".
- linux-firmware has four newer builds of that file
  (`iwlwifi-so-a0-gf-a0-89.ucode`, revisions 735b75a4, f7dcb3ee, d2579d43,
  b20dcd1f, March to September 2026); Ubuntu 24.04 ships 123cf747 (January
  2026). Their commit messages do not say what they fix.
- Quick Share announces itself over mDNS as `_FC9F5ED42C8A._tcp`; TXT `n` is
  the base64url endpoint info, whose first byte holds the device type in bits
  1-3 (1 phone, 2 tablet, 3 laptop; seen 0x34 from the Tab S10 FE+, 0x32 from
  a phone). KDE Connect's `_kdeconnect._udp` TXT has `type=tablet` and
  `name=`. RFC 6762 recommends a 75 min TTL for the PTR that lists a service
  and 120 s for its SRV, so a tablet that leaves silently is noticed by
  resolving it again, not by waiting for it to be unlisted.

## Pen handling in note apps (for the signing window)

A note app (version 4.1.4) and Samsung Notes 4.4.45, pulled from
the tablet and read with REA (JADX, objdump); static analysis only.

- **The note app smooths with Google's ink-stroke-modeler** (`ink::stroke_model`,
  Apache-2.0) in `libsmooth_motion.so`, behind `SmoothMotionJNI`
  (`smoothMotionDown/Move/Up(x, y, pressure, time)`, `predictMotionMove`,
  `resetInkStrokeModeler(handle, stylus, level)`). The native reset fills
  `StrokeModelParams` from the pen's *Stabilization* level L (0-10); times
  are passed in milliseconds and divided by 100, so in seconds:
  - spring-mass tip: mass/spring `(30 + 10 L²) / 32400 / 100` s², drag
    `(72 - 0.36 L²) * 10` /s, i.e. the ink trails the pen by `mass * drag`,
    from 7 ms (L=0) to 115 ms (L=10), always well damped;
  - wobble smoothing over 10 ms (stylus) or 50 ms, below 20-30 units/s;
    upsampling to at least 500 steps/s; end-of-stroke catch-up (0.01, 20
    iterations); loop-contraction mitigation off;
  - Kalman predictor: 24 ms ahead for the stylus (36 ms otherwise),
    `process_noise` 2.6e-4, `measurement_noise` 0.026, travel-speed
    confidence 25-400 units/s, acceleration 0.5, jerk 0.1.
  The Java side feeds every historical MotionEvent sample, skips samples
  identical to the previous one, spreads timestamps evenly when the batch's
  are all equal, drops modelled points closer than 0.2 units to the last
  kept one, and asks for the prediction only while moving.
- **The note app's widths** (`lsa.e`): fountain pen width =
  `size * ((k/2 + 1) * pressure + 1 / (2k + 2))` with `k = 6s / (s + 5)`, `s` the
  pressure sensitivity 0-10: linear in pressure above a floor. Strokes are
  filled outlines: each pair of samples becomes two circles (radius = half
  the width at each end) joined by their outer tangents, so the width changes
  smoothly inside a segment. Holding the pen still at the end of a line
  snaps it straight (`lsa.r`: RMS distance to the chord).
- **Samsung Notes** predicts with encrypted neural models in
  `libSPenPredictor.so` (M16/M20/M22: 16, 5.6 and 6.3 ms ahead), each fed
  through Kalman filters on position, pressure, tilt and orientation; not
  reusable.
- **What Plus One took** (`plusone/modeler.py`): the same model and L scale,
  the default L=2 (15 ms); our clock counts the pen's reports (285 a
  second) because they arrive over Wi-Fi in bursts; pressure-only reports
  (every other one) move time but not the velocity estimate. The prediction
  was retuned on a real signature: a straight line along the filtered
  velocity, 30 ms ahead at most, 0.12 px RMS off the line drawn next.
- The "stray samples" in signatures (a point a few reports ahead, needles
  and bumps) were not Second Screen's: all 232 had the same pressure, the
  signing window's default for a mouse. GTK 4 on Wayland also hands a
  tablet pen to drag gestures, from its "Wayland Tablet Pointer" device
  whose source is *mouse*, so checking the device's source let it through;
  the event's device tool is what tells a pen. The pen reports themselves
  had none (0 in 5225 through the filter that briefly ran on UIBC).

## Companion app (0.5)

- Android's `PdfRenderer` page space is the PC's display space (poppler's,
  plusone/pdf.py): points from the crop box's top-left corner, `/Rotate`
  applied. On a `/Rotate 90` page and on one with a shifted crop box,
  markers at (650, 150) and (250, 200) came back from the tablet at
  (650.2, 150.0) and (249.7, 199.8), and the saved PDF had the ink on them.
- `CanvasFrontBufferedRenderer`'s multi-buffered layer has three buffers:
  asking for a new scene every frame (erasing, panning) faster than they
  came back left its render thread waiting forever ("BufferPool: Waiting
  for buffer to become available") and froze the screen. One scene in
  flight at a time fixed it.
- An `AlertDialog` inside a dialog-themed activity started from a service
  was dismissed as soon as it showed; a plain layout stays.
- Each container build made a new debug signing key, and Android refused
  to update the installed app; the key now lives in the Gradle volume.
- Closing an `Adw.MessageDialog` from its own "response" handler emits
  "response" again, with its close response: the pairing dialog's "Pair"
  then counted as "Cancel". The first answer is the one that counts now.
- A link that dies without a word (Wi-Fi gone, the tablet's address
  changed) left the PC waiting forever: it now asks "ping" after 15 s of
  silence and gives the link up after 45, then looks for the app again. The
  app keeps a job's answer until the PC says "got": bytes written into a
  dead link are not an answer delivered.
- On Android 16 with "Display over other apps", the signing screen opens
  from the background too (the service is in the foreground).
- Pairing codes made of the two certificates only could be ground by
  someone in the middle (a certificate whose code matches, about 10^6
  tries); each side's random number, the app's committed to first, takes
  that away (Bluetooth's numeric comparison does the same).

## How a note app selects (studied with REA, its version 4.1.7)

The note app is a native Android app; its own code was
decompiled with JADX (through REA) and read, never run. What the
selection does, which Plus One follows on the tablet and the PC
(android/.../Selection.kt, plusone/selection.py):

- Lasso tool settings (protobuf `lassoTool_`): `lassoType` FREEFORM or
  RECTANGLE, `allowFullSelectionOnly` (default off: the lasso picks what
  its area touches, by intersecting regions; on: only what is wholly
  inside), `allowSnapToAlign`, `selectedBlockTypes`. Selection triggers:
  single tap, lasso, double tap; a "circle to lasso" gesture.
- The lasso: a dotted path (6 px round dots, dash 4/20) in #0B99FF over
  the same dots in white at 20 % alpha, offset; freeform closes as drawn.
- The box: four corners joined by 4 px #0B99FF lines, turning with the
  selection; on screen, a side shorter than 120 px (2x density) is widened
  to 120 so handles never crowd. Corner handles: circles of 10 px, white
  with the blue ring, filled blue while dragged; hit within 20 px.
- Turn handle: a 24 px circle with a rotate icon, 50 px out of the
  bottom-right corner along both edges; blue with a white icon while
  turning, the angle shown as "%5.1f°" in #FF4631. It turns about the box
  centre, the angle kept within -180..180.
- Operations (`NORMAL_MODE_OPERATION_MOVING/RESIZING/ROTATING`): down on a
  corner resizes from the opposite corner, keeping proportions
  (`shouldKeepAspectRatioWhenResizing`); on the turn handle turns; inside
  moves (with snapping guides); outside ends the selection.
- The eraser (`eraserTool_`) has pixel and object modes and an option to
  deselect itself after use.

## How a note app renders a PDF while scrolling (studied with REA)

The same note app (its version 4.1.7, decompiled with JADX through REA and
its PDFium wrapper disassembled; read, never run). What Plus One's tablet
app follows (android/.../Pages.kt):

- Two layers. Each page whole at a modest resolution (up to about 1.4
  million pixels there; Plus One allows 2 million, A4 at about 170 dpi),
  scaled while the zoom changes; and sharp tiles of 400 × 400 pixels at the
  zoom, quantized to steps of √2 (`√2^ceil(log√2 zoom)`, so 1-1.41 times the
  screen's density), on a grid anchored to the page's corner, so a tile
  stays the same tile while the page moves.
- Tiles are asked for at every scroll step while panning or flinging, not
  only when the page stops (never while pinching): the pages with the
  largest part of the screen first, in each the tiles on screen, then
  those within 400 pixels of it. A request no longer on the list is
  dropped before it is rendered.
- A missing tile is stood in for by the cached tiles of the nearest other
  √2 level (the sharper of two equally near), over the scaled whole page.
- Least-recently-used caches by bytes (whole pages and tiles), cut back
  when Android says memory is short. All ARGB_8888: PDFium renders into
  the bitmap's own pixels, filled white first. PDFium calls are
  serialized behind one lock, so one rendering thread is enough; open
  pages are kept (five there), not opened again for each tile.
