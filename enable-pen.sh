#!/bin/sh
# Optional, needs sudo once (source installs; the .deb does this itself): the
# S Pen's helper, so the pen becomes a real Linux tablet with pressure and
# tilt. It runs as root only when Plus One asks (D-Bus activation), only for
# the session in front of the PC (polkit), and makes the pen's virtual HID
# device and nothing else: no group gets /dev/uhid (which could make keyboards).
# Undo: sudo rm /usr/local/lib/plus-one/pen-helper \
#            /usr/local/share/dbus-1/system-services/io.github.gabrielesantangelo.PlusOne.Pen.service \
#            /etc/dbus-1/system.d/io.github.gabrielesantangelo.PlusOne.Pen.conf \
#            /usr/share/polkit-1/actions/io.github.gabrielesantangelo.plusone.policy
set -e
here="$(dirname "$(readlink -f "$0")")"
helper="$here/data/pen-helper"
install -Dm 755 "$helper/plus-one-pen-helper" /usr/local/lib/plus-one/pen-helper
install -d /usr/local/share/dbus-1/system-services
sed 's|^Exec=/usr/lib/plus-one/pen-helper$|Exec=/usr/local/lib/plus-one/pen-helper|' \
    "$helper/io.github.gabrielesantangelo.PlusOne.Pen.service" \
    > /usr/local/share/dbus-1/system-services/io.github.gabrielesantangelo.PlusOne.Pen.service
install -Dm 644 "$helper/io.github.gabrielesantangelo.PlusOne.Pen.conf" /etc/dbus-1/system.d/
install -Dm 644 "$helper/io.github.gabrielesantangelo.plusone.policy" /usr/share/polkit-1/actions/
# /dev/uhid is only a placeholder until the module loads; load it now and at boot.
install -m 644 "$here/data/plus-one-uhid.conf" /etc/modules-load.d/
modprobe uhid
dbus-send --system --type=method_call --dest=org.freedesktop.DBus / org.freedesktop.DBus.ReloadConfig
echo "Done: the S Pen has its pressure from Plus One's next session (no need to log out)."
