# ImageCtl integration hand-off

## Goal

Add a diskless RAM client without redesigning ImageCtl's existing HTTP/API/imaging layers.

## Developer tasks inside ImageCtl

1. Publish the generated `vmlinuz-lts` and `initramfs` under the existing HTTP service (suggested logical path: `/diskless/`).
2. Publish/serve `boot.ipxe`, or embed a chain URL into the selected iPXE EFI binary.
3. Adjust DHCP architecture rules so UEFI x86_64 clients receive iPXE, while avoiding an iPXE chain-loading loop. Keep the existing path as a fallback during rollout.
4. Decide how the RAM client obtains the ImageCtl web UI URL. Prefer kernel command line/generated config over hard-coded IPs.
5. Connect the existing ImageCtl imaging receiver to `imaging/imaging-adapter.sh` or replace the generic adapter with a native partclone/zstd/udpcast adapter matching the repository's exact contracts.
6. Expose progress/errors to the existing UI/API using the repository's existing data contracts. Do not invent a parallel status schema.
7. Add an explicit server-authorized target-disk selection/confirmation step. Do not auto-select when multiple writable disks are present.
8. Validate NIC firmware, storage, GPU/DRM and input drivers on every supported PC model; trim firmware/modules only after hardware inventory is known.
9. Decide Secure Boot policy. Unsigned custom iPXE/kernel artifacts will not work with Secure Boot enforced unless a valid signing/trust chain is deployed.
10. Roll out behind a selectable boot option first; retain the current GRUB path until the diskless path passes soak testing.

## Suggested acceptance tests

- Cold boot to visible kiosk on representative UEFI PCs.
- DHCP/iPXE loop prevention.
- HTTP kernel/initramfs fetch after TFTP/firmware bootstrap.
- NIC link delayed by 5–20 seconds.
- ImageCtl HTTP unavailable at boot and later restored.
- NVMe, SATA SSD, multiple disks, read-only/removable media.
- Kiosk crash/restart behavior.
- Imaging receiver interruption mid-stream: target reports failure; no false success.
- Multicast to a full classroom without replacing it with per-client HTTP image transfer.
- Reboot after successful imaging boots the local installed OS according to the intended ImageCtl workflow.

## Performance measurement

Record timestamps for: firmware start, DHCP lease, iPXE start, kernel download start/end, kernel userspace start, network-ready, kiosk-visible, imaging start/end. Do not promise a 10–20 second power-to-UI target until measured on the actual hardware.

## Prepared hand-off artifacts

Before touching ImageCtl, the developer can run `make test`, render a server-specific iPXE script with `bin/render-ipxe.sh`, collect hardware inventories with `diagnostics/collect.sh`, and execute the VM/hardware acceptance plan in `docs/ACCEPTANCE_TESTS.md`.

The rootfs overlay has no automatic destructive action. Imaging remains an explicit integration boundary so the existing ImageCtl authorization, progress schema, partclone/zstd pipeline and udpcast multicast behavior can remain authoritative.
