# Acceptance test plan

## Phase 1 — VM, no disk writes
- Render iPXE script and validate syntax/static checks.
- Boot Alpine netboot in QEMU with at least 1 GiB RAM.
- Confirm DHCP, HTTP kernel/initramfs retrieval and userspace start.
- Confirm kiosk waits for UI and restarts after browser exit.
- Confirm diagnostics are produced.

## Phase 2 — representative hardware, no imaging
- Test each NIC family, GPU/DRM path, SATA/NVMe controller, keyboard/mouse.
- Measure POST, DHCP, iPXE download, userspace-ready and kiosk-visible timestamps.
- Test delayed link, HTTP outage/recovery, reboot, and fallback boot entry.
- Inventory missing firmware/modules before trimming the image.

## Phase 3 — destructive imaging lab only
Use a sacrificial disk. Verify explicit target selection, whole-disk checks, mounted-device rejection, interruption handling, progress reporting through ImageCtl's existing contract, multicast fan-out, and local-OS boot after completion.

## Exit criteria
No automatic disk selection; no silent fallback to a destructive target; current ImageCtl boot path remains available; all supported hardware models pass; Secure Boot policy is documented and tested.
