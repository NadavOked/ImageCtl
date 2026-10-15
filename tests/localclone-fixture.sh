#!/bin/sh
# localclone-fixture.sh -- #1322: the disks the local clone engine is tested
# on, as sparse image files on loop devices. Root (the Testrunner's sudo -n).
#
#   localclone-fixture.sh setup <dir>      source + targets; "<name> <dev>" in <dir>/devs
#   localclone-fixture.sh setup-tail <dir> <fill MiB>  #1202: a full C:, a 2M-smaller target
#   localclone-fixture.sh files <dev> <dir> [idx:fs ...]  sha256 of every file on <dev>, by partition
#   localclone-fixture.sh teardown <dir>   loops detached, mounts released
#
# The source is a Windows-like disk with a Linux root at the end:
#   1 ESP FAT32 64M · 2 MSR 16M (no filesystem: raw bytes, copied whole)
#   3 Windows NTFS 3.5G (shrinkable) · 4 Recovery NTFS 300M (attrs 0+63)
#   5 Linux ext4, the rest -- the last system partition, the one that grows.
# Targets: same (the source's size), big (+1.5G: the last partition grows),
# small (needs the temporary shrink of partition 3), tiny (does not fit),
# full (its backing file sits on a 96M tmpfs: writes fail in partition 3).
set -u
cmd=${1:-}; dir=${2:-}
[ -n "$dir" ] || { echo "usage: $0 setup|files|teardown <dir>" >&2; exit 2; }

mkimg() {   # $1 name, $2 size, $3 directory of the backing file
    truncate -s "$2" "$3/$1.img" || return 1
    _d=$(losetup -fP --show "$3/$1.img") || return 1
    echo "$1 $_d" >> "$dir/devs"; echo "$_d"
}

put() {   # $1 node, $2 fs type, $3 tag: a few files, one of them 24M of random bytes
    mkdir -p "$dir/m"
    mount -t "$2" "$1" "$dir/m" || return 1
    mkdir -p "$dir/m/data"
    head -c 25165824 /dev/urandom > "$dir/m/data/big.bin"
    printf 'local clone %s\n' "$3" > "$dir/m/data/hello.txt"
    for _i in 1 2 3 4 5 6 7 8; do head -c 65536 /dev/urandom > "$dir/m/data/small$_i.bin"; done
    sync; umount "$dir/m"
}

case "$cmd" in
setup)
    mkdir -p "$dir" "$dir/full" && : > "$dir/devs" || exit 1
    src=$(mkimg src 4608M "$dir") || exit 1
    sgdisk -n 1:2048:+64M -t 1:ef00 -c 1:"EFI system partition" \
           -n 2:0:+16M -t 2:0c01 -c 2:"Microsoft reserved partition" \
           -n 3:0:+3584M -t 3:0700 -c 3:"Basic data partition" \
           -n 4:0:+300M -t 4:2700 -c 4:"Recovery" -A 4:set:0 -A 4:set:63 \
           -n 5:0:0 -t 5:8300 -c 5:"linux root" "$src" > /dev/null || exit 1
    blockdev --rereadpt "$src"; udevadm settle 2>/dev/null; sleep 1
    mkfs.vfat -F 32 -s 1 -n EFI "${src}p1" > /dev/null || exit 1
    head -c 16777216 /dev/urandom > "${src}p2" || exit 1
    mkntfs -Q -q -L Windows "${src}p3" || exit 1
    mkntfs -Q -q -L Recovery "${src}p4" || exit 1
    mkfs.ext4 -q -L root "${src}p5" || exit 1
    put "${src}p1" vfat esp && put "${src}p3" ntfs-3g windows && put "${src}p4" ntfs-3g recovery \
        && put "${src}p5" ext4 root || exit 1
    mkdir -p "$dir/m"; mount -t ext4 "${src}p5" "$dir/m" && mkdir -p "$dir/m/etc" \
        && printf 'PRETTY_NAME="Fixture Linux 13"\n' > "$dir/m/etc/os-release" && umount "$dir/m" || exit 1
    mkimg same 4608M "$dir" > /dev/null && mkimg big 6144M "$dir" > /dev/null \
        && mkimg small 3328M "$dir" > /dev/null && mkimg tiny 2048M "$dir" > /dev/null || exit 1
    mount -t tmpfs -o size=96m lcfull "$dir/full" && mkimg full 4608M "$dir/full" > /dev/null || exit 1
    ;;
setup-tail)
    # #1202: the drives of 20/09 in miniature -- ESP, MSR, C: (NTFS 256M with
    # $3 MiB of data), Recovery (NTFS) to the very last usable sector; and a
    # target 2M smaller ("near"). 100 leaves C: room for the 64MiB tail; 170
    # does not (packed).
    fill=${3:-100}
    mkdir -p "$dir" && : > "$dir/devs" || exit 1
    src=$(mkimg src 400M "$dir") || exit 1
    sgdisk -n 1:2048:+64M -t 1:ef00 -c 1:"EFI system partition" \
           -n 2:0:+16M -t 2:0c01 -c 2:"Microsoft reserved partition" \
           -n 3:0:+256M -t 3:0700 -c 3:"Basic data partition" \
           -n 4:0:0 -t 4:2700 -c 4:"Recovery" -A 4:set:0 -A 4:set:63 "$src" > /dev/null || exit 1
    blockdev --rereadpt "$src"; udevadm settle 2>/dev/null; sleep 1
    mkfs.vfat -F 32 -s 1 -n EFI "${src}p1" > /dev/null || exit 1
    head -c 16777216 /dev/urandom > "${src}p2" || exit 1
    mkntfs -Q -q -L Windows "${src}p3" || exit 1
    mkntfs -Q -q -L Recovery "${src}p4" || exit 1
    put "${src}p1" vfat esp && put "${src}p4" ntfs-3g recovery || exit 1
    mkdir -p "$dir/m"; mount -t ntfs-3g "${src}p3" "$dir/m" && mkdir -p "$dir/m/data" \
        && head -c $((fill * 1048576)) /dev/urandom > "$dir/m/data/fill.bin" \
        && printf 'tail shift\n' > "$dir/m/data/hello.txt" && sync && umount "$dir/m" || exit 1
    mkimg near 398M "$dir" > /dev/null || exit 1
    ;;
files)
    # $2 = disk, $3 = dir, $4 = the file partitions (idx:fs ..., the default
    # is the setup layout). "<idx> <sha256> <path>" for each of them.
    node=$2; dir=$3; mkdir -p "$dir/m"
    # shellcheck disable=SC2086 # the list is split on purpose
    for _p in ${4:-1:vfat 3:ntfs-3g 4:ntfs-3g 5:ext4}; do
        _i=${_p%%:*}; _t=${_p#*:}; _o=ro; [ "$_t" = ext4 ] && _o=ro,noload
        mount -t "$_t" -o "$_o" "${node}p$_i" "$dir/m" || { echo "$_i mount-failed"; continue; }
        (cd "$dir/m" && find data etc -type f 2>/dev/null | sort | while read -r _f; do
            echo "$_i $(sha256sum "$_f" | cut -c1-64) $_f"; done)
        umount "$dir/m"
    done
    ;;
teardown)
    umount "$dir/m" 2>/dev/null
    [ -f "$dir/devs" ] && while read -r _n _d; do losetup -d "$_d" 2>/dev/null; done < "$dir/devs"
    umount "$dir/full" 2>/dev/null
    rm -rf "$dir"
    ;;
*) echo "unknown command: $cmd" >&2; exit 2 ;;
esac
