# hostname.sh -- writes the computer name into the restored system before
# its first boot (interfaces.md section 5). Windows and Linux alike.
# POSIX sh (busybox ash).
#
# Windows: offline registry editing, not a running system. The SYSTEM hive is
# mounted from the restored NTFS partition and three values are set.
# "CurrentControlSet" does not exist offline -- it is a runtime alias -- so
# the real control set is read from Select\Current first. Editing the wrong
# one silently does nothing.
#
# Linux: /etc/hostname is replaced and the 127.0.1.1 line of /etc/hosts is
# rewritten, which is what every Debian/Ubuntu-family installer does.

HOSTNAME_METHOD="offline-registry"
HOSTNAME_METHOD_LINUX="etc-hostname"

_mount_windows() {
    # $1 = disk, $2 = manifest. Echoes the mount point, or fails.
    _idx=$(manifest_plan "$2" | awk -F'|' '$3 == "windows" { print $1; exit }')
    [ -n "$_idx" ] || { log "no windows partition in the manifest"; return 1; }
    _node=$(partition_node "$1" "$_idx")
    _mnt="$RUN_DIR/win"
    mkdir -p "$_mnt"
    # remove_hiberfile: an image captured from a hibernated machine leaves the
    # volume "dirty", and ntfs-3g would mount it read-only.
    if ! ntfs-3g -o remove_hiberfile "$_node" "$_mnt" >> "$LOG_FILE" 2>&1; then
        log "could not mount $_node"
        return 1
    fi
    echo "$_mnt"
}

_mount_linux() {
    # $1 = disk, $2 = manifest. Echoes the mount point, or fails. A btrfs
    # root usually lives in a subvolume (Ubuntu: "@"), so if there is no
    # /etc at the top level the mount is retried with that subvolume.
    _idx=$(manifest_plan "$2" | awk -F'|' '$3 == "linux" { print $1; exit }')
    [ -n "$_idx" ] || { log "no linux partition in the manifest"; return 1; }
    _fs=$(manifest_plan "$2" | awk -F'|' -v i="$_idx" '$1 == i { print $4 }')
    _node=$(partition_node "$1" "$_idx")
    _mnt="$RUN_DIR/linux"
    mkdir -p "$_mnt"
    mount -t "$_fs" "$_node" "$_mnt" >> "$LOG_FILE" 2>&1 || { log "could not mount $_node"; return 1; }
    if [ ! -d "$_mnt/etc" ] && [ "$_fs" = "btrfs" ]; then
        umount "$_mnt" 2>/dev/null
        mount -t btrfs -o subvol=@ "$_node" "$_mnt" >> "$LOG_FILE" 2>&1 \
            || { log "could not mount $_node (subvol @)"; return 1; }
    fi
    [ -d "$_mnt/etc" ] || { umount "$_mnt" 2>/dev/null; log "no /etc on $_node"; return 1; }
    echo "$_mnt"
}

_write_linux_files() {
    # $1 = the restored system's /etc, $2 = name. Replaces /etc/hostname and
    # the 127.0.1.1 line the installer wrote in /etc/hosts (added if absent).
    printf '%s\n' "$2" > "$1/hostname" || return 1
    if [ -f "$1/hosts" ]; then
        awk -v n="$2" 'BEGIN { done = 0 }
            /^127\.0\.1\.1[ \t]/ { print "127.0.1.1\t" n; done = 1; next }
            { print }
            END { if (!done) print "127.0.1.1\t" n }' "$1/hosts" > "$1/hosts.new" \
            && mv "$1/hosts.new" "$1/hosts" || return 1
    else
        printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n' "$2" > "$1/hosts" || return 1
    fi
}

_write_hostname_linux() {
    # $1 = disk, $2 = manifest, $3 = name. Emits the section 5 result.
    _mnt=$(_mount_linux "$1" "$2") || {
        printf '{"ok":false,"error":"could not mount the linux partition","code":"mount_failed"}\n'
        return 1
    }
    log "writing hostname $3 into $_mnt/etc/hostname"
    _write_linux_files "$_mnt/etc" "$3"
    _rc=$?
    sync
    umount "$_mnt" 2>/dev/null
    if [ "$_rc" -ne 0 ]; then
        printf '{"ok":false,"error":"could not write /etc/hostname","code":"hostname_write_failed"}\n'
        return 1
    fi
    printf '{"ok":true,"hostname":"%s","method":"%s"}\n' "$3" "$HOSTNAME_METHOD_LINUX"
}

_control_set() {
    # $1 = hive path. Echoes e.g. ControlSet001. הקריאה דרך hivewrite -g
    # ולא hivexget — זה wrapper ל-hivexsh שלא רץ ב-initramfs כלל (#33).
    _current=$(hivewrite -g "$1" Select Current 2>/dev/null | tr -dc '0-9')
    [ -n "$_current" ] || _current=1
    printf 'ControlSet%03d' "$_current"
}

write_hostname() {
    # $1 = disk name, $2 = manifest file, $3 = hostname.
    # Emits the section 5 result object on stdout.
    _disk="$1"; _manifest="$2"; _name="$3"

    case "$_name" in
        *[!A-Za-z0-9-]*|"")
            printf '{"ok":false,"error":"invalid hostname","code":"bad_hostname"}\n'
            return 1 ;;
    esac
    # NetBIOS caps names at 15 characters; a longer one is silently truncated
    # by Windows and the machine ends up with a name nobody expects.
    [ "${#_name}" -le 15 ] || {
        printf '{"ok":false,"error":"hostname longer than 15 characters","code":"bad_hostname"}\n'
        return 1
    }

    # Which system is on the disk decides how the name is written. The
    # partition roles in the manifest are the source of truth for this
    # (spec section 14) -- there is no separate "which OS" flag to get wrong.
    if ! manifest_plan "$_manifest" 2>/dev/null | awk -F'|' '$3 == "windows"' | grep -q .; then
        _write_hostname_linux "$_disk" "$_manifest" "$_name"
        return $?
    fi

    _mnt=$(_mount_windows "$_disk" "$_manifest") || {
        printf '{"ok":false,"error":"could not mount the windows partition","code":"mount_failed"}\n'
        return 1
    }

    _hive="$_mnt/Windows/System32/config/SYSTEM"
    if [ ! -f "$_hive" ]; then
        umount "$_mnt" 2>/dev/null
        printf '{"ok":false,"error":"SYSTEM hive not found","code":"no_hive"}\n'
        return 1
    fi

    _cs=$(_control_set "$_hive")
    log "writing hostname $_name into $_cs"

    # שלושה ערכים בשני מפתחות, כולם דרך hivewrite (‏agent/hivewrite.c):
    # כתיבה משמרת-ערכים — ‏setval של hivexsh מחליף את *כל* רשימת ערכי
    # המפתח, וב-Tcpip\Parameters זה היה מוחק את שאר הגדרות ה-TCP/IP ‏(#33).
    # ‏ActiveComputerName הוא מפתח *נדיף* — קיים רק בזיכרון של Windows רץ,
    # ‏Windows בונה אותו מחדש מ-ComputerName בכל אתחול. ‏Hostname נבנה
    # מ-"NV Hostname" באתחול, אבל נכתבים שניהם — כמו שעושה sysprep.
    hivewrite "$_hive" "$_cs\\Control\\ComputerName\\ComputerName" \
        ComputerName "$_name" >> "$LOG_FILE" 2>&1
    _rc=$?
    hivewrite "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
        Hostname "$_name" "NV Hostname" "$_name" >> "$LOG_FILE" 2>&1
    _rc2=$?
    # "נכתב" הוא מה שנקרא בחזרה מהדיסק — לא קוד יציאה. הכתיבה הקודמת
    # כבר החזירה 0 פעם אחת בלי לכתוב כלום (#33).
    _back=$(hivewrite -g "$_hive" "$_cs\\Control\\ComputerName\\ComputerName" \
                ComputerName 2>> "$LOG_FILE")
    _back_host=$(hivewrite -g "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
                Hostname 2>> "$LOG_FILE")
    _back_nv=$(hivewrite -g "$_hive" "$_cs\\Services\\Tcpip\\Parameters" \
                "NV Hostname" 2>> "$LOG_FILE")
    sync
    umount "$_mnt" 2>/dev/null

    if [ "$_rc" -ne 0 ] || [ "$_rc2" -ne 0 ] || [ "$_back" != "$_name" ] \
        || [ "$_back_host" != "$_name" ] || [ "$_back_nv" != "$_name" ]; then
        log "hostname verify failed: wrote '$_name', read back computer='$_back' hostname='$_back_host' nv='$_back_nv' (rc=$_rc/$_rc2)"
        printf '{"ok":false,"error":"registry edit failed","code":"hive_write_failed"}\n'
        return 1
    fi
    printf '{"ok":true,"hostname":"%s","method":"%s"}\n' "$_name" "$HOSTNAME_METHOD"
}

compose_hostname() {
    # $1 = prefix, $2 = suffix. INS is always uppercase (section 10).
    printf '%s-%s' "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" \
                   "$(printf '%s' "$2" | tr 'a-z' 'A-Z')"
}
