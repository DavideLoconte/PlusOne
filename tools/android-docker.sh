#!/bin/sh
# Build the companion app (android/) in the Dockerfile.android container:
# JDK, Android SDK and Gradle never touch the host. Gradle's caches stay in
# the plus-one-android-gradle volume. Arguments are Gradle tasks
# (default: assembleDebug); the APK lands in android/app/build/outputs/apk.
set -e
cd "$(dirname "$0")/.."
docker build -q -t plus-one-android - < Dockerfile.android > /dev/null
tasks="${*:-assembleDebug}"
docker run --rm -v "$PWD:/src" -v plus-one-android-gradle:/gradle plus-one-android \
    sh -c "gradle --no-daemon --console=plain $tasks; status=\$?; chown -R $(id -u):$(id -g) /src/android; exit \$status"
