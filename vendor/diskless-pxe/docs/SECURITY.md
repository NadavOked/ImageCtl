# Security notes for integration

- Do not disable Secure Boot merely to make iPXE convenient; choose and test a signing/trust-chain strategy or retain the existing signed path.
- Treat boot scripts, kernel, initramfs and overlays as privileged artifacts. Serve them from a controlled network path and add integrity/signature verification where the final trust model permits.
- Never accept a target block device solely from browser input. Server authorization and local validation should both be required.
- Do not embed API secrets in `boot.ipxe`, kernel command lines, or public HTTP files; kernel command lines are locally observable.
- Keep the imaging VLAN/firewall policy separate from kiosk/UI convenience.
- Log failures without logging credentials/tokens.
