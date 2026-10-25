#!/bin/sh
# Build the companion app (android/) in the Dockerfile.android container:
# JDK, Android SDK and Gradle never touch the host. Gradle's caches stay in
# the plus-one-android-gradle volume. Arguments are Gradle tasks
# (default: assembleDebug); the APK lands in android/app/build/outputs/apk.
# For a signed release (assembleRelease), set PLUSONE_KEYSTORE (a path on
# the host), PLUSONE_KEYSTORE_PASSWORD and optionally PLUSONE_KEY_ALIAS.
set -e
cd "$(dirname "$0")/.."
docker build -q -t plus-one-android - < Dockerfile.android > /dev/null
tasks="${*:-assembleDebug}"
signing=""
if [ -n "$PLUSONE_KEYSTORE" ]; then
    # The key file alone, read only: not the folder it is in.
    key="$(cd "$(dirname "$PLUSONE_KEYSTORE")" && pwd)/$(basename "$PLUSONE_KEYSTORE")"
    signing="-v $key:/keys/release.p12:ro -e PLUSONE_KEYSTORE=/keys/release.p12
             -e PLUSONE_KEYSTORE_PASSWORD -e PLUSONE_KEY_ALIAS"
fi
# shellcheck disable=SC2086 # $signing is a list of options
docker run --rm -v "$PWD:/src" -v plus-one-android-gradle:/gradle $signing plus-one-android \
    sh -c "gradle --no-daemon --console=plain $tasks; status=\$?; chown -R $(id -u):$(id -g) /src/android; exit \$status"
