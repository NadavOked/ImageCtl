#!/bin/sh
set -eu
PKG=${1:-rootfs/packages.txt}
for p in curl iproute2 util-linux partclone zstd udpcast cage cog; do
  grep -Eq "^${p}([[:space:]]|$)" "$PKG" || echo "WARN package not declared: $p"
done
