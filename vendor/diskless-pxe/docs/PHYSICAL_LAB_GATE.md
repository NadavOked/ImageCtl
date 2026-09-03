# Physical Lab Gate

This is the deliberate stopping point for this handoff. Do not cross it without a disposable physical test machine or explicit ImageCtl integration work.

## Preconditions before physical boot
1. Confirm DHCP architecture matching for UEFI x86_64 and the intended iPXE EFI binary.
2. Confirm the HTTP boot URLs are reachable from the imaging VLAN.
3. Decide the Secure Boot trust chain and sign every executable component accordingly.
4. Verify NIC, storage and graphics support on each hardware family.
5. Keep imaging disabled (`ALLOW_IMAGING=0`) during first boot tests.
6. Prove local-boot/fallback behavior with the server unavailable.
7. Only then test against a disposable disk with a known backup.

## Exit criteria
PXE -> iPXE -> kernel/initramfs -> network -> kiosk succeeds repeatedly; reboot/failure paths are deterministic; no disk write occurs without explicit authorization.
