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
