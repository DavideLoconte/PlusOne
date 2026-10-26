#!/bin/sh
# A release as a flat APT repository: Packages, Release, InRelease and
# Release.gpg for the .deb in DIR, signed with KEY (a key id or fingerprint
# in $GNUPGHOME). Published as release assets next to the .deb, they make
# https://github.com/GabrieleSantangelo/PlusOne/releases/latest/download/
# a repository (packaging/apt/plus-one.sources).
set -e
dir="$1"
key="$2"
[ -d "$dir" ] && [ -n "$key" ] || { echo "usage: $0 DIR KEY" >&2; exit 2; }
cd "$dir"
apt-ftparchive packages . > Packages
gzip -9kfn Packages
# Release lists the indexes only, not the .deb (Packages has its hashes).
index="$(mktemp -d)"
cp Packages Packages.gz "$index/"
(cd "$index" && apt-ftparchive \
    -o APT::FTPArchive::Release::Origin="Plus One" \
    -o APT::FTPArchive::Release::Label="Plus One" \
    -o APT::FTPArchive::Release::Description="Plus One's releases" \
    release .) > Release
rm -r "$index"
gpg --batch --yes --local-user "$key" --clearsign -o InRelease Release
gpg --batch --yes --local-user "$key" --armor --detach-sign -o Release.gpg Release
