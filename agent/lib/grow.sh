# grow.sh -- הצד השני של ההרחבה: אחרי שהזרם נגמר, מערכת הקבצים נמתחת
# לגודל המחיצה שכבר הורחבה לפניו.
# POSIX sh (busybox ash).
#
# פוצל מ-expand.sh ב-#478, כשזה הגיע ל-305 שורות. הכותרת של expand.sh
# טענה ששני שלבי ההרחבה "חייבים להישאר צמודים" — הם אינם קוראים זה
# לזה. הצימוד הוא דרך הסימון `$RUN_DIR/targets/<disk>/expanded`:
# ‏expand_last כותב אותו לפני הזרם, grow_expanded קורא אותו אחריו.
# הקובץ הזה נטען מיד אחרי expand.sh ב-imagectl-agent, ותלוי ב-
# ‏partition_node (restore.sh) ו-target_set (progress.sh) — שניהם
# נטענים לפניו.

grow_expanded() {
    # $1 = disk name. The second half of the expansion: the partition was
    # widened before the stream, and now that there is a filesystem inside it
    # it is told to follow. Nothing was widened -- nothing to do.
    _mark="$RUN_DIR/targets/$1/expanded"
    [ -f "$_mark" ] || return 0
    _idx=$(cut -d'|' -f1 "$_mark")
    _fs=$(cut -d'|' -f2 "$_mark")
    grow_filesystem "$_fs" "$(partition_node "$1" "$_idx")"
}

grow_filesystem() {
    # $1 = fs, $2 = partition node. The partition has already been enlarged;
    # this makes the filesystem inside it follow. Each family has its own
    # tool (spec section 14) -- btrfs can only be resized while mounted.
    case "$1" in
        ntfs)
            ntfsresize --force --no-progress-bar "$2" >> "$LOG_FILE" 2>&1
            _rc=$?
            [ "$_rc" = "0" ] && return 0
            log "$2: ntfsresize נכשל (rc=${_rc:-לא נרשם})"
            return 1 ;;
        ext4|ext3|ext2)
            e2fsck -f -y "$2" >> "$LOG_FILE" 2>&1
            _ck=$?
            # 0=נקי, 1=תוקן. 4 ומעלה = נשארו שגיאות. בלי הבדיקה הזו
            # קוד היציאה נזרק ורק resize2fs נספר — משפחת #346 על מסלול
            # הרסני (#444). חסר אינו 0.
            case "$_ck" in
                ''|*[!0-9]*) log "$2: e2fsck לא החזיר קוד יציאה"; return 1 ;;
            esac
            if [ "$_ck" -ge 4 ]; then
                log "$2: e2fsck נכשל (rc=$_ck)"
                return 1
            fi
            resize2fs "$2" >> "$LOG_FILE" 2>&1
            _rc=$?
            [ "$_rc" = "0" ] && return 0
            log "$2: resize2fs נכשל (rc=${_rc:-לא נרשם})"
            return 1 ;;
        btrfs)
            _m="$RUN_DIR/grow"
            mkdir -p "$_m"
            mount -t btrfs "$2" "$_m" >> "$LOG_FILE" 2>&1 || return 1
            btrfs filesystem resize max "$_m" >> "$LOG_FILE" 2>&1
            _rc=$?
            umount "$_m" 2>/dev/null
            return $_rc ;;
        *)
            # המועמד כבר גדל ב-GPT. "אין כלי" אינו הצלחה (#444).
            log "אין כלי הרחבה ל-$1 -- המחיצה גדלה, מערכת הקבצים נשארה בגודל המקורי"
            return 1 ;;
    esac
}

finish_grow() {
    # $1 = disk. שלב שני של ההרחבה: מערכת הקבצים אמורה לעקוב אחרי
    # המחיצה שכבר גדלה. כישלון כאן אינו "האימג' עדיין שמיש" — המחיצה
    # גדולה והפורמט לא, ו-done שולח לחפש אימג' קטן (#444).
    if grow_expanded "$1"; then
        target_set "$1" "done"
        return 0
    fi
    _mark="$RUN_DIR/targets/$1/expanded"
    _idx=$(cut -d'|' -f1 "$_mark" 2>/dev/null)
    _fs=$(cut -d'|' -f2 "$_mark" 2>/dev/null)
    target_set "$1" "failed" \
        "partition ${_idx:-?}: ההרחבה של מערכת הקבצים (${_fs:-?}) נכשלה"
    return 1
}
