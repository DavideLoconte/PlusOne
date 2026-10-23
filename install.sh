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
# Same entry as the package, but launching this checkout.
sed "s|^Exec=.*|Exec=$here/bin/plus-one|" "$here/data/$app_id.desktop" > "$apps/$app_id.desktop"
update-desktop-database "$apps" 2>/dev/null || true
# -f -t: the per-user hicolor dir has no index.theme, and a stale cache hides new icons.
gtk-update-icon-cache -q -f -t "${icons%/scalable/apps}" 2>/dev/null || true
echo "Installed: look for «Plus One» in your apps (or run plus-one)."
