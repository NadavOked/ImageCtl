#!/bin/sh
# בונה את הדיסק ואת ה-seed של חומת האש של המעבדה, על ה-VM של השרת.
# פלט: /tmp/firewall.vhdx, /tmp/firewall-seed.iso
# ואז, מהמארח:
#   scp root@10.98.10.8:/tmp/firewall.vhdx D:\ImageCtl-Lab\ImageCtl-Firewall-disk1.vhdx
#   scp root@10.98.10.8:/tmp/firewall-seed.iso C:\ImageCtl-Lab\
#   ואז setup-lab.ps1 יוצר את המכונה, ומחברים את ה-seed כ-DVD להפעלה הראשונה.
#
# אותה תבנית של tools/lab/college-dhcp/build-on-server.sh — בכוונה.
set -e
export DEBIAN_FRONTEND=noninteractive
command -v qemu-img >/dev/null || apt-get install -y --no-install-recommends qemu-utils >/dev/null
command -v genisoimage >/dev/null || apt-get install -y --no-install-recommends genisoimage >/dev/null

IMG=/tmp/debian-13-genericcloud-amd64.qcow2
if [ ! -s "$IMG" ]; then
    echo "downloading debian cloud image..."
    wget -q -O "$IMG" \
        https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
fi

# resize works on qcow2 but not on vhdx -- grow first, convert second.
cp "$IMG" /tmp/firewall.qcow2
qemu-img resize /tmp/firewall.qcow2 8G
qemu-img convert -O vhdx -o subformat=dynamic /tmp/firewall.qcow2 /tmp/firewall.vhdx
rm -f /tmp/firewall.qcow2

HERE=$(dirname "$0")
genisoimage -quiet -output /tmp/firewall-seed.iso \
    -volid cidata -joliet -rock \
    "$HERE/user-data" "$HERE/meta-data" "$HERE/network-config"

ls -la /tmp/firewall.vhdx /tmp/firewall-seed.iso
