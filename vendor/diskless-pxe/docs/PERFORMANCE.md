# Performance measurement

Measure before optimizing. Capture timestamps for firmware start, DHCP lease, iPXE start, kernel download start/end, userspace start, link ready, kiosk visible, imaging start/end.

High-value knobs after measurement: HTTP payload size, initramfs/module/firmware size, DHCP latency, NIC link negotiation, graphics initialization, browser startup, and multicast receiver throughput. Do not remove firmware or drivers until the supported hardware inventory is complete.
