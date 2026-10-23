# Changelog

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
