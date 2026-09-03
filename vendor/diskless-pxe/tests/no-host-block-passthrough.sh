#!/bin/sh
set -eu
if grep -RInE --exclude='no-host-block-passthrough.sh' '(/dev/(sd[a-z]|nvme[0-9]+n[0-9]+).*(of=|format=raw)|-drive[[:space:]]+file=/dev/)' qemu rootfs ipxe 2>/dev/null; then
  echo 'FAIL host block-device passthrough pattern found'; exit 1
fi
echo 'PASS no-host-block-passthrough'
