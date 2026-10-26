"""The cable's frames (usb.py, UsbTunnel.kt): cut back however the reads fell."""
from plusone import usb


def test_frames_across_reads():
    stream = usb.frame(usb.OPEN, 3) + usb.frame(usb.DATA, 3, b"hello") + usb.frame(usb.DATAGRAM, 0, bytes(1400))
    frames = usb.Frames()
    found = []
    for at in range(0, len(stream), 7):  # reads that split headers and data
        found += frames.feed(stream[at:at + 7])
    assert found == [(usb.OPEN, 3, b""), (usb.DATA, 3, b"hello"), (usb.DATAGRAM, 0, bytes(1400))]
    assert not frames.buffer
