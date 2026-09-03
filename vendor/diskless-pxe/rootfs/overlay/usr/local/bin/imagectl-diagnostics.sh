#!/bin/sh
set -u
OUT=${1:-/tmp/imagectl-diagnostics.txt}
{
 echo "timestamp=$(date -Iseconds 2>/dev/null || date)"
 echo "cmdline=$(cat /proc/cmdline 2>/dev/null)"
 echo '--- uname'; uname -a
 echo '--- ip'; ip addr 2>&1
 echo '--- route'; ip route 2>&1
 echo '--- links'; ip -s link 2>&1
 echo '--- block'; lsblk -o NAME,TYPE,SIZE,RO,RM,MODEL,SERIAL,TRAN 2>&1
 echo '--- pci'; lspci -nn 2>&1
 echo '--- drm'; ls -la /dev/dri 2>&1 || true
 echo '--- memory'; free -h 2>&1
 echo '--- mounts'; mount 2>&1
} > "$OUT"
echo "$OUT"
