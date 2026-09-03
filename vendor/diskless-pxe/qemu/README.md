# QEMU pre-integration harness
Safe harness for firmware/client smoke testing. It intentionally attaches no writable disk.
For full PXE DHCP/TFTP testing use an isolated bridge/VLAN; do not run a second DHCP server on a production LAN.
