# Troubleshooting Runbook

## Firmware never reaches iPXE
Check UEFI network boot, DHCP option/architecture selection, VLAN relay/helper configuration, and Secure Boot signature acceptance.

## iPXE loads but kernel/initramfs does not
Use `ipxe/diagnostic.ipxe`; verify DHCP lease, gateway/DNS if used, HTTP URL, server status, and artifact checksums.

## Linux boots but has no network
Capture `ip addr`, `ip route`, `dmesg`, PCI IDs and loaded modules with `diagnostics/collect.sh`. Add the missing firmware/driver only after identifying hardware.

## Kiosk does not appear
Run `imagectl-preflight`, verify Cage/Cog are installed, inspect DRM/input devices, and test the KIOSK_URL with curl.

## Disk is not detected
Inspect `lsblk -e7 -o NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,RO,RM`; never bypass safety checks just to make a disk appear.
