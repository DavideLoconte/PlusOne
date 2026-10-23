#!/bin/sh
# Optional, needs sudo once: lets the logged-in desktop user create virtual
# HID devices (/dev/uhid), so the S Pen becomes a real Linux tablet with
# pressure and tilt. Note: any program you run could then also create
# virtual input devices (same trust level as /dev/uinput access).
# Undo: sudo rm /etc/udev/rules.d/70-schermo-tablet-uhid.rules && sudo udevadm trigger --name-match=uhid
set -e
rule=/etc/udev/rules.d/70-schermo-tablet-uhid.rules
echo 'KERNEL=="uhid", SUBSYSTEM=="misc", TAG+="uaccess"' > "$rule"
udevadm control --reload
udevadm trigger --name-match=uhid
echo "Fatto: $(getfacl -p /dev/uhid 2>/dev/null | grep '^user:' || ls -l /dev/uhid)"
