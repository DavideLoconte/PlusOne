# secondscreen-linux

Use a Samsung Galaxy Tab as a wireless extended monitor for GNOME on Linux,
through the tablet's built-in **Second Screen** app (the one Samsung ships for
Windows PCs). No app to install on the tablet.

Status: working prototype, tested with a Galaxy Tab S10 FE+ (One UI 8.5) and
Ubuntu 24.04 / GNOME 46 on Wayland.

## How it works

- **Virtual monitor**: created through Mutter's ScreenCast/RemoteDesktop D-Bus
  API (no consent dialog); it shows up in Settings → Displays.
- **Link**: Wi-Fi Direct through NetworkManager, then a Miracast (WFD) RTSP
  session in which this program is the source.
- **Video**: PipeWire → NVENC (or VA-API/x264) H.264, video-only MPEG-TS over
  RTP, negotiated at the exact stream resolution (1920×1200 by default).
- **Touch**: WFD UIBC events from the tablet are injected as touch input.

Tablet-side tweaks found by reverse engineering Second Screen (see
`docs/FINDINGS.md`): the source identifies as a Windows Miracast source, the
sink gets `sink_latency_mode low` in `/sdcard/Documents/wfd/wfdconfig.txt`,
and the stream carries no audio so frames are shown without A/V sync.

## Usage

```bash
./install.sh          # once: adds "Schermo Tablet" to the app grid
```

Open **Schermo Tablet**, press **Avvia**, then tap **Second screen** in the
tablet's quick settings. The tablet becomes a third monitor within a few
seconds (arrange it in Settings → Displays). Closing Second Screen on the
tablet puts the app back in waiting mode; reopen the tile to reconnect.

Command line:

```bash
bin/schermo-tablet --cli              # same engine, no window
bin/schermo-tablet --cli --test       # test pattern + local preview (latency photos)
```

Close GNOME Network Displays first: it competes for the Wi-Fi Direct device.

Requirements: GNOME (Mutter) on Wayland, NetworkManager with Wi-Fi P2P,
GStreamer 1.24 with `pipewiresrc`, `mpegtsmux`, `rtpmp2tpay` and an H.264
encoder, PyGObject with NM and Gst typelibs. Optional: `adb` with wireless
debugging paired to the tablet.
