#!/bin/sh
# builds the college-dhcp VM disk + seed ISO on the lab server.
# outputs: /tmp/collegedhcp.vhdx, /tmp/collegedhcp-seed.iso
# then, from the host:
#   scp root@10.98.10.8:/tmp/collegedhcp.vhdx D:\ImageCtl-Lab\ImageCtl-CollegeDHCP-disk1.vhdx
#   scp root@10.98.10.8:/tmp/collegedhcp-seed.iso C:\ImageCtl-Lab\
#   ואז setup-lab.ps1 יוצר את המכונה, ומחברים את ה-seed כ-DVD להפעלה הראשונה.
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
cp "$IMG" /tmp/collegedhcp.qcow2
qemu-img resize /tmp/collegedhcp.qcow2 8G
qemu-img convert -O vhdx -o subformat=dynamic /tmp/collegedhcp.qcow2 /tmp/collegedhcp.vhdx
rm -f /tmp/collegedhcp.qcow2

HERE=$(dirname "$0")
genisoimage -quiet -output /tmp/collegedhcp-seed.iso \
    -volid cidata -joliet -rock \
    "$HERE/user-data" "$HERE/meta-data" "$HERE/network-config"

ls -la /tmp/collegedhcp.vhdx /tmp/collegedhcp-seed.iso
