# Secure Boot integration plan

ImageCtl currently documents a Microsoft-signed shim -> Debian-signed GRUB -> distro kernel chain. Treat that as the known-good baseline.

The iPXE path must not silently require disabling Secure Boot. Integration choices to validate on target hardware:
- keep the existing signed GRUB path and use HTTP-capable boot logic where practical;
- introduce a properly signed/trusted iPXE binary and validate firmware/SBAT/signature behavior;
- retain an immediate local-boot fallback.

Acceptance gate: no deployment recommendation until the chosen chain boots with Secure Boot enabled on representative physical models.
