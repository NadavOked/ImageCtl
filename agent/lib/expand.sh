# expand.sh -- טבלת המחיצות: כתיבתה מהמניפסט, מתיחת המחיצה האחרונה
# לגודל הכונן, והראיה שהכול אכן הגיע לדיסק.
# POSIX sh (busybox ash).
#
# פוצל מ-restore.sh כשזה הגיע לגבול 300 השורות. החלוקה אינה שרירותית:
# כאן יושב כל מה שנוגע ב*טבלת המחיצות* לפני שהגיע בייט אחד, ושם נשאר
# מה שצורך את הזרם עצמו. שני השלבים של ההרחבה (‏expand_last לפני הזרם,
# ‏grow_expanded אחריו) חייבים להישאר צמודים זה לזה, והם כאן — ואיתם
# ‏apply_gpt, שכותב את אותה טבלה בדיוק ורץ שורה לפני ההרחבה.
#
# ומכאן גם הראיה: ‏verify_table הוא הצד השני של אותה מטבע — מי שכותב
# את הטבלה קורא אותה בחזרה מהדיסק לפני שמותר לזרם להתחיל. ‏#51 היה
# בדיוק החוסר הזה, ולכן הוא סוגר את הקובץ הזה משני קצותיו.

# כמה שניות מותר לקרנל ול-devtmpfs לבנות את /dev/sdaN אחרי כתיבת
# הטבלה. זו אינה "המתנה שאולי תעזור": הבדיקה נקראת שוב ושוב, מפסיקה
# ברגע שכל הצמתים קיימים, ובסוף התקרה נכשלת בגלוי.
TABLE_SETTLE_S="${TABLE_SETTLE_S:-10}"

# --- הראיה שהטבלה באמת על הדיסק ----------------------------------------------

verify_table() {
    # $1 = disk name, $2 = manifest file. הראיה החיובית שהטבלה הגיעה
    # לדיסק: כל אינדקס שבתוכנית נקרא בחזרה כצומת מחיצה, והמספר חייב
    # להשתוות. ‏sgdisk שיצא 0 בזמן שהקרנל לא בנה את /dev/sdaN הוא בדיוק
    # המצב שבו partclone פותח **קובץ רגיל** ב-devtmpfs, כותב את המחיצה
    # ל-RAM ויוצא 0 — וה-sha256 עובר, כי הוא נלקח על הבייטים שהתקבלו
    # ולא על מה שיושב על הדיסק (#51). "לא צעק" אינו "נכתב".
    _plan="$RUN_DIR/table.plan"
    manifest_plan "$2" > "$_plan" \
        || { log "$1: no partition plan to check the table against"; return 1; }
    _want=$(awk 'END { print NR }' "$_plan")
    [ "$_want" -gt 0 ] || { log "$1: the partition plan is empty"; return 1; }
    _spent=0
    while :; do
        _live=0
        _missing=""
        for _i in $(cut -d'|' -f1 "$_plan"); do
            if node_is_block "$(partition_node "$1" "$_i")"; then
                _live=$((_live + 1))
            else
                _missing="$_missing $_i"
            fi
        done
        [ "$_live" -eq "$_want" ] && break
        [ "$_spent" -ge "$TABLE_SETTLE_S" ] && break
        sleep 1
        _spent=$((_spent + 1))
    done
    if [ "$_live" -ne "$_want" ]; then
        log "$1: $_live of $_want partitions came back from the disk (missing:$_missing)"
        return 1
    fi
    log "$1: all $_want partitions are live block devices"
}

apply_gpt() {
    # $1 = disk name, $2 = manifest file.
    # The partition table never travels in the stream -- it is derived
    # from the manifest alone, then the GPT backup is pushed to the real
    # end of the disk (matters when restoring onto a bigger drive).
    _ss=$(json_get "$2" ".sector_size")
    [ "$_ss" = "512" ] || { log "unsupported sector size: $_ss"; return 1; }
    _scheme=$(json_get "$2" ".scheme")
    [ "$_scheme" = "gpt" ] || { log "unsupported scheme: $_scheme"; return 1; }

    sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 1
    manifest_plan "$2" | while IFS='|' read -r _idx _guid _role _fs _start _size _f _sha _exp _uguid _uuid; do
        _end=$((_start + _size / 512 - 1))
        # ה-GUID הייחודי משוחזר כשיש: ה-BCD של Windows מאתר את מחיצת
        # המערכת לפי זוג ה-GUIDים (דיסק+מחיצה) — טבלה עם GUIDים חדשים
        # נגמרת ב-winload.efi 0xc000000e על כל מחשב משוחזר (#26).
        _u=""
        [ -n "$_uguid" ] && _u="-u $_idx:$_uguid"
        # shellcheck disable=SC2086 # הפיצול של $_u מכוון
        sgdisk -n "$_idx:$_start:$_end" -t "$_idx:$_guid" $_u "$DEVROOT/$1" \
            >> "$LOG_FILE" 2>&1 || exit 1
    done || return 1
    _dguid=$(json_get "$2" ".disk_guid")
    if [ -n "$_dguid" ] && [ "$_dguid" != "null" ]; then
        sgdisk -U "$_dguid" "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
            || log "WARNING: could not set the disk GUID on $1"
    fi
    # מכאן ומטה כל שורה היא ראיה, ואף אחת מהן אינה מסתיימת ב-`|| true`:
    # ‏sgdisk -e הוא לב ההרחבה (בדיקה 2.5), ‏rereadpt הוא הסימן היחיד
    # שהקרנל קיבל את הטבלה ובנה את /dev/sdaN, ו-verify_table קורא את
    # שניהם בחזרה מהדיסק. עד כאן הפונקציה נגמרה ב-`sleep 1` ולכן החזירה
    # תמיד 0 — טבלה שנכשלה לגמרי הייתה נראית כמו טבלה שנכתבה (#51).
    sgdisk -e "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { log "$1: could not move the GPT backup to the end of the disk"; return 1; }
    blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { log "$1: the kernel refused to re-read the partition table"; return 1; }
    verify_table "$1" "$2"
}

expand_last() {
    # $1 = disk name, $2 = manifest file. Rewrites the table so the candidate
    # partition fills the disk. Runs whenever the manifest has a candidate and
    # the disk really is 1GiB larger -- and *before* the data arrives, so that
    # the mkswap of the restore loop lands on the swap's final place, once.
    # הסימון הוא לכל כונן בנפרד — בחדר השיכפולים מגירה שנכשלה לא גוררת
    # את השאר, ולכן גם השלב השני של ההרחבה נשאל לכל אחת לחוד.
    _mark="$RUN_DIR/targets/$1/expanded"
    mkdir -p "$RUN_DIR/targets/$1"
    rm -f "$_mark"
    # תוכנית חלקית כאן אינה "אין מועמד" אלא מועמד **שגוי**, ואיתו הזזה של
    # מחיצות שאיש לא ספר. כל הקוראים למטה מריצים את manifest_plan בתוך
    # החלפת פקודה או צינור, ושם קוד היציאה שלו אינו נראה — ולכן הוא נבדק
    # פעם אחת כאן, לפני שנגזרת ממנו החלטה.
    manifest_plan "$2" > /dev/null || return 1
    _line=$(_expand_candidate "$2")
    [ -n "$_line" ] || return 0
    _idx=$(echo "$_line" | cut -d'|' -f1)
    _guid=$(echo "$_line" | cut -d'|' -f2)
    _fs=$(echo "$_line" | cut -d'|' -f4)
    _start=$(echo "$_line" | cut -d'|' -f5)
    _size=$(echo "$_line" | cut -d'|' -f6)
    _uguid=$(echo "$_line" | cut -d'|' -f10)

    # כל מה שיושב אחרי המועמד חוסם את מתיחתו — ה-swap של מתקין דביאן
    # (‏#46) ומחיצת ה-recovery של Windows 11, שהיא הפריסה השכיחה ולא
    # מקרה קצה (#58). בשחזור הטבלה נכתבת מחדש מהמניפסט ממילא, ולכן הזנב
    # נפרס אחרת: המחיצות שאחרי המועמד עוברות לסוף הכונן **בסדר הפיזי
    # שלהן**, והמועמד נמתח עד תחילתן. ‏swap נבראת שם מחדש ב-mkswap (אפיון
    # סעיף 14), וכל השאר נכתבת שם מקובץ הזרם שלה — הזרם כותב לפי אינדקס,
    # והמחיצה כבר נבראה מחדש באותו אינדקס במקום החדש.
    # מי "אחרי" נקבע לפי start_sector ולא לפי סדר הרשימה: הרשימה בסדר
    # אינדקסים, ובאימג' ענן זה אינו הסדר על הדיסק.
    _tail=$(manifest_plan "$2" | awk -F'|' -v s="$_start" '$5 + 0 > s + 0')
    _tailsec=$(printf '%s\n' "$_tail" | awk -F'|' '{ n += int($6 / 512) } END { print n + 0 }')

    _disk_sectors=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null || echo 0)
    _table_end=$((_start + _size / 512))
    # הסף נמדד על מה שבאמת יתווסף למחיצה: הזנב, פחות **כל** מה שיחזור אליו.
    [ $((_disk_sectors - _table_end - _tailsec)) -gt 2097152 ] || return 0

    log "expanding partition $_idx to fill the disk"
    _move_to_tail "$1" "$_tail" || return 1
    # ההרחבה בונה את המחיצה מחדש — ה-GUID הייחודי חייב לשרוד גם אותה,
    # אחרת ה-BCD מאבד את מחיצת המערכת שהרגע שוחזרה (#26).
    _u=""
    [ -n "$_uguid" ] && _u="-u $_idx:$_uguid"
    # ‏0 בסוף = סוף השטח הפנוי הגדול ביותר, כלומר עד תחילת ה-swap החדשה.
    # shellcheck disable=SC2086 # הפיצול של $_u מכוון
    sgdisk -d "$_idx" -n "$_idx:$_start:0" -t "$_idx:$_guid" $_u "$DEVROOT/$1" \
        >> "$LOG_FILE" 2>&1 || return 1
    # ההרחבה בנתה טבלה **שונה** מזו של apply_gpt, ולכן היא נקראת בחזרה
    # שוב: מי שכתב לא מעיד על עצמו. ‏rereadpt שנכשל היה `|| true` עד כאן,
    # והוא הסימן היחיד שהקרנל בכלל קיבל את הטבלה החדשה (#51).
    blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { log "$1: the kernel refused to re-read the expanded table"; return 1; }
    verify_table "$1" "$2" || return 1
    # מערכת הקבצים גדלה רק כשיהיה בה מה להגדיל — grow_expanded, אחרי הזרם.
    echo "$_idx|$_fs" > "$_mark"
}

_expand_candidate() {
    # $1 = manifest file. The one plan line to stretch, or nothing at all.
    #
    # ההחלטה נגזרת מהמניפסט **בכל שחזור**, ולא נקראת מהסימון שבו: הסימון
    # נעשה בקליטה בלבד, ולכן בכל אימג' שנקלט לפני #58 כל המחיצות הן
    # ‏`expandable: false` — ו"אף אחד לא סימן" נראה בדיוק כמו "אל תרחיב".
    # שני מצבים שונים שנראים אותו דבר הם עיקרון 5, וזה מה שהיה משאיר
    # 244GB לא מוקצים בלי שורה אחת שמסבירה למה.
    #
    # מניפסט שמסמן **בדיוק** מחיצה אחת עדיין גובר — זו העקיפה הידנית,
    # וזו גם התאימות לאחור. יותר מאחת (מניפסט שאיש לא אימת) חוזר לבחירה
    # האוטומטית במקום להכריע בין השתיים.
    _marked=$(manifest_plan "$1" | awk -F'|' '$9 == "true"')
    if [ "$(printf '%s\n' "$_marked" | awk 'NF { n++ } END { print n + 0 }')" -eq 1 ]
    then
        printf '%s\n' "$_marked"
        return 0
    fi
    # אחרת: המחיצה האחרונה **פיזית** שתפקידה windows או linux. הסדר הוא
    # של start_sector ולא של הרשימה, שהיא בסדר אינדקסים (באימג' ענן
    # השורש הוא מחיצה 1 — ראשון ברשימה, אחרון על הפלטה). ‏`recovery`
    # לעולם אינה מועמדת: היא אינה windows/linux, ולנפח אותה היה משאיר
    # מחיצת שחזור ענקית ליד מערכת שלא גדלה.
    manifest_plan "$1" | awk -F'|' '
        $3 == "windows" || $3 == "linux" {
            if (best == "" || $5 + 0 > k) { k = $5 + 0; best = $0 }
        }
        END { if (best != "") print best }'
}

_move_to_tail() {
    # $1 = disk name, $2 = the plan lines that sit after the candidate, in
    # any order (may be empty). Deleted first, then recreated from the disk's
    # last one inwards, so that their order on the platter survives the move.
    # The size is given as a negative start -- "that many sectors before the
    # end of the free space" -- and the end as 0: an explicit sector would
    # bypass sgdisk's own 2048-sector alignment, and a misaligned partition
    # costs real speed on an SSD. The index is kept: it is what the stream
    # writes by, and what the swap's mkswap lands on.
    printf '%s\n' "$2" | while IFS='|' read -r _si _rest; do
        [ -n "$_si" ] || continue
        sgdisk -d "$_si" "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || exit 1
    done || return 1
    # הסדר נקבע כאן, לפי start_sector יורד — ולא ב-sort(1), שאינו בשימוש
    # בשום מקום אחר בסוכן. הרשימה שמגיעה לכאן היא בסדר אינדקסים, ובאימג'
    # ענן זה אינו הסדר על הדיסק; מיון שגוי היה מחליף שתי מחיצות בשקט.
    printf '%s\n' "$2" \
        | awk -F'|' '{ a[NR] = $0; k[NR] = $5 + 0 }
             END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++)
                       if (k[j] > k[i]) { t = k[i]; k[i] = k[j]; k[j] = t
                                          u = a[i]; a[i] = a[j]; a[j] = u }
                   for (i = 1; i <= NR; i++) print a[i] }' \
        | while IFS='|' read -r _si _sg _sr _sf _ss _sb _sfile _ssha _sexp _su _suuid; do
        [ -n "$_si" ] || continue
        # ה-GUID הייחודי שורד גם את ההזזה: ‏/etc/fstab עשוי להפנות ל-PARTUUID.
        _su_arg=""
        [ -n "$_su" ] && _su_arg="-u $_si:$_su"
        # shellcheck disable=SC2086 # הפיצול של $_su_arg מכוון
        sgdisk -n "$_si:-$((_sb / 512)):0" -t "$_si:$_sg" $_su_arg "$DEVROOT/$1" \
            >> "$LOG_FILE" 2>&1 || exit 1
    done || return 1
}

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
            ntfsresize --force --no-progress-bar "$2" >> "$LOG_FILE" 2>&1 ;;
        ext4|ext3|ext2)
            e2fsck -f -y "$2" >> "$LOG_FILE" 2>&1
            resize2fs "$2" >> "$LOG_FILE" 2>&1 ;;
        btrfs)
            _m="$RUN_DIR/grow"
            mkdir -p "$_m"
            mount -t btrfs "$2" "$_m" >> "$LOG_FILE" 2>&1 || return 1
            btrfs filesystem resize max "$_m" >> "$LOG_FILE" 2>&1
            _rc=$?
            umount "$_m" 2>/dev/null
            return $_rc ;;
        *)
            log "no resize tool for $1 -- partition grown, filesystem left as is"
            return 0 ;;
    esac
}
