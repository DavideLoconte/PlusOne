#!/bin/sh
# Optional, needs sudo once (source installs; the .deb does this itself): lets
# the plus-one group's members create virtual HID devices (/dev/uhid), so the
# S Pen becomes a real Linux tablet with pressure and tilt, and adds you to
# it. Note: any program you run could then also create virtual input devices
# (same trust level as /dev/uinput access).
# Undo: sudo gpasswd -d $USER plus-one && sudo rm /etc/udev/rules.d/70-plus-one-uhid.rules \
#            /etc/modules-load.d/plus-one-uhid.conf && sudo udevadm trigger
set -e
here="$(dirname "$(readlink -f "$0")")"
groupadd --system -f plus-one
install -m 644 "$here/data/70-plus-one-uhid.rules" /etc/udev/rules.d/
# /dev/uhid is only a placeholder until the module loads; load it now and at boot.
install -m 644 "$here/data/plus-one-uhid.conf" /etc/modules-load.d/
udevadm control --reload
modprobe uhid
udevadm trigger --action=change --subsystem-match=misc --sysname-match=uhid
udevadm settle
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != root ]; then
    usermod -aG plus-one "$SUDO_USER"
    echo "Done: $SUDO_USER is in the plus-one group. Log out and back in for the pen's pressure."
else
    echo "Done. Join the group (sudo usermod -aG plus-one \$USER), then log out and back in."
fi
