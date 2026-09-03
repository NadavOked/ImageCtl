# manifest.sh -- how a source disk is described in the manifest it produces.
# POSIX sh (busybox ash).
#
# ‏capture.sh מזרים את הבייטים; כאן יושבים השדות שהמניפסט מצהיר עליהם
# ונגזרים מהדיסק עצמו — ‏role, ‏fs, ‏uuid, ‏used_bytes, ‏os ו-expandable.
# הפרדה ולא שינוי: אותן פונקציות, אותם שמות, אותה סמנטיקה, ושני הקבצים
# נטענים יחד (‏imagectl-agent טוען את manifest.sh לפני capture.sh).

_partition_role() {
    # $1 = type guid. The roles named in section 1 of the interfaces.
    case "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" in
        C12A7328-F81F-11D2-BA4B-00A0C93EC93B) echo "esp" ;;
        E3C9E316-0B5C-4DB8-817D-F92DF00215AE) echo "msr" ;;
        DE94BBA4-06D1-4D40-A16A-BFD50179D6AC) echo "recovery" ;;
        0FC63DAF-8483-4772-8E79-3D69D8477DE4) echo "linux" ;;
        0657FD6D-A4AB-43C4-84E5-0933C84B4F4F) echo "swap" ;;
        EBD0A0A2-B9E5-4433-87C0-68B6B72699C7) echo "windows" ;;
        *) echo "data" ;;
    esac
}

_fs_of() {
    blkid -o value -s TYPE "$1" 2>/dev/null || echo "unknown"
}

_uuid_of() {
    # ה-UUID של מערכת הקבצים עצמה — לא ה-GUID של רשומת ה-GPT. ל-swap זה מה
    # ש-mkswap חותם בכותרת ומה ש-/etc/fstab של מתקין דביאן מחפש (‏UUID=); בלי
    # לתעדו כל שחזור מקבל UUID חדש וה-swap לא עולה (#48). אין UUID — ריק.
    blkid -o value -s UUID "$1" 2>/dev/null || echo ""
}

_used_bytes() {
    # $1 = node, $2 = fs. Mounted read-only just to measure; a partition that
    # will not mount is reported as 0 with a warning rather than guessed.
    _m="$RUN_DIR/probe"
    mkdir -p "$_m"
    # ‏-t מפורש, ולא זיהוי אוטומטי: ‏mount בלי סוג בוחר רק מבין מערכות הקבצים
    # שכבר טעונות (/proc/filesystems), ואילו `-t ext4` מבקש מהקרנל לטעון את
    # המודול. כל עוד עץ kernel/fs לא נארז בכלל שני המסלולים נכשלו, וכל
    # המחיצות דיווחו used_bytes=0 (#84). ‏stderr ליומן ולא ל-/dev/null:
    # "לא הצלחנו לעגן" ו"עגנו וזה ריק" הם שני מצבים שונים, והסיבה מבדילה.
    if mount -t "$2" -o ro "$1" "$_m" 2>>"$LOG_FILE" \
       || mount -o ro "$1" "$_m" 2>>"$LOG_FILE"; then
        # ‏-kP ולא -B1: ה-df של busybox לא מכיר ‎-B, וכל המחיצות דווחו
        # בשקט used_bytes=0 (נתפס באימג' הראשון של המעבדה, #12).
        _used=$(df -kP "$_m" 2>/dev/null | awk 'NR==2 {print $3 * 1024}')
        umount "$_m" 2>/dev/null
        [ -n "$_used" ] && { echo "$_used"; return 0; }
    fi
    # An image captured from a hibernated Windows mounts dirty. Section 9 of the
    # spec tells the operator to run powercfg /h off first; saying so here is the
    # difference between a puzzling number and a known cause. ‏>&2: הפונקציה
    # נקראת בתוך $( ) ו-log מדבר גם ל-stdout — אחרת האזהרה שוברת מניפסט (#12).
    log "WARNING: $1 would not mount -- used_bytes unknown (hibernation? run powercfg /h off)" >&2
    echo 0
}

_image_os() {
    # The image's operating system (spec section 14), from the partition
    # roles alone: a windows partition makes it windows, else linux.
    case "$1" in
        *'"role":"windows"'*) echo "windows" ;;
        *'"role":"linux"'*)   echo "linux" ;;
        *)                    echo "unknown" ;;
    esac
}

_mark_expandable() {
    # Marks the windows/linux partition that sits last *on the disk* -- by
    # start_sector, not by position in the list. The two are not the same thing:
    # this list is built in index order (that is what `sgdisk -p` prints), and on
    # the Debian cloud image the root is partition 1, first in the list and last
    # on the platter. Reading the list in order picked partition 16 (`data`),
    # marked nothing, and every 256->500 restore of that image quietly ended with
    # 244GB unallocated (#58).
    #
    # Whatever follows the candidate no longer disqualifies it. Restore rewrites
    # the whole table from the manifest anyway, so it moves that tail to the end
    # of the disk before a byte arrives and stretches the candidate up to it:
    # swap is recreated there (spec section 14, #46), anything else -- the
    # recovery partition that Windows 11 always puts last -- is written there
    # from its own stream file, by index (#58). The server enforces the same line
    # at intake. A manifest without start_sector (there is no such thing in
    # practice) falls back to the last windows/linux in the list, as before.
    printf '%s' "$1" | awk '{
        n = split($0, parts, "},");
        best = 0; pick = 0;
        for (i = 1; i <= n; i++) {
            if (parts[i] !~ /"role":"(windows|linux)"/) continue;
            s = -1;
            if (match(parts[i], /"start_sector":[0-9]+/))
                s = substr(parts[i], RSTART + 15, RLENGTH - 15) + 0;
            if (pick == 0 || s >= best) { best = s; pick = i; }
        }
        if (pick > 0)
            sub(/"expandable":false/, "\"expandable\":true", parts[pick]);
        out = "";
        for (i = 1; i < n; i++) out = out parts[i] "},";
        printf "%s%s", out, parts[n];
    }'
}
