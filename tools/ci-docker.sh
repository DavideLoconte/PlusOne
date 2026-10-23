#!/bin/sh
# Run the CI checks locally in the same container GitHub uses.
# The built .deb lands in ./dist.
set -e
cd "$(dirname "$0")/.."
docker build -f Dockerfile.ci -t plus-one-ci .
mkdir -p dist
docker run --rm -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -v "$PWD/dist:/src/dist" plus-one-ci
