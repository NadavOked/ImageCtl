# Proposed boot flow (pre-integration)

1. Firmware obtains normal DHCP lease and PXE information.
2. Existing PXE infrastructure loads an iPXE EFI binary (or retains the signed GRUB path when Secure Boot policy requires it).
3. iPXE retrieves the small boot script, kernel and initramfs over the existing ImageCtl HTTP service.
4. Linux runs from RAM; local disks are never mounted/written merely because PXE boot succeeded.
5. Network comes up, then the kiosk starts against the configured ImageCtl URL.
6. Imaging actions remain behind the ImageCtl agent/task contract. This package does not invent a second task protocol.
7. Any unknown/error/no-task state must resolve to local boot or a non-destructive failure screen.

Do not pass task secrets or destructive task parameters on the kernel command line.
