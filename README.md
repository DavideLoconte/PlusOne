<p align="center">
  <img src="data/io.github.gabrielesantangelo.PlusOne.svg" width="128" alt="Plus One icon">
</p>

<h1 align="center">Plus One</h1>

<p align="center">
  <a href="https://github.com/GabrieleSantangelo/PlusOne/actions/workflows/ci.yml"><img src="https://github.com/GabrieleSantangelo/PlusOne/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/GabrieleSantangelo/PlusOne/releases/latest"><img src="https://img.shields.io/github/v/release/GabrieleSantangelo/PlusOne" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT"></a>
</p>

<p align="center">Your Samsung Galaxy Tab as a wireless second monitor for GNOME on Linux.<br>
No app to install on the tablet: it talks to the <b>Second Screen</b> feature Samsung ships for Windows PCs.</p>

---

## Features

- **Real extended display.** A virtual monitor appears in *Settings → Displays*;
  drag windows onto the tablet like onto any other screen.
- **Nothing to install on the tablet.** Uses the built-in *Second screen* quick
  settings tile (One UI).
- **Low latency.** About 50 ms end to end in our measurements, 60 fps, hardware
  H.264 (NVENC, VA-API) or x264.
- **Touch and S Pen.** Fingers drive the desktop; the S Pen becomes a real Linux
  drawing tablet with pressure (4096 levels), tilt and hover.
- **Sign PDFs on the tablet.** Right-click a PDF → *Sign on Tablet*: the page
  opens full screen on the tablet, you sign with the pen, a signed copy
  appears next to the original.
- **One click.** A *Tablet* toggle in GNOME's Quick Settings, a notification
  with *Disconnect*, and Plus One ready in the background from login.
- **Wireless, no setup.** Wi-Fi Direct between PC and tablet, no router needed,
  no developer options on the tablet. Reconnects by itself when you reopen
  Second Screen soon after closing it.
- **Correct aspect ratio, automatically.** The tablet's preferred mode is read
  from its EDID (1920×1200 on a Tab S10 FE+), so the picture fills the screen.
- **Simple GNOME app**, in English and Italian.

## Requirements

| | |
|---|---|
| **PC** | Linux with GNOME 46+ on **Wayland** (Mutter), NetworkManager, a Wi-Fi card with P2P (Wi-Fi Direct) support. Tested on Ubuntu 24.04. |
| **Encoder** | One of: NVIDIA (NVENC), Intel/AMD (VA-API), or software x264. |
| **Tablet** | A Galaxy Tab with *Second screen* in quick settings (One UI 5.1.1 or later). Tested on a Galaxy Tab S10 FE+ with One UI 8.5. |

## Install

### Debian / Ubuntu package

Download `plus-one_<version>_all.deb` from the
[Releases](../../releases) page, then:

```bash
sudo apt install ./plus-one_0.4.0_all.deb
```

The package pulls in GTK 4, libadwaita, GStreamer and NetworkManager bindings
from your distribution. It also installs a udev rule that lets the members of
the `plus-one` group create virtual HID devices (`/dev/uhid`), which is how the
S Pen becomes a tablet with pressure and tilt. Join the group once, then log
out and back in:

```bash
sudo usermod -aG plus-one $USER
```

Plus One only ever creates the S Pen's own device, but the access is the
user's: any program you run can then create a virtual keyboard or mouse too,
and type or click in your session (Wayland's isolation does not stop that).
That is why it is a group you choose to join. Outside it the pen works as a
mouse; to leave it, `sudo gpasswd -d $USER plus-one`.

The *Tablet* Quick Settings toggle (a GNOME Shell extension) is included but
off: after logging out and back in, turn it on in Plus One's window
(*Options → Quick Settings toggle*) or in the *Extensions* app. Plus One never
enables shell extensions by itself.

Uninstall with `sudo apt remove plus-one`.

### Flatpak (any distribution with GNOME)

Download `plus-one_<version>_x86_64.flatpak` from the [Releases](../../releases)
page, then:

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user ./plus-one_v0.4.0_x86_64.flatpak
```

The GNOME runtime it needs comes from Flathub. Differences from the `.deb`:

- A sandbox cannot install udev rules, so for S Pen pressure and tilt run once
  on the host, then log out and back in (without it the pen works as a mouse):

  ```bash
  sudo sh -c 'groupadd --system -f plus-one && usermod -aG plus-one "$SUDO_USER"
              echo "KERNEL==\"uhid\", SUBSYSTEM==\"misc\", GROUP=\"plus-one\", MODE=\"0660\"" > /etc/udev/rules.d/70-plus-one-uhid.rules
              echo uhid > /etc/modules-load.d/plus-one-uhid.conf
              modprobe uhid && udevadm control --reload
              udevadm trigger --action=change --subsystem-match=misc --sysname-match=uhid'
  ```
- No *Open Second Screen from here* (adb is not in the sandbox).
- The sandbox is not much of a boundary: Plus One needs GNOME's screen
  capture and remote input without asking (the virtual monitor and the
  tablet's touches), the host's dconf (mapping the pen to the monitor) and
  the devices (`/dev/uhid`).
- The Quick Settings toggle is a GNOME Shell extension, which a Flatpak cannot
  install. Get `plus-one@gabrielesantangelo.github.io.shell-extension.zip` from
  the same release, then run
  `gnome-extensions install plus-one@gabrielesantangelo.github.io.shell-extension.zip`,
  log out and back in, and enable it in *Extensions*.
- Upgrading from 0.2: the app ID changed, so remove the old one with
  `flatpak uninstall io.github.santal.PlusOne`.

### From source

```bash
git clone https://github.com/GabrieleSantangelo/PlusOne.git plus-one && cd plus-one
./install.sh               # menu entry, icon, Quick Settings toggle for your user, no root
sudo ./enable-pen.sh       # optional: S Pen pressure and tilt (then log in again)
```

Dependencies on Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gstreamer-1.0 gir1.2-nm-1.0 \
    gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-vaapi gstreamer1.0-x
```

## Usage

1. Click **Tablet** in GNOME's Quick Settings (top-right corner), or press
   **Connect** in the Plus One window.
2. On the tablet, swipe down for quick settings and tap **Second screen**.
3. A few seconds later the tablet shows a new monitor, to the right of your
   other screens. Arrange it in *Settings → Displays*.

The order does not matter: you can tap the tablet first. To stop, click the
toggle again, use *Disconnect* in the notification, or close Second Screen on
the tablet; reopening it within a minute and a half reconnects by itself.

Plus One runs in the background from login (*Ready in the background*), so the
toggle answers at once; idle, it uses no radio and no CPU. It only searches for
the tablet after you ask, and gives up after two minutes without an answer:
while it searches, the Wi-Fi card also scans for Wi-Fi Direct devices and
other traffic sees occasional delays of about 100 ms.

To connect from a keyboard shortcut, bind this command in *Settings → Keyboard
→ Custom Shortcuts* (`Disconnect` works the same way):

```bash
gdbus call --session -d io.github.gabrielesantangelo.PlusOne -o /io/github/gabrielesantangelo/PlusOne \
    -m io.github.gabrielesantangelo.PlusOne1.Connect
```

### Sign a PDF on the tablet

Right-click a PDF in Files → **Sign on Tablet** (or *Open With → Sign on
Tablet*, or launch *Sign on Tablet* from the app grid and pick a file). Files
offers it while a tablet is on your network or connected: Plus One sees it
when the tablet announces itself, which Android's Quick Share and KDE Connect
both do. **Options → Sign on Tablet** in the Plus One window turns it off
everywhere, and shows which tablet Plus One sees.

1. If the tablet is not connected yet, Plus One connects it: tap **Second
   screen** on the tablet.
2. The document opens full screen on the tablet. Sign with the S Pen: it
   draws with pressure, and erases while you hold its side button; fingers
   never draw, so resting your hand is fine. Pinch with two fingers to zoom
   and move.
3. Tap **Done**. `name-signed.pdf` is saved next to the original (which is
   left untouched) and a notification offers *Open* and *Show in folder*. If
   the connection was opened for signing, the tablet disconnects again.

The pen button in the toolbar sets the colour, the thickness and the
stabilization (how much the ink smooths the pen's movement); Plus One
remembers them. **My signature** places your saved signature with one tap;
its arrow lists them all: pick another, remove one, or draw a new one on a
pad (with the pen or the mouse). The first time, **Keep as my signature**
also saves what you just drew. **Options → My signatures** in the Plus One
window manages them too. **Date** and **Text** add a date or a few words
where you tap; **Undo**, **Clear page**, page arrows and **Fit**.

What you place comes selected, in a blue box: drag it from inside, turn it
with the round handle, resize it from a corner; tap elsewhere when it is
right. The **lasso** button selects anything on the page the same way:
draw around it, or tap a stroke; **Delete** (or the Delete key) removes
the selection.
If the tablet does not connect, the notification offers *Sign on this PC*
(mouse or a pen tablet). The signature is drawn into the page (a simple
electronic signature), not a cryptographic digital signature.

The Files menu entry needs `python3-nautilus` (installed with the `.deb` by
default) and appears after Files restarts (`nautilus -q`). In the Flatpak,
use *Open With*; if the folder cannot be written, Plus One asks where to save.

### The companion app: ink under the nib

Through Second Screen, the ink reaches the tablet as video, 70-100 ms behind
the pen. Plus One's app for the tablet draws it on the tablet itself, as a
note app does, and needs neither Second Screen nor Wi-Fi Direct: the PC finds
it on the home network.

1. Install `plus-one-companion_*.apk` from the release on the tablet
   (Android 13 or later; allow installing from your browser or file manager
   when Android asks), and open **Plus One** once.
2. In the app, allow it to **display over other apps**, so a PDF sent from
   the PC opens by itself (without it, a notification to tap).
3. Sign on Tablet from Files as above. The first time, the PC and the tablet
   both show the same six-digit code: confirm it on both. From then on they
   recognize each other, and nothing else on the network can send PDFs to
   the tablet or read what you sign.

The app has the same tools (pen colour and thickness, your signatures,
date, text, the S Pen's button to erase, undo, pinch to zoom). The pen and
the signatures are shared with the PC: each time you sign, what you added
or removed on either side reaches the other. The app lists them too, under
**My signatures**. When it is on the network, Sign on
Tablet uses it; otherwise Second Screen as before. It waits for the PC with a
quiet notification; **Ready in the background** in the app turns that off (it
is then ready for 10 minutes after you open it). If the Wi-Fi drops while you
sign, Plus One finds the app again and gets the signature when you are done.

**More than one tablet.** With two or more tablets running the app on the
network, *Sign on Tablet* in Files asks which; **Options → Tablet for
signing** in the window picks one for good, or *Automatic*, the last one
used.

**Several pages.** The pages lie one under the other, as in a note app: a
finger scrolls through them, and the pen signs on whichever page it touches.
In the app, **Documents of several pages → Scroll through the pages** off
shows one page at a time, turned with the arrows at the top.

**Several PDFs at once.** Select them all in Files → *Sign on Tablet* (or
send them one at a time, also while you sign): they wait on the tablet in the
order you sent them. The first opens; *Done* (or ✕, to leave one unsigned)
sends it back to the PC and opens the next. The documents button in the
top bar lists them, to sign them in any order: what you drew on one stays
there while you look at another. The app's main screen lists them too, under
**To sign**, and so does a notification.

### Recommended once: tablet low-latency mode

Second Screen reads a configuration file. Creating it cuts its internal
buffering (measured roughly 90 → 50 ms together with the other tweaks):

1. On the tablet, open *My Files* → *Internal storage* → *Documents*.
2. Create a folder `wfd` and in it a text file `wfdconfig.txt` containing:

   ```
   sink_latency_mode low
   ```

If the tablet is connected over adb (see below) Plus One writes this file for you.

### Settings

| Setting | |
|---|---|
| Resolution | *Automatic* (default) reads the tablet's preferred mode from its EDID (1920×1200 on a Tab S10 FE+), or pick 1920×1200, 1680×1050, 1440×900, 1280×800 (16:10), 1920×1080, 1280×720 (16:9). |
| Quality | Video bitrate in Mbit/s. 20 is a good default; raise it for crisper text. |
| Touch and S Pen | Forward finger touches and the pen from the tablet. |
| Quick Settings toggle | Turns the *Tablet* GNOME Shell extension on or off. Shown when the extension is installed (the `.deb`, or the zip for Flatpak users) and GNOME Shell knows it, which is after the next login. |
| Ready in the background | Start at login and keep running after the window closes, so the Quick Settings toggle connects at once. Costs nothing while idle. |
| Open Second Screen from here | Uses `adb` to open the tile for you. Needs *USB debugging* or *Wireless debugging* on the tablet; off by default. |
| Language | System, English or Italiano. |

### S Pen

The first time you bring the pen near the screen after connecting, Plus One
creates a virtual tablet called **Plus One S Pen** and maps it onto the tablet's
monitor; to Linux it is an ordinary drawing tablet, so apps with tablet support
get pressure and tilt. GIMP 2.10 runs through XWayland: restart it after the pen
has appeared, then in *Edit → Input Devices* set **xwayland-tablet stylus** to
*Screen*.

Without `/dev/uhid` access the pen still works as a mouse (hover moves the
pointer, tip clicks, side button right-clicks).

### Command line

```bash
plus-one --cli                       # same engine, no window; mode from the tablet
plus-one --cli --size 1680x1050      # a fixed mode
plus-one --cli --test                # test pattern + local preview window
plus-one --help
```

`--test` shows the same moving timer on the PC and on the tablet: photograph
both screens together and the difference between the two timers is the
latency.

## Troubleshooting

| Problem | What to do |
|---|---|
| *“Close GNOME Network Displays”* | It uses the same Wi-Fi Direct device. Quit it. |
| Never connects | Reopen Second Screen on the tablet. A VPN with a firewall/kill switch can block the link; turn it off to check. Open *Log* at the bottom of the window. |
| *“Connection is not secure”* on the tablet | Shown at every connection: the stream is not HDCP-protected. Harmless. |
| Picture squashed or with black bars | Use one of the listed modes; the tablet lays out the picture by the negotiated mode. |
| Stutter / artefacts | Lower *Quality*, move closer, avoid 2.4 GHz congestion. Packet loss shows in the log. |
| Pen not in GIMP | See the S Pen section (restart GIMP, enable *xwayland-tablet stylus*). |
| App icon or Quick Settings toggle missing after install | Log out and back in; GNOME Shell only looks for new icons and extensions at login. |
| Wi-Fi slower for a moment | Expected while Plus One searches for the tablet (up to two minutes after you click *Tablet*); it stops once connected or after giving up. |

## How it works

```
GNOME (Mutter)                    Plus One                          Galaxy Tab
──────────────                    ────────                          ──────────
virtual monitor ──PipeWire──▶ capture ▶ H.264 ▶ MPEG-TS/RTP ──UDP──▶ Second Screen
                                       ▲                              (Miracast sink)
remote desktop  ◀──── touch ───── UIBC server ◀──────── TCP ──────────── touch / S Pen
/dev/uhid       ◀──── S Pen ─────┘
                         Wi-Fi Direct group + WFD RTSP session (NetworkManager)
```

- **Display**: `org.gnome.Mutter.ScreenCast.RecordVirtual` creates the monitor
  without a consent dialog; its size follows the negotiated stream.
- **Link**: Second Screen is a Wi-Fi Display (Miracast) sink. Plus One forms the
  Wi-Fi Direct group through NetworkManager and acts as the WFD source (RTSP
  M1–M7, keep-alives, IDR requests).
- **Tuning for Samsung's sink** (found by reverse engineering the tablet side):
  the source identifies as a Windows Miracast source, which selects the sink's
  PC profile and makes it share its EDID; the EDID's preferred mode is
  negotiated exactly (a VESA mode for 16:10) so the picture fills the screen; the stream is video-only, so frames are shown on
  arrival instead of being synchronised to an audio clock.
- **Input**: UIBC generic events become Mutter remote-desktop touches; the pen
  arrives as an HID device whose descriptor and reports are replayed into
  `/dev/uhid`.
- **Application**: one GApplication process holds the connection and offers it
  over D-Bus (`io.github.gabrielesantangelo.PlusOne1`: `Connect`, `Disconnect`,
  `ShowWindow`, `SignDocument(path)`, properties `State`, `Active`, `Title`, `Detail`, `Background`).
  The window, the notifications and the Quick Settings extension are front
  ends; D-Bus activation starts the process when one of them needs it.

Details, evidence and the tools used are in [docs/FINDINGS.md](docs/FINDINGS.md).

What comes next, and the convenience rules every feature follows, is in
[docs/ROADMAP.md](docs/ROADMAP.md).

## Development

```
plusone/          the app: app.py (background app, D-Bus, notifications), gui.py (window),
                  engine.py (ties display, p2p, rtsp, media, uibc, pen together),
                  signing.py + sign.py + pdf.py + ink.py + modeler.py (Sign on Tablet),
                  nearby.py (tablets on the network)
data/gnome-shell/ the Quick Settings extension
data/nautilus/    the Files context-menu entry
android/          the companion app for the tablet (Kotlin, 0.5, in progress)
tests/            unit and protocol tests (a scripted fake tablet, no hardware needed)
packaging/        build-deb.sh and maintainer scripts
tools/            CI scripts; tools/re/ has the reverse-engineering and measurement helpers
docs/FINDINGS.md  protocol and reverse-engineering notes
```

Run the app from the checkout with `bin/plus-one`.

Tests:

```bash
python3 -m pytest tests          # quick, on the host
tools/ci-docker.sh               # everything CI runs, in an Ubuntu 24.04 container
```

The companion app builds in a container too, so no Android SDK goes on the
host: `tools/android-docker.sh` (Gradle tasks as arguments, `assembleDebug`
by default) puts the APK in `android/app/build/outputs/apk/`; install it with
`adb install -r`. Building `Dockerfile.android` accepts the Android SDK
license.

`tools/ci-docker.sh` builds `Dockerfile.ci`, runs the tests, builds the `.deb`
into `dist/` and checks it with lintian. GitHub Actions runs the same container
on every push and pull request, and also builds the Flatpak.

Flatpak, locally:

```bash
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//50
flatpak run org.flatpak.Builder --user --install --force-clean flatpak-build \
    packaging/flatpak/io.github.gabrielesantangelo.PlusOne.yml
```

### Releasing

1. Bump `__version__` in `plusone/__init__.py` and add a section to
   `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v0.4.0 && git push origin main v0.4.0`.
3. The *Release* workflow checks that the tag matches the version, runs the
   tests, builds the `.deb` and the Flatpak bundle and publishes a GitHub
   release with both, the Quick Settings extension zip and the changelog
   section as notes. No tests, no release.
4. With the secrets `ANDROID_KEYSTORE` (the release key, a PKCS#12 file in
   base64) and `ANDROID_KEYSTORE_PASSWORD`, it also builds the companion app,
   signed, and attaches the APK. The app's version is Plus One's.

## Acknowledgements

- [GNOME Network Displays](https://gitlab.gnome.org/GNOME/gnome-network-displays),
  the reference for Wi-Fi Direct and WFD on GNOME.
- [segunda-tela-linux](https://github.com/paulorochagodoi/segunda-tela-linux),
  whose notes on the Windows side of Second Screen were the starting point.
- [REA](https://github.com/morluto/rea) and JADX, used to study the tablet's
  receiver.

## Disclaimer

Plus One is an independent project, not affiliated with or endorsed by
Samsung. Samsung, Galaxy and Second Screen are trademarks of Samsung
Electronics. No Samsung code or binaries are included.

## License

[MIT](LICENSE)
