#!/bin/sh
set -eu
OUT=${1:-imagectl-hw-$(date +%Y%m%d-%H%M%S).txt}
{
 echo '=== ImageCtl diskless hardware inventory ==='
 uname -a
 echo '--- PCI'; lspci -nnk 2>&1 || true
 echo '--- block'; lsblk -o NAME,TYPE,SIZE,RO,RM,MODEL,SERIAL,TRAN 2>&1 || true
 echo '--- network'; ip -br link 2>&1 || true
 echo '--- DRM'; ls -la /dev/dri 2>&1 || true
 echo '--- modules'; lsmod 2>&1 || true
 echo '--- firmware warnings'; dmesg 2>&1 | grep -iE 'firmware|failed|error' | tail -200 || true
} > "$OUT"
echo "$OUT"
