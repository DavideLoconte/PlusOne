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
    keys="$(cd "$(dirname "$PLUSONE_KEYSTORE")" && pwd)"
    signing="-v $keys:/keys:ro -e PLUSONE_KEYSTORE=/keys/$(basename "$PLUSONE_KEYSTORE")
             -e PLUSONE_KEYSTORE_PASSWORD -e PLUSONE_KEY_ALIAS"
fi
# shellcheck disable=SC2086 # $signing is a list of options
docker run --rm -v "$PWD:/src" -v plus-one-android-gradle:/gradle $signing plus-one-android \
    sh -c "gradle --no-daemon --console=plain $tasks; status=\$?; chown -R $(id -u):$(id -g) /src/android; exit \$status"
