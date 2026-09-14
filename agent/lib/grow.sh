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
    # tool (spec section 14) -- btrfs and xfs grow only while mounted.
    # Return the tool result; finish_grow converts failure into a warning.
    case "$1" in
        ntfs)
            # שיטת קרונזילה + ניקוי dirty בסגנון FOG (resetFlag): ה-clone
            # עשוי לשאת דגלי hibernation/dirty של הגלופה (fast-startup),
            # ו-ntfsresize מסרב לווליום מהובר גם עם --force (#648). ‏ntfs-3g
            # -o remove_hiberfile נכשל ב-"Operation not permitted" במעבדה —
            # עיגון FUSE ב-initramfs busybox אינו יציב. במקום לעגן, עובדים
            # על ההתקן הגולמי, בשלושה צעדים שהסדר ביניהם קריטי:
            #   1. ntfsfix -b -d — מתקן את ה-$LogFile, מנקה את דגל ה-dirty
            #      (-d) ואת רשימת ה-bad clusters (-b). בלי -d, ‏ntfsfix
            #      **מסמן** את הווליום dirty בכוונה, וזה chkdsk על כל תחנת
            #      כיתה באתחול הראשון.
            #   2. ntfsresize -f -f — מותח את מערכת הקבצים (double force).
            #   3. ntfsfix -d — האחרון על המחיצה: ‏ntfsresize עלול לסמן
            #      dirty מחדש, וניקוי הדגל חייב לבוא **אחרי** ה-resize.
            # ‏ntfsfix עשוי לא לנקות fast-startup עקשן לגמרי — ולכן
            # finish_grow הופך כישלון הרחבה ל-warning, לא לכשל (best-effort).
            if ntfsfix -b -d "$2" >> "$LOG_FILE" 2>&1; then
                :
            else
                _rc=$?
                log "WARNING: $2: ntfsfix -b -d failed (rc=$_rc); trying ntfsresize"
            fi
            if ntfsresize -f -f --no-progress-bar "$2" >> "$LOG_FILE" 2>&1; then
                # ניקוי דגל ה-dirty אחרון, כדי שהתחנה לא תעלה ל-chkdsk.
                if ntfsfix -d "$2" >> "$LOG_FILE" 2>&1; then
                    :
                else
                    _rc=$?
                    log "WARNING: $2: final ntfsfix -d failed (rc=$_rc); volume may boot into chkdsk"
                fi
                return 0
            else
                _rc=$?
                log "WARNING: $2: ntfsresize failed (rc=$_rc); grow deferred"
                return 1
            fi ;;
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
        xfs)
            # XFS גדל רק mounted: xfs_growfs על נקודת העגינה, לא על
            # ההתקן (בניגוד ל-ext). RHEL/Fedora/Rocky/Alma (#667).
            _m="$RUN_DIR/grow"
            mkdir -p "$_m"
            mount -t xfs "$2" "$_m" >> "$LOG_FILE" 2>&1 || return 1
            xfs_growfs "$_m" >> "$LOG_FILE" 2>&1
            _rc=$?
            umount "$_m" 2>/dev/null
            [ "$_rc" = "0" ] && return 0
            log "$2: xfs_growfs נכשל (rc=${_rc:-לא נרשם})"
            return 1 ;;
        *)
            # המועמד כבר גדל ב-GPT. "אין כלי" אינו הצלחה (#444).
            log "אין כלי הרחבה ל-$1 -- המחיצה גדלה, מערכת הקבצים נשארה בגודל המקורי"
            return 1 ;;
    esac
}

finish_grow() {
    # $1 = disk, אחרי שחזור מלא. ההרחבה היא best-effort (שיטת קרונזילה):
    # דיסק שקיבל תמונה מלאה הוא done — משכפלים בכל מקרה. כישלון הרחבה
    # אינו מפיל את המגירה; הוא נרשם כ-warning בשדה האבחון, וה-clone
    # נשאר done (#648). ‏grow_filesystem/grow_expanded שומרים על קודי
    # האבחון שלהם.
    if grow_expanded "$1"; then
        target_set "$1" "done"
        return 0
    fi
    _mark="$RUN_DIR/targets/$1/expanded"
    _idx=$(cut -d'|' -f1 "$_mark" 2>/dev/null)
    _fs=$(cut -d'|' -f2 "$_mark" 2>/dev/null)
    _grow_warning="done (grow deferred): partition ${_idx:-?} (${_fs:-?}); filesystem growth failed"
    log "WARNING: $1: $_grow_warning"
    target_set "$1" "done" "$_grow_warning"
    return 0
}
