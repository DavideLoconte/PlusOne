#!/bin/sh
# Plus One.app for macOS (the menu bar: plusone/menubar.py), zipped, and a
# Homebrew cask for it:
#   packaging/macos/build-app.sh   -> dist/PlusOne-<version>-macos.zip, dist/plus-one.rb
# then: brew tap-new <you>/plusone; cp dist/plus-one.rb into its Casks;
#       brew install --cask <you>/plusone/plus-one
#       xattr -dr com.apple.quarantine "/Applications/Plus One.app"
# (signed ad hoc, not by a Developer ID: quarantined, Gatekeeper would refuse it).
set -e
cd "$(dirname "$0")/../.."
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' plusone/__init__.py)"
python="${PYTHON:-python3}"
app="dist/Plus One.app"
resources="$app/Contents/Resources"

rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$resources"
# ponytail: a venv of the Python that builds it (python.org's or Homebrew's,
# which must stay installed), not a Python of its own; py2app if the app
# must run on a Mac without that Python.
"$python" -m venv "$resources/venv"
"$resources/venv/bin/pip" install -q --disable-pip-version-check \
    pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ScreenCaptureKit \
    pyobjc-framework-VideoToolbox pyobjc-framework-CoreMedia pyobjc-framework-libdispatch \
    pyobjc-framework-ApplicationServices cryptography pyusb
rsync -a --exclude __pycache__ plusone "$resources/"

# The launcher embeds Python rather than exec'ing venv/bin/python: that one
# hands over to Python.app, and macOS would see "Python" (its menu bar item,
# its permissions), not Plus One.
cat > dist/launcher.c <<'EOF'
#include <Python.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char exe[PATH_MAX], up[PATH_MAX], resources[PATH_MAX], python[PATH_MAX], log[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size)) return 1;
    snprintf(up, sizeof up, "%s/../Resources", dirname(exe));
    if (!realpath(up, resources)) return 1;  // a plain path, as the venv's sys.prefix
    snprintf(python, sizeof python, "%s/venv/bin/python", resources);
    snprintf(log, sizeof log, "%s/Library/Logs/Plus One.log", getenv("HOME"));
    if (chdir(resources)) return 1;
    // The menu bar's log, where Console finds it.
    freopen(log, "a", stdout);
    freopen(log, "a", stderr);
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    PyConfig_SetBytesString(&config, &config.executable, python);  // finds the venv
    PyConfig_SetBytesString(&config, &config.run_module, "plusone.menubar");
    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);
    return Py_RunMain();
}
EOF
var() { "$python" -c "import sysconfig; print(sysconfig.get_config_var('$1'))"; }
xcrun clang -O2 -I"$(var INCLUDEPY)" -L"$(var LIBDIR)" -lpython"$(var LDVERSION)" -o "$app/Contents/MacOS/PlusOne" dist/launcher.c
rm dist/launcher.c

cat > "$app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Plus One</string>
    <key>CFBundleDisplayName</key><string>Plus One</string>
    <key>CFBundleIdentifier</key><string>io.github.gabrielesantangelo.PlusOne</string>
    <key>CFBundleVersion</key><string>$version</string>
    <key>CFBundleShortVersionString</key><string>$version</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>PlusOne</string>
    <key>CFBundleIconFile</key><string>PlusOne</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <!-- The menu bar only: no Dock icon. -->
    <key>LSUIElement</key><true/>
    <key>NSLocalNetworkUsageDescription</key>
    <string>Plus One finds your tablet on the network and sends your desktop to it.</string>
    <key>NSBonjourServices</key>
    <array><string>_plusone._tcp</string></array>
</dict>
</plist>
EOF

# The icon, from the project's SVG (AppKit reads SVG), at every size macOS asks for.
iconset="dist/PlusOne.iconset"
rm -rf "$iconset"
mkdir -p "$iconset"
"$resources/venv/bin/python" - "$iconset" <<'EOF'
import sys
from AppKit import NSBitmapImageRep, NSCompositingOperationSourceOver, NSGraphicsContext, NSImage, NSPNGFileType
svg = NSImage.alloc().initWithContentsOfFile_("data/io.github.gabrielesantangelo.PlusOne.svg")
for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        pixels = size * scale
        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, pixels, pixels, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
        svg.drawInRect_fromRect_operation_fraction_(((0, 0), (pixels, pixels)), ((0, 0), (0, 0)),
                                                    NSCompositingOperationSourceOver, 1.0)
        NSGraphicsContext.restoreGraphicsState()
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(f"{sys.argv[1]}/{name}", True)
EOF
iconutil -c icns "$iconset" -o "$resources/PlusOne.icns"
rm -rf "$iconset"

codesign --force --deep --sign - "$app"
# Ad hoc, macOS would know the app by its hash, new at each build: its
# permissions (Screen Recording, Accessibility) asked for again each time.
# Known by its identifier instead.
# ponytail: any app with this identifier would have them; a Developer ID when released.
codesign --force --sign - --requirements "=designated => identifier \"io.github.gabrielesantangelo.PlusOne\"" "$app"
zip="dist/PlusOne-$version-macos.zip"
rm -f "$zip"
ditto -c -k --keepParent "$app" "$zip"
sha="$(shasum -a 256 "$zip" | cut -d' ' -f1)"

cat > dist/plus-one.rb <<EOF
cask "plus-one" do
  version "$version"
  sha256 "$sha"

  url "file://$PWD/$zip"
  name "Plus One"
  desc "Galaxy Tab as a second monitor, over Wi-Fi or the USB cable"
  homepage "https://github.com/GabrieleSantangelo/PlusOne"

  depends_on formula: "libusb"
  depends_on macos: ">= :ventura"

  app "Plus One.app"

  zap trash: [
    "~/.config/plus-one",
    "~/Library/Logs/Plus One.log",
  ]
end
EOF
echo "$zip"
echo "dist/plus-one.rb"
