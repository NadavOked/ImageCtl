#!/bin/sh
set -eu
for f in rootfs/overlay/usr/local/bin/* rootfs/overlay/usr/local/sbin/* rootfs/overlay/etc/init.d/*; do
  [ -f "$f" ] || continue
  sh -n "$f"
done
grep -q 'ALLOW_IMAGING=0' rootfs/overlay/etc/imagectl/client.conf.example
echo 'PASS overlay-static'
