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
    # Nadav, 23/09 (#1208): the server boots straight through like ESXi and
    # Esc opens the menu. hidden needs a timeout above 0: with 0 there is
    # no window in which to press Esc. Written before update-grub reads it.
    write_target 0644 /etc/default/grub.d/imagectl.cfg "GRUB_TIMEOUT_STYLE=hidden
GRUB_TIMEOUT=2
"
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
    write_target 0600 /etc/imagectl/answers "disk=$ANSWER_DISK
role=$ANSWER_ROLE
primary_url=$ANSWER_PRIMARY_URL
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
