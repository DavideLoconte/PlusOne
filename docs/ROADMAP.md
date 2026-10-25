# Plus One roadmap

The goal: the tablet becomes part of the Linux desktop like any other
peripheral. Its screen, touch and pen, but also its camera, microphone and
speakers (usually better than a laptop's) show up where GNOME already lists
such devices, and are used only when you pick them.

## The rule: convenience first

Being able to use the tablet is not enough; every task has to be quick to
start from wherever the user already is. Signing one PDF must not mean
"open Plus One, start the extended display, find a PDF app that handles a pen,
open the file, sign, save, close everything".

Every feature is designed and accepted against these rules:

1. **Start where you are.** Tasks begin from the file manager, GNOME Quick
   Settings, a notification or the tablet itself, never from "open Plus One
   first".
2. **At most one tap on the tablet**, and no choosing apps. Today that tap is
   the *Second screen* tile; the research track aims at zero.
3. **Connections are on demand.** A task opens the link it needs and closes it
   when done, unless the user is using the tablet as a monitor anyway.
4. **Results come back by themselves**: saved next to the original (never
   overwriting it) with a notification offering *Open* and *Show in folder*.
5. **Same features in the .deb and the Flatpak**, or a documented equivalent.
6. **Defaults that just work**; settings are for preferences, not for setup.

Each milestone states its acceptance target as *clicks on the PC / taps on
the tablet / waiting time*.

## Architecture change: a background service

Convenience needs something that is always there and that every entry point
can call. Plus One becomes a small session service plus thin front ends:

```
 Nautilus menu ─┐                                ┌─ virtual monitor (Mutter)
 Quick Settings ┼─ D-Bus ─▶ plus-one service ────┼─ Wi-Fi Direct + WFD session
 notifications ─┤   io.github.…PlusOne1          ├─ signing window on the tablet
 window ────────┘                                └─ Quick Share sender/receiver
```

- **D-Bus API** (`io.github.<id>.PlusOne1`): `Extend()`, `Mirror(connector)`,
  `TabletMode(connector)`, `Stop()`, `SignDocument(path) → path`,
  `SendFiles(paths)`; properties `State`, `Mode`, `Tablet`; change signals.
- **D-Bus activation and autostart** (XDG autostart for the .deb, the
  Background portal for the Flatpak). Idle cost: a sleeping process; Wi-Fi
  Direct is only touched when a task needs it, because searching for peers
  delays other Wi-Fi traffic (measured during a find: median unchanged, p90
  8 → 66 ms, p99 12 → 136 ms, 20% of packets over 30 ms).
- **Leases on the link**: every task holds one; the link closes when the last
  lease goes, unless the user pinned *Extend*.
- The current window becomes the settings and status page of the service.

## Milestones

### 0.3 — Always ready (done)

- Background application, D-Bus API, activation, autostart; the window is a
  front end like the others.
- **GNOME Shell Quick Settings toggle "Tablet"**: connect/disconnect, state as
  subtitle, menu with *Ready in the background* and *Settings*. *Mirror* and
  *Graphics tablet* join the menu in 0.5.
- Notifications: "Connected" with *Disconnect*, "tablet not found", errors.
- Searching is time-boxed (see the measurement above); reopening Second Screen
  within 90 s of closing it reconnects without a click.
- App ID moved to `io.github.gabrielesantangelo.PlusOne`.

Acceptance: from a fresh login, tablet as a monitor = 1 click + 1 tap.

### 0.4 — Sign on the tablet (done)

- **Entry points**: Nautilus *Sign on tablet* (nautilus-python, .deb),
  *Open With → Sign on tablet* (both packages), `SignDocument` over D-Bus.
- **Flow**: opens a temporary display if none is active; a signing window
  appears full screen on the tablet by itself (no app to pick); the PC screen
  shows a small "Signing on the tablet…" notice.
- **Signing window, built for the pen**:
  - pressure-sensitive ink;
  - fingers scroll and zoom, and touches are ignored while the pen is near
    (palm rejection);
  - *Place signature* reuses saved signatures (drawn once, stored as strokes);
  - text and date fields for forms;
  - large *Done* / *Cancel* buttons.
- **Result**: `name-signed.pdf` next to the original plus a notification;
  the temporary display closes.
- Tech: poppler-glib for rendering, ink and stamp annotations and saving (new
  dependency; the Flatpak builds it).
- Scope note: a drawn signature is a simple electronic signature. A digital
  PAdES signature with the user's certificate is a possible later addition.

Acceptance: right-click → *Sign on tablet* → 1 tap → sign → *Done*: no other
step, less than 10 s of overhead.

### 0.5 — Companion app: instant ink (done; the display moved to 0.7)

Measured in 0.4: from the pen to its ink on the tablet takes about 70-100 ms
through Second Screen. Plus One's own drawing is 3-4 ms of that; the rest is
the round trip: the pen's reports over Wi-Fi, Mutter compositing and
capturing the virtual monitor (~20 ms), encoding (5 ms), the stream, and the
tablet's player decoding and showing it. Only code on the tablet can remove
the round trip. A small Plus One app on the tablet, optional and installed
once, does that for signing first, then for the display, then for the rest
of the tablet's hardware.

- **Signing in the app, ink under the nib**: the PC hands the app the PDF;
  the app shows its pages (Android's `PdfRenderer`) and draws the ink itself
  with Android's low-latency front-buffer rendering (androidx.graphics) and
  motion prediction (androidx.input), from every pen sample, with pressure
  and palm rejection. Strokes go back to the PC, which stamps them into the
  PDF as in 0.4 (`name-signed.pdf`, notification). No Second Screen and no
  Wi-Fi Direct for this: it runs over the home Wi-Fi, starts in about a
  second and avoids the Intel firmware crashes. Target: ink within 20 ms.
- **A display of its own** (Second Screen stays the no-install way): H.264
  from Plus One decoded with `MediaCodec` in low-latency mode and shown as
  each frame arrives, with no jitter buffer; touch and pen sent back as
  Android motion events with all their samples (no UIBC bursts); quick
  recovery from loss. Target: below Second Screen's ~45-50 ms.
- **Then the rest of the tablet's hardware**, on demand:
  - **Camera as a webcam**: front or rear camera, encoded on the tablet
    (MediaCodec H.264), decoded on the PC and published as a PipeWire camera,
    so it appears in GNOME Snapshot, Firefox, OBS, browser meetings. For apps
    that only know V4L2 (some Chrome/Zoom builds) an optional v4l2loopback
    device, set up once like the pen permission.
  - **Microphone**: a PipeWire input "Tablet microphone".
  - **Speakers**: a PipeWire output "Tablet speakers" with its own low-latency
    audio stream (Opus/PCM), independent of the video, so it does not slow the
    display down.
- **On demand, one tap at most**: when the PC needs the app (a PDF to sign,
  a camera in use), the app shows one notification on the tablet (Android
  needs a visible start for camera and microphone). Nothing runs while
  unused.
- **Transport**: the home Wi-Fi (found with mDNS, as 0.4 already finds the
  tablet), or the Wi-Fi Direct link while the display uses it; encrypted
  and paired once with a code.
- **Install**: the APK is attached to the GitHub release; no developer
  options needed. Kotlin, no Google services, built by CI.

Acceptance: right-click a PDF → *Sign on tablet* → the app opens on the
tablet (one tap on its notification at most) → the ink is under the nib →
*Done* → `name-signed.pdf` on the PC.

### 0.6 — Your tablet, and only it (done)

- Pairing from the window (six-digit code on both screens); one tablet in
  use; Second Screen checked against the paired app before any picture
  leaves the PC.
- The tablet's camera and microphone as the PC's webcam and microphone,
  over WebRTC, captured only while a program uses them, asked for on the
  tablet when needed; neural noise reduction on the PC.
- The S Pen through a small root helper (no group gets `/dev/uhid`);
  Intel's low-power encoder first; preferences on one page.

### 0.7 — The desktop in Plus One's app (steps 1-3 done)

The display part of 0.5, now first: the desktop goes to Plus One's app
instead of Second Screen, which stays as the way without the app.

- **Why**: any Android tablet (not only Samsung's), the home Wi-Fi instead
  of Wi-Fi Direct (no group to form, no Intel firmware crashes, the PC keeps
  its network), the tablet's own resolution (2880×1800 on a Tab S10 FE+,
  WFD stops at 1920×1200), and the pen's every sample instead of UIBC's
  bursts.
- **Picture**: the virtual monitor and encoder of today (Intel low-power
  first), H.264 without MPEG-TS, each frame cut into UDP packets and sent
  at once, encrypted (AES-GCM) with a key the paired TLS link hands over
  for the session. On the tablet `MediaCodec` in low-latency mode decodes
  into a full-screen `SurfaceView`, each frame shown as soon as it is
  decoded, no jitter buffer. A lost packet drops the frame and asks for a
  keyframe, at most one a second.
- **Input**: touches and the pen as Android gives them (every historical
  sample, pressure, tilt, hover, side button, eraser), over the same
  encrypted UDP; the PC replays touches through Mutter (as UIBC's today)
  and the pen through the pen helper, on the tablet's monitor only.
- **Control** over the paired TLS link: start and stop, size and rate,
  keyframe requests, statistics; found by mDNS as for signing.
- **Starting**: *Use as second screen*, the Quick Settings button or the
  notification pick the app when it is there; the app opens the display by
  itself (it may already, for signing). Nothing runs while it is not shown:
  the tablet's screen off or the app left stops capture and encoding.
- **Measured, not guessed**: frames carry the PC's capture time and the app
  reports when each was shown (clocks matched over the TLS link), so the
  window and log give the real delay.

Acceptance: 1 click on the PC, no tap on the tablet; from a change on the
PC to the tablet's screen under Second Screen's ~45-50 ms (target 35 ms) at
1920×1200@60, sharp text at 2880×1800; the pen's ink in GIMP with every
sample.

Steps: (1) a test pattern to the app, its delay measured; (2) the real
desktop, touch and pen; (3) starting, stopping, losses, power, the window
and Quick Settings; (4) release.

Measured after step 3, 1920×1200@60, Intel's low-power encoder, Exynos'
decoder, home Wi-Fi: capture to decoded 23 ms median idle, about 30 while
windows move. Still open: the tablet's own resolution (2880×1800 wants a
fractional scale for the virtual monitor), and a photo of both screens
for the delay to the glass.

### Later — Tablet modes and picture quality

- **Mirror** an existing monitor (presentations, teaching).
- **Graphics tablet**: the pen drives a chosen PC monitor like a Wacom while
  the tablet mirrors it; switch from Quick Settings.
- **2560×1600** through WFD R2 for sharper text (VESA index 33 is in the
  tablet's table).
- **Portrait**, if the sink accepts it (research `wfd_sec_portrait_display` and
  the 7260 side channel).
- **Adaptive bitrate** from the RTCP loss reports the tablet already sends.
- Optional **audio on the tablet through Second Screen**, no app needed: the
  tablet appears as a speaker in GNOME Sound; picking it reconnects with an
  audio track. Video latency goes from ~50 to ~90 ms (the sink syncs video to
  audio), so this suits films more than desktop work. The companion app (0.5)
  removes that cost.

### Later — Quick Share

- Our own Python implementation of the Quick Share (Nearby Share) protocol:
  mDNS, UKEY2 handshake, protobuf frames, AES-CBC and HMAC. Written from the
  public protocol notes (NearDrop, RQuickShare), MIT, no extra program.
  Dependencies: `cryptography`, `protobuf`.
- Works on the home Wi-Fi; check whether it also runs over the Wi-Fi Direct
  link while that is up.
- **Send**: Nautilus *Send to tablet*, Quick Settings *Send files…*.
- **Receive**: the tablet's normal *Share → Quick Share → this PC*; files land
  in Downloads with a notification; auto-accepted from our paired tablet.
- **Sign with a tablet app**: the PC sends the PDF, you sign it in Samsung
  Notes (best palm rejection) and share it back; Plus One recognises it and
  saves `name-signed.pdf` next to the original.

Acceptance: send = right-click → *Send to tablet*; receive = share on the
tablet → notification on the PC.

### Later — Polish

- Optional USB mode through adb and the MS-MICE path (steadier link for
  people with a free port).
- Flathub submission.
- Documentation, screenshots, translations.

### Research track (in parallel)

- **Connect without touching the PC**: notice that Second Screen was opened
  without scanning Wi-Fi Direct all the time (its cost is above). Candidates:
  a change in the tablet's Samsung BLE advertisements (Bluetooth scanning does
  not disturb Wi-Fi), mDNS on the home network. Then opening the tile on the
  tablet would be the only step.
- **Open Second Screen without touching the tablet**: Windows wakes it over
  Bluetooth through Samsung MCF; reverse engineer the start intent
  (`com.samsung.android.mcfserver`, the Windows MCF library). This would remove
  the last tap.
- Persistent Wi-Fi Direct groups for faster reconnects.
- Mitigations for Intel `iwlwifi` firmware crashes during group formation.
- Android's built-in USB webcam mode (UVC, Android 14+, `ro.usb.uvc.enabled`):
  a zero-app camera for people who can plug the tablet in.
- Whether the companion app can start the display too (fewer apps involved
  when it is installed).

## Not planned

Clipboard sharing, phone notifications and remote input: GSConnect and KDE
Connect already do them well.
