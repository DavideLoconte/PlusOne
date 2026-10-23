#!/bin/sh
# Optional, needs sudo once (source installs; the .deb does this itself): lets
# the logged-in desktop user create virtual HID devices (/dev/uhid), so the
# S Pen becomes a real Linux tablet with pressure and tilt. Note: any program
# you run could then also create virtual input devices (same trust level as
# /dev/uinput access).
# Undo: sudo rm /etc/udev/rules.d/70-plus-one-uhid.rules \
#            /etc/modules-load.d/plus-one-uhid.conf && sudo udevadm trigger
set -e
here="$(dirname "$(readlink -f "$0")")"
install -m 644 "$here/data/70-plus-one-uhid.rules" /etc/udev/rules.d/
# /dev/uhid is only a placeholder until the module loads; load it now and at boot.
install -m 644 "$here/data/plus-one-uhid.conf" /etc/modules-load.d/
udevadm control --reload
modprobe uhid
udevadm trigger --action=change --subsystem-match=misc --sysname-match=uhid
udevadm settle
echo "Done: $(getfacl -p /dev/uhid 2>/dev/null | grep '^user:' | tr '\n' ' ')"
