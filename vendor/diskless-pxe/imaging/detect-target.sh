#!/bin/sh
set -eu
# Enumerate writable physical disks; never auto-select when ambiguous.
lsblk -dn -o NAME,TYPE,SIZE,MODEL,RO | awk '$2=="disk" && $5==0 {print "/dev/"$1"\t"$3"\t"substr($0,index($0,$4))}'
