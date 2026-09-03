#!/bin/sh
set -eu
: "${OVMF_CODE:=/usr/share/OVMF/OVMF_CODE.fd}"
: "${RAM:=1024}"
[ -r "$OVMF_CODE" ] || { echo "OVMF not found: $OVMF_CODE" >&2; exit 2; }
# This harness deliberately uses user networking and does not write a host disk.
exec qemu-system-x86_64 -enable-kvm -m "$RAM" -machine q35 -bios "$OVMF_CODE" \
  -netdev user,id=n0 -device e1000,netdev=n0 -serial stdio -display gtk
