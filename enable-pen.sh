#!/bin/sh
# Optional, needs sudo once: lets the logged-in desktop user create virtual
# HID devices (/dev/uhid), so the S Pen becomes a real Linux tablet with
# pressure and tilt. Note: any program you run could then also create
# virtual input devices (same trust level as /dev/uinput access).
# Undo: sudo rm /etc/udev/rules.d/70-plus-one-uhid.rules \
#            /etc/modules-load.d/plus-one-uhid.conf && sudo udevadm trigger
set -e
echo 'KERNEL=="uhid", SUBSYSTEM=="misc", TAG+="uaccess"' > /etc/udev/rules.d/70-plus-one-uhid.rules
# /dev/uhid is only a placeholder until the module loads; load it now and at boot.
echo uhid > /etc/modules-load.d/plus-one-uhid.conf
udevadm control --reload
modprobe uhid
udevadm trigger --action=change --subsystem-match=misc --sysname-match=uhid
udevadm settle
echo "Done: $(getfacl -p /dev/uhid 2>/dev/null | grep '^user:' | tr '\n' ' ')"
