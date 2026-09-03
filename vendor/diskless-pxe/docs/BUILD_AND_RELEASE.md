# Build and release

Run `make test` before packaging. `tools/verify-artifacts.sh` rejects missing boot artifacts and destructive commands in the automatic boot overlay.
Use a Linux build host for the initramfs. Record package versions and SHA-256 hashes for produced kernel/initramfs/iPXE binaries before lab deployment.
Never embed credentials in iPXE scripts, kernel command lines, or the initramfs image.
