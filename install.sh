#!/bin/sh
# Per-user install: menu entry, icon, D-Bus activation and the Quick Settings
# toggle, all pointing at this checkout. No root needed.
set -e
here="$(dirname "$(readlink -f "$0")")"
# This checkout's launcher as an Exec argument: quoted (a path with spaces),
# with what sed's replacement and the Desktop Entry spec treat specially escaped.
exec_cmd="\"$(printf '%s' "$here/bin/plus-one" | sed 's/["`$\\]/\\\\&/g; s/%/%%/g')\""
exec_sed="$(printf '%s' "$exec_cmd" | sed 's/[&|\\]/\\&/g')"
app_id=io.github.gabrielesantangelo.PlusOne
data="${XDG_DATA_HOME:-$HOME/.local/share}"
apps="$data/applications"
icons="$data/icons/hicolor/scalable/apps"
services="$data/dbus-1/services"
extensions="$data/gnome-shell/extensions"
mkdir -p "$apps" "$icons" "$services" "$extensions" "$HOME/.local/bin"
cp "$here/data/$app_id.svg" "$icons/$app_id.svg"
ln -sf "$here/bin/plus-one" "$HOME/.local/bin/plus-one"
# Same entry as the package, but launching this checkout.
sed "s|^Exec=.*|Exec=$exec_sed|" "$here/data/$app_id.desktop" > "$apps/$app_id.desktop"
sed "s|^Exec=plus-one|Exec=$exec_sed|" "$here/data/$app_id.Sign.desktop" > "$apps/$app_id.Sign.desktop"
mkdir -p "$data/nautilus-python/extensions"
ln -sf "$here/data/nautilus/plus-one-sign.py" "$data/nautilus-python/extensions/plus-one-sign.py"
sed "s|^Exec=[^ ]*|Exec=$exec_sed|" "$here/data/$app_id.service" > "$services/$app_id.service"
ext=plus-one@gabrielesantangelo.github.io
ln -sfn "$here/data/gnome-shell/$ext" "$extensions/$ext"
update-desktop-database "$apps" 2>/dev/null || true
# -f -t: the per-user hicolor dir has no index.theme, and a stale cache hides new icons.
gtk-update-icon-cache -q -f -t "${icons%/scalable/apps}" 2>/dev/null || true
echo "Installed: look for «Plus One» in your apps (or run plus-one)."
echo "The Quick Settings toggle appears after you log out and back in."
