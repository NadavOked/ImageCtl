# iPXE layer

`boot.template.ipxe` is the source template. Run `bin/render-ipxe.sh config.example out/boot.ipxe` to render it.
The initial firmware PXE hop may still use TFTP; kernel/initramfs payloads are fetched over the existing HTTP service. The script retries transient failures and does not alter local storage.

For production, decide Secure Boot signing/trust before replacing the existing signed boot path. Keep the current boot entry as rollback during rollout.
