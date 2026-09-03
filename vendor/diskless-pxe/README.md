# ImageCtl Diskless PXE Bootstrap

Standalone preparation package for a future ImageCtl diskless client. It is deliberately isolated from the ImageCtl repository: no existing API, FastAPI route, DHCP configuration, or imaging contract is modified or invented here.

## Intended flow

UEFI/PXE -> iPXE -> existing ImageCtl HTTP service -> Linux kernel + initramfs -> RAM-resident Alpine -> Wayland/Cage -> Cog kiosk -> existing ImageCtl web UI -> existing imaging workflow.

iPXE is used so the large kernel/initramfs payloads can be fetched over HTTP. Only the initial firmware-to-iPXE hop may still need TFTP on machines that do not implement UEFI HTTP Boot.

## What is prepared

- `ipxe/boot.ipxe`: HTTP kernel/initramfs boot script with failure paths.
- `rootfs/`: Alpine package manifest, overlay, and rootfs staging script.
- `kiosk/`: Cage + Cog launcher and HTTP health check.
- `network/`: network-ready helper.
- `imaging/`: conservative disk discovery/safety checks plus an intentionally generic stream adapter.
- `tests/smoke.sh`: shell/static checks.
- `INTEGRATION.md`: hand-off checklist for the ImageCtl developer.

## Safety decisions

The package never silently selects the first disk. Imaging requires an explicit whole-disk target and runs a safety check first. The adapter does not hard-code a new HTTP image protocol: ImageCtl already uses an imaging/distribution workflow, so the developer should connect that existing receiver/decoder to this boundary.

## Build status

This is an integration-ready scaffold, not a hardware-certified boot image. A final initramfs must be built on Linux/Alpine and tested against the NIC/GPU/storage controllers used in the classrooms. Secure Boot is also intentionally left as an integration decision because it requires a signed trust chain.

## Quick static test

```sh
./tests/smoke.sh
```

## Integration variables

Copy `config.example` and replace the example server address. The developer should then map these values to ImageCtl's real configuration rather than maintaining duplicate configuration.

## Added pre-integration tooling (v0.2)

- Parameterized iPXE template + renderer.
- Retry/failure behavior that never writes local storage.
- RAM-client environment parsing from kernel command line.
- Kiosk watchdog/recovery loop.
- Boot/hardware diagnostics and standalone hardware inventory collector.
- Acceptance, security and performance test plans.
- Packaging target and expanded smoke tests, including a guard against destructive commands in automatic startup.

### Deliberately not implemented outside ImageCtl

The package does **not** invent API endpoints, authentication, progress JSON, image IDs, or multicast command arguments. Those must be wired to ImageCtl's actual contracts by the integrator. It also does not make Secure Boot policy decisions or auto-select a disk.

## 0.3 pre-integration additions
The handoff bundle now includes a Secure Boot decision plan, explicit failure matrix, QEMU-safe harness, CI/static checks, destructive-default guard, artifact validation, boot-flow document, and developer handoff checklist. These are designed to reduce integration work without modifying ImageCtl itself.

## Final pre-integration additions
This handoff also includes an OpenRC client overlay, runtime configuration examples/schema, kiosk supervisor, preflight checks, APK overlay packager, iPXE linting, a safe QCOW2-only QEMU harness, physical-lab gate, network requirements, troubleshooting runbook, and integration checklist. Imaging remains disabled by default and no host block device is passed into the VM harness.
