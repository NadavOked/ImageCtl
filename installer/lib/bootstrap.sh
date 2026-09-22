# bootstrap.sh -- Debian bootstrap and offline package payload. POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares engine and answer state

TARGET="${TARGET:-/target}"
CDROM="${CDROM:-/cdrom}"
BOOTSTRAP_LOG="${BOOTSTRAP_LOG:-/run/imagectl/debootstrap.log}"
MOUNT_DEV=0; MOUNT_PROC=0; MOUNT_SYS=0; MOUNT_CDROM=0; MOUNT_ESP=0; MOUNT_ROOT=0

last_bootstrap_line() {
    [ -s "$BOOTSTRAP_LOG" ] || return 0
    tail -n 1 "$BOOTSTRAP_LOG" | tr '\r\n' '  '
}

run_debootstrap() {
    if [ "$DRY_RUN" -eq 1 ]; then
        print_command debootstrap --arch amd64 --no-check-gpg trixie "$TARGET" "file://$CDROM"
        return
    fi
    mkdir -p "${BOOTSTRAP_LOG%/*}" || fail bootstrap-log "cannot create bootstrap log directory"
    # debootstrap is #!/bin/sh; in the live image that is busybox ash, whose
    # dpkg-deb applet has no --fsys-tarfile ("tar failed" on the first
    # package, ESXi 21/09). Run it under dash, which finds the real dpkg-deb.
    _bd_sh=$(command -v dash 2>/dev/null) || _bd_sh=
    _bd_bin=$(command -v debootstrap 2>/dev/null) || fail bootstrap-missing "debootstrap is not on this image"
    # The exit code comes back through a file, not a bare `wait`: the wait
    # has a ceiling -- a log that has not grown for BOOTSTRAP_STALL_S is a
    # hung debootstrap, killed and named, not a frozen progress screen.
    _bd_rcfile="${BOOTSTRAP_LOG}.rc"; rm -f "$_bd_rcfile"
    { ${_bd_sh:+"$_bd_sh"} "$_bd_bin" --arch amd64 --no-check-gpg trixie "$TARGET" "file://$CDROM" > "$BOOTSTRAP_LOG" 2>&1; echo "$?" > "$_bd_rcfile"; } &
    _bd_pid=$!
    _bd_size=0; _bd_still=0
    while kill -0 "$_bd_pid" 2>/dev/null; do
        _bd_last=$(last_bootstrap_line)
        progress bootstrap 25 "Installing Debian base" "$_bd_last"
        _bd_now=$(wc -c < "$BOOTSTRAP_LOG" 2>/dev/null || echo 0)
        if [ "$_bd_now" = "$_bd_size" ]; then _bd_still=$((_bd_still + 1)); else _bd_size=$_bd_now; _bd_still=0; fi
        if [ "$_bd_still" -ge "${BOOTSTRAP_STALL_S:-900}" ]; then
            kill -9 "$_bd_pid" 2>/dev/null
            PROGRESS_LOG=$_bd_last; fail bootstrap-stalled "debootstrap wrote nothing for ${BOOTSTRAP_STALL_S:-900}s and was stopped"
        fi
        sleep 1
    done
    _bd_rc=$(cat "$_bd_rcfile" 2>/dev/null) || _bd_rc=
    _bd_last=$(last_bootstrap_line)
    [ -n "$_bd_rc" ] || fail bootstrap-failed "debootstrap ended without an exit code"
    [ "$_bd_rc" -eq 0 ] || { PROGRESS_LOG=$_bd_last; fail bootstrap-failed "debootstrap exited $_bd_rc"; }
    progress bootstrap 35 "Installing Debian base" "$_bd_last"
}

read_package_list() {
    _rp_file="$CDROM/imagectl-src/tools/iso/packages.txt"
    [ -r "$_rp_file" ] || fail packages-list-missing "cannot read $_rp_file"
    PACKAGE_WORDS=
    while IFS= read -r _rp_line || [ -n "$_rp_line" ]; do
        _rp_line=${_rp_line%%#*}
        for _rp_pkg in $_rp_line; do
            case "$_rp_pkg" in *[!A-Za-z0-9+.-]*) fail packages-list-invalid "invalid package name: $_rp_pkg" ;; esac
            case " $PACKAGE_WORDS " in *" $_rp_pkg "*) ;; *) PACKAGE_WORDS="$PACKAGE_WORDS $_rp_pkg" ;; esac
        done
    done < "$_rp_file"
    [ -n "$PACKAGE_WORDS" ] || fail packages-list-empty "package list is empty"
}

mount_target_root() {
    must_run target-root-dir "could not create the target directory" mkdir -p "$TARGET"
    must_run mount-root "could not mount the target root" mount "$ROOT_NODE" "$TARGET"; MOUNT_ROOT=1
    must_run target-esp-dir "could not create the EFI mount point" mkdir -p "$TARGET/boot/efi"
    must_run mount-esp "could not mount the EFI partition" mount "$ESP_NODE" "$TARGET/boot/efi"; MOUNT_ESP=1
}

bind_target_filesystems() {
    must_run target-dirs "could not create target bind points" mkdir -p \
        "$TARGET/dev" "$TARGET/proc" "$TARGET/sys" "$TARGET/cdrom"
    must_run mount-dev "could not bind /dev" mount --bind /dev "$TARGET/dev"; MOUNT_DEV=1
    must_run mount-proc "could not bind /proc" mount --bind /proc "$TARGET/proc"; MOUNT_PROC=1
    must_run mount-sys "could not bind /sys" mount --bind /sys "$TARGET/sys"; MOUNT_SYS=1
    must_run mount-cdrom "could not bind /cdrom" mount --bind "$CDROM" "$TARGET/cdrom"; MOUNT_CDROM=1
}

copy_local_repo() {
    must_run repo-dirs "could not create the local apt repository" mkdir -p "$TARGET/var/lib/imagectl/apt-repo/pool"
    _cr_seen=0
    for _cr_comp in "$CDROM"/pool/*/imagectl; do
        [ -d "$_cr_comp" ] || continue
        _cr_name=${_cr_comp#"$CDROM/pool/"}; _cr_name=${_cr_name%%/*}
        must_run repo-component "could not create apt component $_cr_name" mkdir -p "$TARGET/var/lib/imagectl/apt-repo/pool/$_cr_name"
        must_run repo-copy "could not copy apt component $_cr_name" cp -a "$_cr_comp" "$TARGET/var/lib/imagectl/apt-repo/pool/$_cr_name/"
        _cr_seen=$((_cr_seen + 1))
    done
    if [ "$DRY_RUN" -eq 0 ]; then [ "$_cr_seen" -gt 0 ] || fail apt-repo-missing "the ISO contains no imagectl package pool"; fi
    must_run repo-index "could not copy apt indexes" cp -a "$CDROM/dists" "$TARGET/var/lib/imagectl/apt-repo/"
    if [ "$DRY_RUN" -eq 1 ]; then
        printf '+ verify-package-pool %s/pool %s/var/lib/imagectl/apt-repo/pool\n' "$CDROM" "$TARGET"
    else
        _cr_dir=${BOOTSTRAP_LOG%/*}
        find "$CDROM/pool" -path '*/imagectl/*.deb' -print > "$_cr_dir/packages.source" \
            || fail apt-repo-check "could not enumerate packages on the ISO"
        find "$TARGET/var/lib/imagectl/apt-repo/pool" -name '*.deb' -print > "$_cr_dir/packages.target" \
            || fail apt-repo-check "could not enumerate copied packages"
        _cr_src=$(wc -l < "$_cr_dir/packages.source") || fail apt-repo-check "could not count ISO packages"
        _cr_dst=$(wc -l < "$_cr_dir/packages.target") || fail apt-repo-check "could not count copied packages"
        case "$_cr_src:$_cr_dst" in *[!0-9:]*) fail apt-repo-check "invalid package counts" ;; esac
        [ "$_cr_src" -gt 0 ] && [ "$_cr_dst" -eq "$_cr_src" ] ||
            fail apt-repo-check "package pool copy failed ($_cr_dst of $_cr_src files)"
        rm -f "$_cr_dir/packages.source" "$_cr_dir/packages.target" \
            || fail apt-repo-check "could not remove package count files"
    fi
}

write_installer_facts() {
    write_target 0644 /etc/imagectl/installer-nic "state=configured
interface=$ANSWER_SERVERS_IF
mac=$ANSWER_SERVERS_MAC
reason=$ANSWER_SERVERS_MODE
d_i_choose_interface=$ANSWER_SERVERS_IF
source=/etc/imagectl/answers
"
    write_target 0644 /etc/imagectl/installer-role "role=$ANSWER_ROLE
primary=$ANSWER_PRIMARY_URL
"
    must_run iso-manifest "could not copy imagectl-iso.json" cp "$CDROM/imagectl-iso.json" "$TARGET/etc/imagectl/iso-release.json"
}

install_target_packages() {
    read_package_list
    write_target 0644 /etc/apt/sources.list "deb [trusted=yes] file:/cdrom trixie main contrib non-free-firmware
"
    must_run apt-sources-clean "could not clear inherited apt sources" rm -f \
        "$TARGET/etc/apt/sources.list.d/"'*.list' "$TARGET/etc/apt/sources.list.d/"'*.sources'
    DEBIAN_FRONTEND=noninteractive; export DEBIAN_FRONTEND
    must_run apt-update "offline apt update failed" chroot "$TARGET" apt-get update
    # PACKAGE_WORDS contains only validated apt package names; splitting is intentional.
    # shellcheck disable=SC2086
    must_run apt-install "offline package installation failed" chroot "$TARGET" apt-get install -y --no-install-recommends \
        $PACKAGE_WORDS linux-image-amd64 grub-efi-amd64-signed shim-signed grub-pc-bin
    must_run source-copy "could not copy imagectl-src" cp -a "$CDROM/imagectl-src" "$TARGET/opt/imagectl-src"
    # .git is what the update button works on (#1185). An ISO from the public
    # clone always carries it; a lab ISO (build-iso --source) says so in its
    # manifest (source_git=false) and is allowed through, named on screen.
    if [ "$DRY_RUN" -eq 0 ] && [ ! -e "$TARGET/opt/imagectl-src/.git" ]; then
        _src_git=$(jq -r 'if .source_git == false then "no" else "yes" end' "$CDROM/imagectl-iso.json" 2>/dev/null || echo yes)
        [ "$_src_git" = no ] || fail source-git-missing "imagectl-src was copied without .git"
        progress packages 62 "Installing packages" "lab ISO (--source): imagectl-src has no .git, the update button will not work on this server"
    fi
    must_run firstboot-copy "could not install firstboot" cp "$CDROM/imagectl/firstboot.sh" "$TARGET/usr/local/sbin/imagectl-firstboot"
    must_run firstboot-mode "could not mark firstboot executable" chmod 0755 "$TARGET/usr/local/sbin/imagectl-firstboot"
    must_run firstboot-service "could not install firstboot service" cp "$CDROM/imagectl/imagectl-firstboot.service" "$TARGET/etc/systemd/system/imagectl-firstboot.service"
    must_run firstboot-enable "could not enable firstboot service" chroot "$TARGET" systemctl enable imagectl-firstboot.service
    copy_local_repo
    write_target 0644 /etc/apt/sources.list ""
    write_target 0644 /etc/apt/sources.list.d/imagectl-iso.list \
        "deb [trusted=yes] file:/var/lib/imagectl/apt-repo trixie main contrib
"
    write_installer_facts
}

cleanup_mounts() {
    [ "$DRY_RUN" -eq 0 ] || return 0
    if [ "$MOUNT_CDROM" -eq 1 ]; then if umount "$TARGET/cdrom"; then MOUNT_CDROM=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET/cdrom" >&2; fi; fi
    if [ "$MOUNT_SYS" -eq 1 ]; then if umount "$TARGET/sys"; then MOUNT_SYS=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET/sys" >&2; fi; fi
    if [ "$MOUNT_PROC" -eq 1 ]; then if umount "$TARGET/proc"; then MOUNT_PROC=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET/proc" >&2; fi; fi
    if [ "$MOUNT_DEV" -eq 1 ]; then if umount "$TARGET/dev"; then MOUNT_DEV=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET/dev" >&2; fi; fi
    # debootstrap mounts its own proc inside the target and leaves it
    # (our bind stacked on top, so the flag-tracked umount peeled one layer
    # and the root stayed busy -- ESXi, 21/09). Anything still mounted under
    # the target that is not the ESP or the root goes now, deepest first,
    # each one named.
    _cm_leftover=$(awk -v t="$TARGET/" 'index($2, t) == 1 && $2 != t "boot/efi" { print $2 }' "$PROC_MOUNTS" 2>/dev/null | sort -r)
    for _cm_mp in $_cm_leftover; do
        if umount "$_cm_mp"; then printf 'installer: unmounted leftover %s\n' "$_cm_mp" >&2
        else printf 'installer: [unmount-failed] %s\n' "$_cm_mp" >&2; fi
    done
    if [ "$MOUNT_ESP" -eq 1 ]; then if umount "$TARGET/boot/efi"; then MOUNT_ESP=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET/boot/efi" >&2; fi; fi
    if [ "$MOUNT_ROOT" -eq 1 ]; then if umount "$TARGET"; then MOUNT_ROOT=0; else printf 'installer: [unmount-failed] %s\n' "$TARGET" >&2; fi; fi
}
