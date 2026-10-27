# Plus One's protocol

How a computer (Linux: `plusone/`, macOS: `macos/`) and Plus One's tablet
app (`android/`) talk. Any computer app and any tablet app that follow it
work together. Version **3**.

The tablet app is the server: it listens, the computer connects.

## Finding the tablet

The app announces itself with mDNS/Bonjour as `_plusone._tcp`, port 47236
(another if that one is taken). TXT entries:

| Key    | Value                                                          |
|--------|----------------------------------------------------------------|
| `id`   | the first 16 hex digits of the app's certificate's fingerprint |
| `name` | the tablet's name, for people                                  |

An `id` that belongs to a paired tablet must come with that tablet's
certificate: another one is an impostor, and the computer does not connect.

## The link

TLS 1.3, each side presenting a self-signed certificate (EC P-256). No CA
vouches for either: the app's certificate is checked against the paired
list, or the user compares a code (pairing). A **fingerprint** is the
SHA-256 of a certificate's DER, in lowercase hex.

Messages, both ways: a 4-byte big-endian length, then a JSON object of that
length. A message with a `"size"` is followed by that many payload bytes
(only the computer sends payloads: a PDF). Before pairing, a message is at
most 4 KiB; after, 16 MiB.

### Hello

The computer first, then the app:

```json
{"type": "hello", "name": "Davide's Mac", "version": 3, "paired": true, "pair": false}
{"type": "hello", "name": "Galaxy Tab S6 Lite", "version": 3, "paired": true, "at": "192.168.0.204"}
```

`paired`: whether this side knows the other's certificate. `pair` (from the
computer): the user asked to pair with this tablet. `at` (from the app): the
address the computer reached. Different versions: both stop.

### Pairing

When either side does not know the other, and the computer said `"pair": true`:

1. app → `{"type": "pair-commit", "commit": hex(SHA-256(app nonce ‖ app cert ‖ computer cert))}`
2. computer → `{"type": "pair-nonce", "nonce": hex(16 random bytes)}`
3. app → `{"type": "pair-nonce", "nonce": hex(its 16 random bytes)}`; the computer checks the commitment
4. Both show the **code**: the first 4 bytes, as a big-endian integer, of
   SHA-256(lower cert ‖ higher cert ‖ computer nonce ‖ app nonce) modulo
   1,000,000, as six digits. The certificates (DER) are ordered bytewise.
5. Each sends `{"type": "pair-ok"}` or `{"type": "pair-no"}` once its user
   answers; both `pair-ok`: each remembers the other's certificate.

The app commits to its nonce before it sees the computer's, so someone in
the middle cannot make both codes match but by chance.

### Other messages

`{"type": "ping"}` keeps a link alive (the app drops a paired computer
silent for 90 s); `{"type": "bye"}` ends one. Signing PDFs (`sign`,
`opened`, `signed`, `cancelled`, `got`, `resume`) and the camera,
microphone and speakers have their own messages: `plusone/companion.py`,
`plusone/rtc.py` and `CompanionService.kt` are their reference for now.

## The display

The computer asks for a session on a paired link:

```json
{"type": "display", "key": "<64 hex digits: a fresh AES-256 key>", "port": 50123, "codec": "hevc"}
{"type": "display-ready", "port": 41089, "width": 2000, "height": 1200, "refresh": 60.0, "codec": "hevc"}
```

`codec`: `"avc"` (H.264, the default) or `"hevc"`. The computer asks; the app
answers with the one it will decode (`"avc"` if it has no hardware HEVC
decoder), and the computer encodes that. No `codec` in the answer: H.264.

or `{"type": "refused", "reason": "off" | "permission" | "background" | "unavailable"}`.
`port` (each side's): its UDP port. The computer chooses a size at the
tablet's proportions, makes a display of it, and sends it encoded. The link
stays open: pings every 5 s, `{"type": "stop"}` to end; the app closing it
ends the session.

### Packets

UDP, each sealed with AES-GCM under the session's key:

    nonce (12 bytes: direction u32, count u64) | ciphertext | tag (16 bytes)

Direction 1 from the computer, 2 from the app; each side counts its packets
from 0 and drops one whose count is not higher than the last it opened. All
numbers are big-endian unless said. The body's first byte is its kind:

| Kind | Name     | From     | After the kind                                                      |
|------|----------|----------|---------------------------------------------------------------------|
| 1    | VIDEO    | computer | flags u8 (1: keyframe), frame u32, index u16, count u16, captured i64, H.264 |
| 2    | KEYFRAME | app      | nothing: the app needs one (it asks again until one comes)          |
| 3    | PING     | app      | its time i64, shown u8 (whether it shows the desktop)              |
| 4    | PONG     | computer | the ping's time i64, the computer's time i64                        |
| 5    | STATS    | app      | shown u32, lost u32, then six u16 in tenths of a ms: capture-to-decoded and first-packet-to-decoded, each median, 90th percentile, highest |
| 6    | INPUT    | app      | fingers u8, per finger id u8, x u16, y u16 (0–65535 of the screen); pen samples u8, per sample 9 bytes |

**Video.** H.264 (High profile) or HEVC (Main), Annex B (start codes), no
B-frames; a keyframe carries its parameter sets (H.264's SPS and PPS,
HEVC's VPS, SPS and PPS). Each frame is cut into packets of at most
1426 bytes of H.264, so a packet with its header, nonce and tag fits one
Ethernet frame. `captured` is the computer's monotonic clock in ns when the
frame was captured; PING/PONG let the app match the two clocks and report
the delay. The app decodes a frame once all its packets came, and asks for
a keyframe after a loss.

**Input.** Each INPUT says what touches the screen now (the app repeats it
every 0.1 s while anything does): a finger missing since the last one was
lifted. Nothing heard for 0.4 s: everything is lifted. A pen sample is the
S Pen's HID report without its ID, little-endian: bits u8 (0x01 tip,
0x02 barrel, 0x10 in range), x u16 and y u16 (0–32767), pressure u16
(0–4096), tilt x s8 and tilt y s8 (degrees).

**Visibility.** While the app does not show the desktop (PING's `shown` is
0), the computer stops capturing; when it shows it again, a keyframe.

## The cable

Over USB-C, without tethering or developer options, through Android Open
Accessory: the computer is the USB host and asks the tablet (control
requests 51, 52, 53) to become an accessory with manufacturer `Plus One`,
model `Plus One`, description `Plus One on the PC`, version `1`. The tablet
asks its user once, then opens the app.

One bulk stream each way then carries what the network would, as frames:

    kind u8 | connection u8 | length u32 (big-endian) | data

| Kind | Name     | Meaning                                                          |
|------|----------|------------------------------------------------------------------|
| 0    | HELLO    | the computer starts afresh (all closed); the app answers HELLO   |
| 1    | OPEN     | a new TLS connection to the app, numbered `connection`           |
| 2    | DATA     | bytes of that connection                                         |
| 3    | CLOSE    | that connection ended                                            |
| 4    | DATAGRAM | one display packet, either way                                   |

TLS, pairing and AES-GCM stay end to end: the cable is trusted with
nothing. The app reads in 16 KiB transfers; the computer writes at most
48 KiB at a time, and a write that fills its last USB packet exactly is
followed by an empty one.
