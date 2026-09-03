# Developer handoff checklist

This directory is intentionally outside ImageCtl. It is a pre-integration implementation package.

Before copying anything into ImageCtl:
1. Compare `docs/interfaces.md` in ImageCtl with the adapter boundary here. Do not add undocumented fields.
2. Decide the Secure Boot chain on real hardware.
3. Build the RAM client on the intended Linux build host and pin package/repository versions.
4. Test NIC, storage, graphics, keyboard and DHCP on each representative hardware family.
5. Wire the existing agent task protocol to imaging; preserve partclone + zstd + udpcast semantics.
6. Preserve ImageCtl's default-local-boot behavior for unknown/no-task/error states.
7. Run ImageCtl's native and E2E suites plus this package's checks.
8. Perform first destructive restore only on a disposable test disk.

Items intentionally left for integration: exact API field mapping, server routes, boot-server installer edits, production signing keys, production DHCP configuration, and hardware-specific firmware quirks.
