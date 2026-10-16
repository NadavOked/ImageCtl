# finish.sh -- boot loader, host facts, and firstboot answers. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module consumes bootstrap and answer state

read_root_hash() {
    # build-iso writes the sha512-crypt hash (`openssl passwd -6`) to
    # imagectl/root-password.hash; there is no debian-installer preseed any more.
    _rh_file="$CDROM/imagectl/root-password.hash"; ROOT_PASSWORD_HASH=
    [ -r "$_rh_file" ] || fail root-password-source "cannot read $_rh_file"
    read -r ROOT_PASSWORD_HASH < "$_rh_file" || fail root-password-source "cannot read $_rh_file"
    case "$ROOT_PASSWORD_HASH" in '$6$'*) ;; *) fail root-password-source "rendered root password hash is missing" ;; esac
}

filesystem_uuid() {
    _fu_out=$(blkid -s UUID -o value "$1" 2>/dev/null); _fu_rc=$?
    [ "$_fu_rc" -eq 0 ] && [ -n "$_fu_out" ] || fail filesystem-uuid "cannot read UUID for $1"
    printf '%s' "$_fu_out"
}

install_bootloader() {
    read_root_hash
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ printf root:<redacted> | chroot %s chpasswd -e\n' "$TARGET"
    else
        printf 'root:%s\n' "$ROOT_PASSWORD_HASH" | chroot "$TARGET" chpasswd -e
        [ "$?" -eq 0 ] || fail root-password "chpasswd -e failed"
    fi
    must_run root-expiry "could not expire the initial root password" chroot "$TARGET" chage -d 0 root
    # The NVRAM entry is written through the target's efivarfs; a bind of
    # /sys does not carry that submount. Named, not fatal: EFI/BOOT
    # (--force-extra-removable) boots without an entry (ESXi, 21/09).
    _ib_efivars=0
    if [ "$DRY_RUN" -eq 0 ] && [ -d /sys/firmware/efi/efivars ]; then
        if mount -t efivarfs efivarfs "$TARGET/sys/firmware/efi/efivars" 2>/dev/null; then _ib_efivars=1
        else progress bootloader 70 "Configuring UEFI and BIOS boot" "efivarfs not mounted in the target: no NVRAM boot entry, EFI/BOOT fallback only"; fi
    fi
    # Debian's grub-install refuses --removable together with
    # --force-extra-removable; the latter alone writes EFI/debian and the
    # EFI/BOOT fallback (ESXi, 21/09).
    must_run grub-uefi "UEFI grub-install failed" chroot "$TARGET" grub-install \
        --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=debian \
        --force-extra-removable
    if [ "$_ib_efivars" -eq 1 ]; then
        umount "$TARGET/sys/firmware/efi/efivars" || fail efivars-unmount "could not unmount efivarfs from the target"
    fi
    must_run grub-bios "BIOS grub-install failed" chroot "$TARGET" grub-install --target=i386-pc "$ANSWER_DISK"
    # Boot straight through like ESXi, while leaving a short Esc/Shift window.
    # Written before update-grub reads it.
    write_target 0644 /etc/default/grub.d/imagectl.cfg "GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=3
"
    # A deterministic recovery path that does not depend on Debian's optional
    # recovery submenu. grub-probe runs when update-grub generates grub.cfg, so
    # both BIOS and UEFI installations use the installed root filesystem UUID.
    write_target 0755 /etc/grub.d/41_imagectl_recovery '#!/bin/sh
set -eu
root_uuid=$(grub-probe --target=fs_uuid /)
cat <<EOF
menuentry "ImageCtl recovery (text console)" {
    search --no-floppy --fs-uuid --set=root $root_uuid
    linux /vmlinuz root=UUID=$root_uuid ro nomodeset systemd.unit=multi-user.target
    initrd /initrd.img
}
EOF
'
    must_run grub-config "update-grub failed" chroot "$TARGET" update-grub
}

write_host_configuration() {
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command blkid -s UUID -o value "$ROOT_NODE"
        print_command blkid -s UUID -o value "$ESP_NODE"
        _wh_root_uuid='<root-uuid>'; _wh_esp_uuid='<esp-uuid>'
    else
        _wh_root_uuid=$(filesystem_uuid "$ROOT_NODE")
        _wh_esp_uuid=$(filesystem_uuid "$ESP_NODE")
    fi
    write_target 0644 /etc/fstab "UUID=$_wh_root_uuid / ext4 defaults 0 1
UUID=$_wh_esp_uuid /boot/efi vfat umask=0077 0 1
"
    write_target 0644 /etc/hostname "$ANSWER_HOSTNAME
"
    write_target 0644 /etc/hosts "127.0.0.1 localhost
127.0.1.1 $ANSWER_HOSTNAME
::1 localhost ip6-localhost ip6-loopback
"
    # #1447: Debian's default is Etc/UTC; the DCUI clock reads the OS zone.
    # Install Israel's zone before enabling time synchronization.
    must_run timezone-missing "Asia/Jerusalem is not in the target's zoneinfo" \
        chroot "$TARGET" test -f /usr/share/zoneinfo/Asia/Jerusalem
    must_run timezone "could not set the server timezone" \
        chroot "$TARGET" ln -sf /usr/share/zoneinfo/Asia/Jerusalem /etc/localtime
    write_target 0644 /etc/timezone "Asia/Jerusalem
"
    # Journal ordering, TLS validity, and session expiry all depend on a sane
    # clock. The package is in the offline ISO pool; enabling it here makes
    # synchronization start on the first installed-system boot.
    must_run time-sync "could not enable systemd-timesyncd" \
        chroot "$TARGET" systemctl enable systemd-timesyncd.service
    write_target 0600 /etc/imagectl/answers "disk=$ANSWER_DISK
role=$ANSWER_ROLE
primary_url=$ANSWER_PRIMARY_URL
primary_fingerprint=$ANSWER_PRIMARY_FINGERPRINT
servers_if=$ANSWER_SERVERS_IF
servers_mac=$ANSWER_SERVERS_MAC
servers_mode=$ANSWER_SERVERS_MODE
servers_addr=$ANSWER_SERVERS_ADDR
servers_mask=$ANSWER_SERVERS_MASK
servers_gw=$ANSWER_SERVERS_GW
servers_dns=$ANSWER_SERVERS_DNS
hostname=$ANSWER_HOSTNAME
admin_user=$ANSWER_ADMIN_USER
"
    write_target_secret /etc/imagectl/answers.secret "$ANSWER_ADMIN_PASS"
}
