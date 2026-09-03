# Integration Checklist

- Map ImageCtl's existing HTTP paths to `boot.template.ipxe`.
- Map the existing client identity/MAC contract; do not invent fields.
- Map existing job state/progress/error contracts from ImageCtl docs.
- Connect `imaging-adapter.sh` to the existing partclone + zstd + udpcast workflow.
- Preserve multicast behavior for classroom deployment.
- Preserve existing authorization and destructive-operation gates.
- Decide how the existing signed GRUB/shim path coexists with or transitions to iPXE.
- Add generated boot artifacts to the existing installer/deployment process.
- Run ImageCtl's own test suite after integration.
- Run `docs/PHYSICAL_LAB_GATE.md` before real disk writes.
