# Plus One roadmap

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
- **D-Bus activation and autostart** (systemd user unit for the .deb, XDG
  autostart plus the background portal for the Flatpak). Idle cost: a sleeping
  process; Wi-Fi Direct is only touched when a task needs it.
- **Leases on the link**: every task holds one; the link closes when the last
  lease goes, unless the user pinned *Extend*.
- The current window becomes the settings and status page of the service.

## Milestones

### 0.3 — Always ready

- Service, D-Bus API, activation, autostart; the window talks to the service.
- **GNOME Shell Quick Settings tile "Tablet"**: on/off for *Extend*, menu with
  *Mirror*, *Graphics tablet*, *Disconnect*; subtitle shows the state.
- Notifications: "Tap Second screen on the tablet", "Connected", errors with
  a hint.
- App ID moved to `io.github.gabrielesantangelo.PlusOne` before more people
  install it (needed for Flathub; settings migrated).

Acceptance: from a fresh login, tablet as a monitor = 1 click + 1 tap.

### 0.4 — Sign on the tablet

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

### 0.5 — Tablet modes and picture quality

- **Mirror** an existing monitor (presentations, teaching).
- **Graphics tablet**: the pen drives a chosen PC monitor like a Wacom while
  the tablet mirrors it; switch from Quick Settings.
- **2560×1600** through WFD R2 for sharper text (VESA index 33 is in the
  tablet's table).
- **Portrait**, if the sink accepts it (research `wfd_sec_portrait_display` and
  the 7260 side channel).
- **Adaptive bitrate** from the RTCP loss reports the tablet already sends.
- Optional **audio on the tablet** for videos (costs ~40 ms of latency).

### 0.6 — Quick Share

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

### 0.7 — Polish

- Optional USB mode through adb and the MS-MICE path (steadier link for
  people with a free port).
- Flathub submission.
- Documentation, screenshots, translations.

### Research track (in parallel)

- **Open Second Screen without touching the tablet**: Windows wakes it over
  Bluetooth through Samsung MCF; reverse engineer the start intent
  (`com.samsung.android.mcfserver`, the Windows MCF library). This would remove
  the last tap.
- Persistent Wi-Fi Direct groups for faster reconnects.
- Mitigations for Intel `iwlwifi` firmware crashes during group formation.

## Not planned

Clipboard sharing, phone notifications and remote input: GSConnect and KDE
Connect already do them well.
