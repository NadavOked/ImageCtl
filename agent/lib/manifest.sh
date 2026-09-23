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
    # will not mount is reported as null (not measured) rather than guessed --
    # null and a measured 0 are two states, and the manifest keeps them apart.
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
    # ‏null ולא 0: "לא הצלחנו למדוד" אינו "מדדנו, ריק" (עיקרון 5, #298). זהו
    # ערך JSON חוקי בשדה קיים — schema נשאר 1, וקורא ישן שמצפה למספר רואה null.
    echo null
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

# --- ה-GUID הייחודי של המחיצה: נקרא בקליטה ונקרא בחזרה בשחזור (#1212) --------
# ה-BCD של Windows מאתר את מחיצת המערכת לפי זוג GUID — של הדיסק ושל
# המחיצה (#26). עד #1212 הקריאה בקליטה נבלעה ב-`2>/dev/null` והפכה ל-"",
# ‏apply_gpt דילג על `-u` כשהערך ריק, sgdisk המציא GUID חדש, ואיש לא קרא
# אותו בחזרה: ‏winload.efi 0xc000000e על כל מחשב משוחזר, אחרי done. קורא
# אחד לשני הצדדים (capture.sh, ‏verify_table ב-expand.sh), ופרסור אחד.

valid_guid() {
    # $1 = value. 8-4-4-4-12 hex digits and nothing else.
    [ "${#1}" -eq 36 ] && printf '%s\n' "$1" \
        | LC_ALL=C grep -Eqx '[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}'
}

read_unique_guid() {
    # $1 = disk node, $2 = partition index. rc 0: stdout is the GUID.
    # Otherwise stdout is the reason, and the rc says which of three states:
    # 1 = sgdisk failed (nothing about the partition was read, its type
    # included), 2 = sgdisk printed no unique GUID, 3 = what it printed is
    # not a GUID. ‏stderr ליומן ולא ל-/dev/null: הסיבה של הכלי היא הראיה.
    _rg_out=$(sgdisk -i "$2" "$1" 2>> "$LOG_FILE")
    _rg_rc=$?
    [ "$_rg_rc" -eq 0 ] || { echo "sgdisk -i $2 failed (rc=$_rg_rc)"; return 1; }
    _rg=$(printf '%s\n' "$_rg_out" \
        | awk -F': ' '/Partition unique GUID/ { print $2; exit }' | awk '{print $1}')
    [ -n "$_rg" ] || { echo "sgdisk -i $2 printed no unique GUID"; return 2; }
    valid_guid "$_rg" || { echo "sgdisk -i $2 printed '$_rg', which is not a GUID"; return 3; }
    echo "$_rg"
}

unique_guid_gate() {
    # $1 = parts file (index|type_guid|unique_guid|...), $2 = the reads that
    # failed (index|rc|reason). stdout: the refusal, or nothing.
    # ‏rc 1 ו-3 נדחים בכל אימג': sgdisk שנכשל לא קרא גם את סוג המחיצה, ולכן
    # אין ממה לגזור שהאימג' *אינו* Windows; וערך שאינו GUID היה נופל על
    # ‏`sgdisk -u` בכל שחזור. ‏rc 2 (אין ערך) נדחה רק באימג' Windows — אותו
    # כלל כמו `_image_os`, מתפקידי המחיצות. לינוקס פטור כמו disk_guid בשרת
    # (‏server/capture.py: ‏GRUB מאתר לפי UUID של מערכת הקבצים), ונרשם ריק
    # כמו עד כאן — אבל ביומן, בשמו, ולא בשקט.
    [ -s "$2" ] || return 0
    _gw=$(awk -F'|' '$2 != 2 { r = $0; sub(/^[^|]*\|[^|]*\|/, "", r)
        print "מחיצה " $1 ": לא ניתן לקרוא את ה-GUID הייחודי — " r; exit }' "$2")
    [ -n "$_gw" ] && { echo "$_gw"; return 0; }
    while IFS='|' read -r _gi _gt _grest; do
        [ "$(_partition_role "$_gt")" = windows ] || continue
        awk -F'|' '{ r = $0; sub(/^[^|]*\|[^|]*\|/, "", r)
            print "מחיצה " $1 ": אימג Windows בלי GUID ייחודי (" r ") — ה-BCD נקשר אליו (#26)"; exit }' "$2"
        return 0
    done < "$1"
    while IFS='|' read -r _gi _grc _gr; do
        log "partition $_gi: $_gr -- recorded empty; not a windows image, exempt like disk_guid" >&2
    done < "$2"
}

verify_unique_guids() {
    # $1 = disk name, $2 = plan file (manifest_plan). Every partition whose
    # manifest carries a unique GUID is read back from the disk and compared.
    # מחיצה בלי GUID במניפסט (אימג' ישן, או לינוקס שנפטר בקליטה) אינה
    # נכשלת, אבל נרשמת "לא נבדק" — "לא נבדק" ו"נבדק, תואם" הם שני מצבים.
    # ‏PLAN_ERROR נושא את הסיבה אל היעד, כמו בשער של apply_gpt (עיקרון 4).
    _vn=0
    while IFS='|' read -r _vi _r _r _r _r _r _r _r _r _vu _r; do
        if [ -z "$_vu" ]; then
            log "$1: partition $_vi has no unique GUID in the manifest -- not checked"
            continue
        fi
        if ! _vg=$(read_unique_guid "$DEVROOT/$1" "$_vi"); then
            PLAN_ERROR="partition $_vi: unique GUID could not be read back from the disk ($_vg)"
        elif [ "$(printf '%s' "$_vg" | tr 'a-f' 'A-F')" != "$(printf '%s' "$_vu" | tr 'a-f' 'A-F')" ]; then
            PLAN_ERROR="partition $_vi: unique GUID on the disk is $_vg, not $_vu (#26)"
        else
            _vn=$((_vn + 1))
            continue
        fi
        log "$1: $PLAN_ERROR"
        return 1
    done < "$2"
    log "$1: $_vn partition unique GUIDs came back from the disk"
}
