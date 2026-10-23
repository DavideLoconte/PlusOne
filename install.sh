#!/bin/sh
# Per-user install: menu entry + icon pointing at this checkout. No root needed.
set -e
here="$(dirname "$(readlink -f "$0")")"
app_id=io.github.santal.PlusOne
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$apps" "$icons" "$HOME/.local/bin"
cp "$here/data/$app_id.svg" "$icons/$app_id.svg"
ln -sf "$here/bin/plus-one" "$HOME/.local/bin/plus-one"
cat > "$apps/$app_id.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Plus One
Comment=Use your Samsung Galaxy Tab as a second monitor via Second Screen
Exec=$here/bin/plus-one
Icon=$app_id
Categories=Utility;GTK;
Keywords=tablet;monitor;second screen;miracast;display;
StartupNotify=true
DESKTOP
update-desktop-database "$apps" 2>/dev/null || true
# -f -t: the per-user hicolor dir has no index.theme, and a stale cache hides new icons.
gtk-update-icon-cache -q -f -t "${icons%/scalable/apps}" 2>/dev/null || true
echo "Installed: look for «Plus One» in your apps (or run plus-one)."
