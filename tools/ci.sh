#!/bin/sh
# Everything CI checks, in order. Runs inside Dockerfile.ci.
set -e
cd "$(dirname "$0")/.."
# Hand the built package back to the host user when run from tools/ci-docker.sh.
[ -n "$HOST_UID" ] && trap 'chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" dist 2>/dev/null' EXIT
python3 -m compileall -q plusone tools tests
# The GUI module must at least import without a display.
python3 -c "import gi; gi.require_version('Gtk', '4.0'); import plusone.gui"
python3 -m pytest -q -p no:cacheprovider tests
packaging/build-deb.sh
lintian --fail-on error dist/*.deb
