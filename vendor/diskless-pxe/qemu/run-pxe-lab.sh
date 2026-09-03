#!/bin/sh
# Safe VM harness: no host block device passthrough.
set -eu
DISK=${DISK:-out/qemu-test-disk.qcow2}
[ -f "$DISK" ] || { echo "Create $DISK first with qemu/create-test-disk.sh" >&2; exit 2; }
command -v qemu-system-x86_64 >/dev/null || { echo 'QEMU required' >&2; exit 3; }
exec qemu-system-x86_64 -m 2048 -enable-kvm -drive "file=$DISK,if=virtio,format=qcow2" -netdev user,id=n1 -device virtio-net-pci,netdev=n1 -boot n
