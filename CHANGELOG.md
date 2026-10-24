# Changelog

## 0.5.0 (in progress)

The companion app: ink under the nib.

- **Plus One for the tablet** (`android/`, Kotlin): Sign on Tablet sends the
  PDF to it over the home network, and the ink is drawn on the tablet
  itself, from every pen sample, into the front buffer (like a note app),
  instead of reaching it as video through Second Screen. No Wi-Fi Direct.
- The PDF is rendered by the tablet, sharp at any zoom; the same tools as on
  the PC (pen colour and thickness, the saved signature, date, text, the
  S Pen's side button to erase, undo, clear, pages; pinch and double-tap to
  zoom, fingers ignored while the pen is near). Pen and saved signature are
  shared with the PC.
- Paired once by comparing a six-digit code on both screens; TLS with a key
  kept by Android's keystore on the tablet. Nothing else on the network can
  send PDFs to the app or read what is signed.
- Opens by itself (with the "display over other apps" permission) or from a
  notification; always ready in the background by default, with a quiet
  notification, or only for a while after the app is opened.
- A Wi-Fi drop while signing does not lose the signature: Plus One finds the
  app again and gets it when the user is done.
- **Several signatures**, on the PC and in the app: "My signature" places
  the last used with one tap, its arrow lists them (pick, remove, draw a new
  one on a pad). Kept in step between PC and tablet at each signing: what
  either side added is kept, what either side removed stays removed. 0.4's
  single saved signature becomes the first.
- **Select, move, turn and scale** what is on the page, on the tablet and
  on the PC, as note apps do: a lasso picks what it touches, a tap picks
  one mark; a box with a handle at each corner (scale) and a turn handle
  (the angle shown, snapping to right angles); drag from inside; Delete.
  A placed signature, date or text comes picked, to adjust it at once.
- The eraser works in the new-signature pad too.
- **A queue of PDFs**: select five PDFs in Files and Sign on Tablet, or send
  them one by one, also while signing: they reach the tablet in the order
  sent (one pairing question for all of them) and wait there. Done or ✕
  sends one back and opens the next; the documents button (and the app's
  main screen, and a notification) lists them, to sign them in any order,
  keeping what was drawn on each. The PC tells about them in one
  notification ("5 PDFs to sign on…", "Signed 5 PDFs"); its file chooser
  takes several PDFs. A PDF whose PC stopped waiting leaves the queue.
- **Scrolling through the pages**, as a note app shows a notebook: a PDF's
  pages one under the other, a finger scrolls (a flick carries on), the
  page in the middle of the screen counted at the top; the pen draws on
  whichever page it touches. An option in the app (*Documents of several
  pages*) brings back one page at a time with the arrows.
- **Pages there at once while scrolling**: each page is rendered whole at
  a modest resolution as soon as it comes into view (and the ones next to
  it ahead), then sharp tiles at the zoom, also while it still moves; both
  kept in memory, so going back shows them at once.
- **More than one tablet**: Sign on Tablet in Files becomes a menu of the
  tablets with the app on the network; the window's *Tablet for signing*
  picks one for good, or *Automatic*: the last one used (before, the one
  with the lowest address). A dropped link is picked up from the same
  tablet, never another.
- **Material 3 Expressive** look for the app, in the wallpaper's colours,
  light or dark as the system: grouped settings as in Android 16; signing
  under floating bars only (close and title, pages and queue, Done, the
  tools), no bar across the screen; Material dialogs, sliders and
  switches; a new icon.
- The app fits phones too (Android 13 or later): icons in the toolbar,
  clear of the system bars and cutouts, popups sized to the screen.
- When the app is not there, Second Screen as before.
- Sturdy: a Wi-Fi drop while signing is noticed within a minute and the
  signature still arrives; turning the tablet, dark mode, a keyboard cover
  or split screen keep the ink; Back asks before throwing a signature away;
  several PDFs at once share one pairing question.
- Builds in a container (`tools/android-docker.sh`, `Dockerfile.android`),
  nothing Android on the host; CI builds it, releases attach the signed APK.
- New dependency: `openssl` (the PC's certificate).

## 0.4.0

Sign on the tablet.

- **Sign on Tablet**: right-click a PDF in Files (or *Open With*, or the
  *Sign on Tablet* launcher). Plus One connects the tablet if needed and opens
  the document full screen on it; the S Pen signs with pressure, fingers never
  draw (palm rejection) and pinch to zoom. *Done* saves `name-signed.pdf`
  next to the original, untouched, and the tablet disconnects again if it was
  connected for this.
- Hold the S Pen's side button to erase whole strokes; undo restores them,
  and also a cleared page.
- Pen colour (five inks) and thickness, down to a fine 0.3 pt (most of the
  slider is for thin pens), remembered; pressure shows in the line.
- A cleaner signing window: the page floats on a soft shadow (light and dark
  themes), tools sit in a floating bar under it with an eraser button, *My
  signature* shows your signature, a faint preview follows the pen while
  placing it, and messages are toasts.
- No more needles and bumps in pen strokes: GTK also reports the pen as a
  mouse drag (from a device that calls itself a mouse), and those late
  positions, with a fixed pressure, were mixed into the stroke.
- Ink like a note app's: strokes go through a stroke model (the one
  Google's ink-stroke-modeler describes): the pen's jitter is
  smoothed, curves come out round and the stroke still ends exactly where
  the pen lifted. A *Stabilization* setting (0-10) sets how
  much; while drawing, the line runs ahead to where the pen is predicted to
  be, which hides part of the delay.
- While the signing window fills the tablet it draws from the pen reports
  as they arrive (about 285 a second), not from desktop events.
- Saved signature (draw once, place with one tap), date and text fields, undo,
  page navigation.
- The signature is stamped onto the original pages (pypdf), so text, forms and
  metadata are kept; rotated pages are handled.
- Falls back to signing on the PC when the tablet does not connect, and to a
  save dialog when the folder is not writable (Flatpak).
- *Sign on Tablet* in Files shows only while a tablet is on the network
  (it says so through Quick Share or KDE Connect) or connected, and a switch
  in the window's options turns it off everywhere (Files, *Open With*, the
  app grid). The window shows which tablet it sees.
- D-Bus method `SignDocument(path)` and property `SignAvailable`.
- Connecting after a change of Wi-Fi network no longer takes minutes:
  - a docker or VPN interface going away no longer passes for the Wi-Fi
    device going away (which left Plus One waiting for it until it gave up);
  - Plus One keeps looking for the tablet for as long as it waits for it
    (NetworkManager stopped looking after 30 s, so a tablet opened later was
    not seen);
  - when the tablet drops out of sight between two attempts, Plus One looks
    for it again instead of failing on the old one until it gives up;
  - when the Intel Wi-Fi firmware crashes while joining the tablet, Plus One
    asks to close and reopen Second Screen (the tablet otherwise ignores new
    attempts for minutes) and waits the full time again; the request is a
    notification, so it shows with the window closed too.
- Asking again to sign a PDF that is waiting for the tablet, or already open,
  no longer opens it twice: *Done* closed one window, and the others kept the
  tablet connected.
- Less lag:
  - each frame reaches the tablet a frame sooner: the MPEG-TS muxer and RTP
    payloader held every frame's last packets until the next frame came
    (16 ms at 60 Hz, up to 100 ms for the last change on a still screen);
    Plus One now sends each frame whole as soon as it is encoded;
  - the signing window draws 60 frames a second while you write (it managed
    33-40): the page is a texture made once, and a frame only draws the
    newest bit of the stroke (3.5 ms a frame instead of 10);
  - the eraser keeps up: it skips marks nowhere near it (checking every
    segment of every mark took 7 ms per pen sample on a short signature),
    and erasing or undoing redraws only that area of the page, whose
    texture is cut in tiles so only the tiles it covers are uploaded again;
  - zooming in and out stays smooth: while fingers pinch the page is only
    magnified (a frame took up to 144 ms rendering it again), only the
    tiles on screen are rendered, and tiles not ready yet render a few per
    frame over a small copy of the whole page instead of in one go (up to
    200 ms after a pinch, 44 ms frames while zooming out);
  - the first connection after Plus One starts is 1.5 s quicker: the NVENC
    plugin (CUDA probing the GPUs) loads while the tablet is being found,
    not after it connected;
  - far fewer lost packets: on a still screen, 1 in two minutes instead of
    35 in a side-by-side test, and each one made the tablet ask for a whole
    new frame (black flashes, ghost cursors).
- No more getting stuck:
  - no Wi-Fi Direct device, NetworkManager unreachable, the virtual monitor
    or the pipeline failing, or adb timing out now end with an error instead
    of waiting (or showing "Connected") forever;
  - searching for the tablet gives up on time even when it stays listed
    but never answers (it could keep the radio busy for an hour);
  - the tablet resuming a paused session no longer starts a second
    virtual monitor and stream;
  - fingers held down are released whatever ends the touch connection;
  - each session's pipeline is freed when it ends;
  - the Wi-Fi Direct group no longer comes up after *Disconnect* when the
    two crossed.
- *Settings* opens the settings window even while a signing window is open;
  *Cancel* and closing wait while the signed copy is being saved.
- New dependencies: `gir1.2-poppler-0.18`, `python3-pypdf` (`.deb`); the
  Flatpak builds poppler 25.12 and pypdf.

## 0.3.0

Always ready, one click away.

- *Tablet* toggle in GNOME's Quick Settings (GNOME Shell 45–49): connect and
  disconnect with one click, state as subtitle, icon in the top bar while
  connected. Shipped in the `.deb` but off: turn it on from Plus One's window
  or the Extensions app (Plus One never enables shell extensions by itself).
  A zip for Flatpak users is attached to the release.
- Plus One runs in the background from login (*Ready in the background*, on by
  default): no window needed, nothing running on the radio while idle.
  D-Bus interface `io.github.gabrielesantangelo.PlusOne1` for the toggle,
  scripts and keyboard shortcuts; D-Bus activation starts the app on demand.
- Notifications: connected (with *Disconnect*), tablet not found, problems.
- Searching for the tablet is time-boxed (2 minutes after a click, 90 seconds
  after Second Screen closes) because Wi-Fi Direct scanning delays other Wi-Fi
  traffic (measured: 20% of packets over 30 ms while searching).
- Touch, resolution and quality settings apply from the next connection
  without restarting anything.
- Ending a session also closes its touch connection, which used to linger
  for about 30 s after a reconnect.
- New app ID `io.github.gabrielesantangelo.PlusOne` (needed for Flathub).
  Flatpak users: `flatpak uninstall io.github.santal.PlusOne` after
  installing this version.

## 0.2.0

- Flatpak bundle (GNOME 50 runtime) built in CI and attached to releases.
- Encoder `vah264enc` (GStreamer va plugin) for Intel/AMD where gstreamer-vaapi is gone.
- Touch kept working after Stop then Start in the same window (the input
  port stayed busy).
- Survives the Wi-Fi device being reset by the driver (seen with Intel
  firmware crashes) instead of hanging in "Connecting…".
- GNOME Network Displays detected over D-Bus, which also works in a sandbox.

Install: `sudo apt install ./plus-one_0.2.0_all.deb` (Ubuntu 24.04), or
`flatpak install --user ./plus-one_v0.2.0_x86_64.flatpak` (any distribution
with GNOME on Wayland; see the README for S Pen pressure in the Flatpak).

## 0.1.0

First release.

- Extends the GNOME desktop onto a Samsung Galaxy Tab through its built-in
  Second Screen app: no app to install on the tablet.
- Virtual monitor through Mutter (shows up in Settings → Displays). The
  resolution is read from the tablet's EDID (1920×1200 at 60 Hz on a
  Tab S10 FE+), or chosen by hand.
- Wi-Fi Direct link that reconnects by itself when Second Screen is reopened.
- Low latency (~50 ms measured): Windows source identity, video-only stream,
  NVENC/VA-API/x264 tuned for zero latency, tablet set to its low-latency mode.
- Finger touch, and the S Pen as a real Linux tablet with pressure and tilt.
- GTK4/libadwaita window in English and Italian.

Install: `sudo apt install ./plus-one_0.1.0_all.deb` (Ubuntu 24.04, GNOME on Wayland).
