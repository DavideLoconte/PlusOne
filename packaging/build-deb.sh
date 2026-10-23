#!/bin/sh
# Build dist/plus-one_<version>_all.deb from this checkout (no root needed).
set -e
umask 022
here="$(dirname "$(readlink -f "$0")")"
top="$(dirname "$here")"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$top/plusone/__init__.py")"
app_id=io.github.santal.PlusOne
pkg=plus-one
root="$top/build/deb/${pkg}_${version}_all"

rm -rf "$root"
install -d "$root/DEBIAN" "$root/usr/bin" "$root/usr/lib/$pkg" \
    "$root/usr/share/applications" "$root/usr/share/icons/hicolor/scalable/apps" \
    "$root/usr/share/metainfo" "$root/usr/share/doc/$pkg" \
    "$root/usr/lib/udev/rules.d" "$root/usr/lib/modules-load.d"

cp -r "$top/plusone" "$root/usr/lib/$pkg/"
find "$root/usr/lib/$pkg" -name __pycache__ -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$root/usr/lib/$pkg"
cat > "$root/usr/bin/$pkg" <<LAUNCHER
#!/bin/sh
PYTHONPATH="/usr/lib/$pkg\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m plusone "\$@"
LAUNCHER
chmod 755 "$root/usr/bin/$pkg"

install -m 644 "$top/data/$app_id.desktop" "$root/usr/share/applications/"
install -m 644 "$top/data/$app_id.svg" "$root/usr/share/icons/hicolor/scalable/apps/"
install -m 644 "$top/data/$app_id.metainfo.xml" "$root/usr/share/metainfo/"
# S Pen as a real tablet: let the desktop user create virtual HID devices.
echo 'KERNEL=="uhid", SUBSYSTEM=="misc", TAG+="uaccess"' > "$root/usr/lib/udev/rules.d/70-$pkg-uhid.rules"
echo uhid > "$root/usr/lib/modules-load.d/$pkg-uhid.conf"
chmod 644 "$root/usr/lib/udev/rules.d/70-$pkg-uhid.rules" "$root/usr/lib/modules-load.d/$pkg-uhid.conf"

install -m 644 "$top/README.md" "$root/usr/share/doc/$pkg/README.md"
{
    echo "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/"
    echo "Upstream-Name: $pkg"
    echo
    echo "Files: *"
    echo "Copyright: 2026 santal1210"
    echo "License: MIT"
    sed 's/^$/./; s/^/ /' "$top/LICENSE"
} > "$root/usr/share/doc/$pkg/copyright"
{
    echo "$pkg ($version) unstable; urgency=medium"
    echo
    echo "  * Release $version."
    echo
    echo " -- $(git -C "$top" log -1 --format='%an <%ae>')  $(date -R)"
} | gzip -9n > "$root/usr/share/doc/$pkg/changelog.gz"
chmod 644 "$root/usr/share/doc/$pkg/"*

install -m 755 "$here/debian/postinst" "$here/debian/postrm" "$root/DEBIAN/"
size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
cat > "$root/DEBIAN/control" <<CONTROL
Package: $pkg
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: $(git -C "$top" log -1 --format='%an <%ae>')
Installed-Size: $size
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.4), gir1.2-gstreamer-1.0, gir1.2-nm-1.0, gstreamer1.0-pipewire, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, network-manager, kmod, udev
Recommends: gstreamer1.0-vaapi, gstreamer1.0-plugins-ugly
Suggests: adb
Description: use a Samsung Galaxy Tab as a second monitor via Second Screen
 Extends the GNOME desktop onto a Galaxy Tab through the tablet's built-in
 Second Screen app: Mutter virtual monitor, Wi-Fi Direct, low-latency H.264
 (NVENC, VA-API or x264), finger touch and S Pen with pressure and tilt.
CONTROL

mkdir -p "$top/dist"
dpkg-deb --root-owner-group -Zxz --build "$root" "$top/dist/${pkg}_${version}_all.deb"
