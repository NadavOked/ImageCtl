#!/bin/sh
set -eu
out=${1:-out/qemu-test-disk.qcow2}
size=${2:-16G}
command -v qemu-img >/dev/null || { echo 'qemu-img required' >&2; exit 2; }
mkdir -p "$(dirname "$out")"
qemu-img create -f qcow2 "$out" "$size"
