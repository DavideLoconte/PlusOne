# Changelog

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
