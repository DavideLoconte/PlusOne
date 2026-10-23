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
python3 -m secondscreen            # opens Second Screen over adb if available
python3 -m secondscreen --test     # test pattern instead of a virtual monitor
```

Without adb, open the **Second screen** tile on the tablet first.

Requirements: GNOME (Mutter) on Wayland, NetworkManager with Wi-Fi P2P,
GStreamer 1.24 with `pipewiresrc`, `mpegtsmux`, `rtpmp2tpay` and an H.264
encoder, PyGObject with NM and Gst typelibs. Optional: `adb` with wireless
debugging paired to the tablet.
