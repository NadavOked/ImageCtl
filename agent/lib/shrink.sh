# shrink.sh -- ‏#87: כיווץ מחיצות ה-NTFS של דיסק המקור לפני הקליטה,
# והחזרתן לגודלן אחריה ("resizable image" של FOG). POSIX sh (busybox ash).
#
# דיסק בנייה של 512GB עם 100GB בשימוש מייצר אימג' של 512GB, ובדיקה 2.7
# (‏disk_fits) חוסמת אותו בצדק על כל כונן 256. הרצפה של מחיצה היא הגודל
# שבו נקלטה -- partclone מסרב לשחזר לתוך מחיצה קטנה ממנה -- ולכן הדרך
# היחידה היא לכווץ את המחיצה **על המקור** לפני שהזרם מתחיל:
# ‏ntfsresize --info (קריאה בלבד) → גודל מינימלי + מרווח → אישור של אדם
# ליד המכונה → ntfsresize -s → כניסת GPT קטנה יותר → הקרנל קורא את הטבלה
# → ntfsfix -d. השחזור (#648, ‏expand_last/grow_expanded) מותח את המחיצה
# האחרונה בחזרה לכונן היעד בלי שינוי.
#
# ‏#929 (הכרעת נדב 17/09): **כל** מחיצות ה-NTFS-data מכווצות, כל אחת
# **במקומה** ובסדר start_sector -- ‏C: ואז D: -- ומוחזרות בסדר הפוך. אף
# מחיצה אינה מוזזת על המקור: רק הגודל בכניסה שלה משתנה. המדידה, היעד
# והשאלה (אחת לכולן) יושבים ב-shrinkplan.sh; כאן רק מה שכותב.
#
# ‏**המקור אינו יעד שיכפול.** ‏grow.sh מריץ ntfsfix עם `-b -d` על דיסק חדש שקיבל
# עותק; כאן `-b` היה מוחק את רשימת הקלאסטרים הפגומים ($BadClus) של דיסק
# הבנייה -- ו-ntfsresize, שמסרב בעצמו לדיסק עם סקטורים פגומים (יוצא 1 בלי
# ‏-b משלו), היה מעביר נתונים לתוכם. לכן ntfsfix כאן רק `-d`, ורק **אחרי**
# ‏resize -- שהשער של #651 כבר הוכיח שהווליום נקי לפניו. ובאותה רוח: הכיווץ
# רץ **בלי `-f`** -- ווליום מלוכלך מסורב על ידי ntfsresize בקול ("Volume is
# scheduled for check"), ועם "y" מפורש ל-stdin: השאלה "proceed?" שלו רואה
# ‏EOF כ**המשך** (proceed_question: fgets שנכשל אינו עוצר), ולא כביטול.
#
# המניפסט מתאר פריסה **מצומצמת**: כל מחיצה שיושבת אחרי מחיצה מכווצת
# (‏D: אחרי C:, ‏recovery של Windows 11) נרשמת עם תחילה שזזה אחורה בסכום
# ההפרשים שלפניה, ו-`source_start_sector` שומר את מקומה האמיתי -- ו-apply_gpt
# בונה את הטבלה הזו על היעד ו-expand_last דוחף את הזנב לסופו. ההפרשים הם
# כפולות של MiB ולכן היישור (2048) נשמר.
#
# מה שאסור: כיווץ NTFS מהובר/מלוכלך (השער של #651 רץ לפני הקורא הזה),
# ושידור ישיר (‏CAPTURE_SINK=discard, ‏#715) -- הקריאה השנייה שם משווה sha256
# לראשונה, ומחיצה שהוחזרה לגודלה ביניהן אינה אותם בייטים.
#
# כל צעד נבדק בקוד יציאה ובקריאה חוזרת (עיקרון 5): "לא הצלחנו לכווץ"
# הוא כשל גלוי של הקליטה, לא "כווצנו". ‏SHRINK_ERROR נושא את הסיבה אל
# ‏_capture_failed, כמו PLAN_ERROR ב-expand.sh.

#: אחרי הקליטה המקור חוזר לגודלו (1) -- מחשב הבנייה נשאר כפי שהיה.
#: ‏0 = משאירים אותו מכווץ. כשל בהחזרה הוא אזהרה, לא כשל קליטה: ווינדוס
#: עולה גם ממחיצה קטנה מהדיסק, והאימג' כבר בספרייה.
CAPTURE_SHRINK_RESTORE="${CAPTURE_SHRINK_RESTORE:-1}"
SHRINK_ERROR=""

_shrink_gpt() {
    # $1 disk $2 idx $3 start $4 size_sectors $5 type $6 uguid $7 name $8 attrs.
    # הכניסה נכתבת מחדש (מחיקה+יצירה באותו אינדקס) עם ה-GUID הייחודי, השם
    # והדגלים -- ה-BCD מאתר את מחיצת המערכת לפי זוג ה-GUIDים (#26). ‏`-a 1`
    # לפני `-n`: ‏CreatePartition של sgdisk מיישר תחילה שאינה כפולה של 2048
    # בלי לצעוק (Align), ו-NTFS שאינו בתחילת המחיצה שלו הוא ווינדוס שלא
    # עולה -- כניסה שכבר הייתה כאן נבנית **בדיוק** במקומה. ואז הקרנל מתבקש
    # לקרוא את הטבלה, והתחילה והגודל נקראים בחזרה **מהדיסק** (#51): ‏sgdisk
    # שלא צעק אינו טבלה שהשתנתה. 1/2/3 = איזה צעד נכשל.
    _sg_u=""; [ -n "$6" ] && _sg_u="-u $2:$6"
    _sg_a=""; [ -n "$8" ] && _sg_a="-A $2:=:0x$8"
    # shellcheck disable=SC2086 # הפיצול של $_sg_u/$_sg_a מכוון
    sgdisk -a 1 -d "$2" -n "$2:$3:$(($3 + $4 - 1))" -t "$2:$5" $_sg_u -c "$2:$7" $_sg_a \
        "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 1
    blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 2
    _sg_got=$(sgdisk -i "$2" "$DEVROOT/$1" 2>/dev/null \
              | awk -F': ' '/^First sector/ { split($2, f, " ") } /^Partition size/ { split($2, z, " ") }
                            END { print f[1] "|" z[1] }')
    [ "$_sg_got" = "$3|$4" ] || return 3
}

_shrink_entry() {
    # $1 disk $2 idx. מדפיס "name|attrs" מ-sgdisk -i. הדגלים נכתבים מחדש עם
    # הכניסה; קריאה שלא הניבה 16 ספרות הקס אינה "אין דגלים" -- היא סירוב
    # (1, SHRINK_ERROR), לפני שנכתב בייט (עיקרון 5). נקרא עם `> קובץ` ולא
    # ב-`$( )`, כדי ש-SHRINK_ERROR ייכתב במעטפת הזו.
    _se_info=$(sgdisk -i "$2" "$DEVROOT/$1" 2>> "$LOG_FILE")
    _se_name=$(printf '%s\n' "$_se_info" | sed -n "s/^Partition name: '\(.*\)'.*/\1/p")
    _se_attr=$(printf '%s\n' "$_se_info" | awk -F': ' '/^Attribute flags/ { print $2 }' | awk '{print $1}')
    case "$_se_attr" in ????????????????) printf '%s|%s\n' "$_se_name" "$_se_attr" ;; *)
        SHRINK_ERROR="לא הצלחנו לקרוא את כניסת מחיצה $2 בטבלה (sgdisk -i: דגלים '$_se_attr') — הדיסק לא שונה"
        return 1 ;;
    esac
}

shrink_before_capture() {
    # $1 disk $2 parts file $3 sector size. ‏0 = אין מה לכווץ, או כווץ
    # ו-$2 כבר מתאר את הפריסה המצומצמת; ‏1 = הקליטה נעצרת, SHRINK_ERROR אומר למה.
    SHRINK_ERROR=""
    rm -f "$RUN_DIR/targets/$1/shrunk" "$RUN_DIR/parts.orig.txt"
    [ "${CAPTURE_SINK:-upload}" = discard ] && return 0
    shrink_plan "$1" "$2" "$3" || return 1
    _sb_plan="$RUN_DIR/shrink.plan"; [ -s "$_sb_plan" ] || return 0
    # ‏#926: לפי זיכרון השרת (ה-hello שהביא את המשימה) הדיסק כבר מכווץ מקליטה
    # קודמת שלא הוחזרה -- לא מכווצים פעמיים, ולא שואלים סתם.
    shrink_records_refresh || :
    if _sb_pend=$(shrink_pending_for "$1"); then
        SHRINK_ERROR="לפי זיכרון השרת מחיצה $(printf '%s\n' "$_sb_pend" | awk -F'|' '{ s = s (s ? ", " : "") $3 } END { print s }') בדיסק הזה כבר כווצה לקליטה קודמת (רשומה #${_sb_pend%%|*}) ולא הוחזרה לגודלה — אתחלו את מחשב הבנייה ובחרו החזרה, או נקו את הרשומה בקונסולה; הקליטה בוטלה"
        return 1
    fi
    _sb_q=$(shrink_question "$_sb_plan")
    shrink_gui_release
    # ‏#906: hello ממשיך בזמן ההמתנה לאדם, והשאלה מגיעה לקונסולה. שאלה אחת לכולן.
    _sb_dec=$(attended "$_sb_q" shrink_ask "$_sb_plan")
    case "$_sb_dec" in
        continue) ;;
        plain) log "partitions captured at their full size by the operator's choice"; return 0 ;;
        *) SHRINK_ERROR="הכיווץ לא אושר ליד המכונה ($_sb_q) — הקליטה בוטלה"; return 1 ;;
    esac
    # הכניסות (שם, דגלים) של **כולן** נקראות לפני הכתיבה הראשונה: קריאה
    # שנכשלת במחיצה השנייה לא תמצא את הראשונה כבר מכווצת. שורת ה-stage היא
    # בדיוק שורת הסימון של ההחזרה: idx|start|size|newsec|type|uguid|name|attrs.
    _sb_stage="$RUN_DIR/shrink.stage"; : > "$_sb_stage"
    while IFS='|' read -r _sb_idx _sb_guid _sb_uguid _sb_start _sb_size _sb_cur _sb_new <&3; do
        [ -n "$_sb_idx" ] || continue
        _shrink_entry "$1" "$_sb_idx" > "$RUN_DIR/shrink.entry" || return 1
        printf '%s|%s|%s|%s|%s|%s|%s\n' "$_sb_idx" "$_sb_start" "$_sb_size" $((_sb_new / $3)) "$_sb_guid" "$_sb_uguid" \
            "$(cat "$RUN_DIR/shrink.entry")" >> "$_sb_stage"
    done 3< "$_sb_plan"
    # ‏#926: הפריסה המקורית של **כל** המחיצות נרשמת **בשרת** -- רשומה אחת
    # לדיסק -- לפני הכתיבה הראשונה; tmpfs מת עם אובדן חשמל. בלי רשומה אין כיווץ.
    shrink_open_record "$1" "$_sb_stage" "$_sb_plan" || { SHRINK_ERROR="$SHRINKMEM_ERROR"; return 1; }
    mkdir -p "$RUN_DIR/targets/$1"
    while IFS='|' read -r _sb_idx _sb_start _sb_size _sb_newsec _sb_guid _sb_uguid _sb_name _sb_attr <&3; do
        [ -n "$_sb_idx" ] || continue
        _sb_node=$(partition_node "$1" "$_sb_idx"); _sb_new=$((_sb_newsec * $3))
        log "shrinking partition $_sb_idx on $1: $((_sb_size * $3)) -> $_sb_new bytes"
        # בלי -f ועם "y" מפורש -- ראו למעלה. ‏$? של הצינור הוא של ntfsresize.
        printf 'y\n' | ntfsresize -s "$_sb_new" --no-progress-bar "$_sb_node" >> "$LOG_FILE" 2>&1; _sb_rc=$?
        [ "$_sb_rc" -eq 0 ] || {
            SHRINK_ERROR="ntfsresize נכשל בכיווץ מחיצה $_sb_idx (rc=$_sb_rc): $(tail -n 1 "$LOG_FILE" 2>/dev/null | tr -d '\r') — מערכת הקבצים עלולה להיות במצב לא ידוע; הריצו chkdsk בווינדוס לפני קליטה חוזרת"
            return 1; }
        # הסימון נכתב לפני הטבלה: מכאן כל כשל מחזיר את המקור לגודלו (‏_capture_failed).
        printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$_sb_idx" "$_sb_start" "$_sb_size" "$_sb_newsec" "$_sb_guid" "$_sb_uguid" "$_sb_name" "$_sb_attr" \
            >> "$RUN_DIR/targets/$1/shrunk"
        _shrink_gpt "$1" "$_sb_idx" "$_sb_start" "$_sb_newsec" "$_sb_guid" "$_sb_uguid" "$_sb_name" "$_sb_attr"; _sb_rc=$?
        [ "$_sb_rc" -eq 0 ] || {
            SHRINK_ERROR="מערכת הקבצים במחיצה $_sb_idx כווצה ל-$_sb_new בייט, אך טבלת המחיצות לא אושרה (שלב $_sb_rc: 1=sgdisk 2=rereadpt 3=התחילה או הגודל שנקראו בחזרה שונים) — המקור הוחזר לגודלו, אלא אם מצורפת כאן סיבה נוספת"
            return 1; }
        # ‏ntfsresize מסמן dirty לבדיקה בווינדוס; ניקוי אחרון, אחרת partclone -I
        # מעתיק את הדגל לכל תחנה (‏chkdsk באתחול הראשון).
        ntfsfix -d "$_sb_node" >> "$LOG_FILE" 2>&1; _sb_rc=$?
        [ "$_sb_rc" -eq 0 ] || { SHRINK_ERROR="דגל ה-dirty לא נוקה אחרי הכיווץ של מחיצה $_sb_idx (ntfsfix -d rc=$_sb_rc)"; return 1; }
        log "partition $_sb_idx shrunk: $_sb_size -> $_sb_newsec sectors"
    done 3< "$_sb_stage"
    cp "$2" "$RUN_DIR/parts.orig.txt" || { SHRINK_ERROR="לא הצלחנו לשמור את הפריסה המקורית"; return 1; }
    _shrink_compact "$RUN_DIR/targets/$1/shrunk" "$RUN_DIR/parts.orig.txt" > "$2" \
        || { SHRINK_ERROR="לא הצלחנו לכתוב את הפריסה המצומצמת"; return 1; }
}

_shrink_compact() {
    # $1 = mark file (idx|start|size|newsec|...), $2 = original parts file.
    # הפריסה המצומצמת: כל מחיצה מכווצת בגודלה החדש, וכל מחיצה זזה אחורה
    # בסכום ההפרשים של המכווצות שיושבות **לפניה** -- כפולות של MiB, ולכן
    # היישור נשמר. ‏FILENAME ולא FNR==NR: קובץ סימון ריק היה מבלבל את השניים.
    awk -F'|' -v OFS='|' -v mark="$1" '
        FILENAME == mark { n[$1] = $4; st[$1] = $2; d[$1] = $3 - $4; next }
        { s = 0; for (i in st) if (st[i] + 0 < $4 + 0) s += d[i]
          if ($1 in n) $5 = n[$1]
          $4 = sprintf("%.0f", $4 - s); print }' "$1" "$2"
}

shrink_json_extra() {
    # $1 idx $2 start $3 size_sectors $4 sector size. השדות שמבדילים פריסה
    # מכווצת מזו שעל דיסק המקור -- או כלום: שדה חסר = כמו על המקור.
    [ -f "$RUN_DIR/parts.orig.txt" ] || return 0
    awk -F'|' -v i="$1" -v s="$2" -v z="$3" -v ss="$4" '$1 == i {
        if ($5 + 0 != z + 0) printf ",\"shrunk_from_bytes\":%.0f", $5 * ss
        if ($4 + 0 != s + 0) printf ",\"source_start_sector\":%s", $4 }' "$RUN_DIR/parts.orig.txt"
}

_shrink_restore_one() {
    # $1 disk, $2..$9 = שורת סימון (idx start old new type uguid name attrs).
    # הטבלה, ואז מערכת הקבצים. ‏-f -f: מותח גם ווליום שנשאר מלוכלך (ntfsfix -d
    # אחרי הכיווץ נכשל), בלי שאלה. אין ntfsfix לפני -- המקור אינו יעד שיכפול.
    # ‏0 = חזרה; ‏1 ו-_sr1_why אומר איזו מחיצה ומה נפל.
    _sr1_node=$(partition_node "$1" "$2"); _sr1_why=""
    log "restoring partition $2 on $1 to $4 sectors"
    if _shrink_gpt "$1" "$2" "$3" "$4" "$6" "$7" "$8" "$9"; then
        if ntfsresize -f -f --no-progress-bar "$_sr1_node" >> "$LOG_FILE" 2>&1; then
            ntfsfix -d "$_sr1_node" >> "$LOG_FILE" 2>&1 || _sr1_why="dirty flag not cleared after the grow (ntfsfix -d)"
        else _sr1_why="ntfsresize could not grow the filesystem back"; fi
    else _sr1_why="the partition table was not restored (step $?)"; fi
    [ -n "$_sr1_why" ] || { log "partition $2 restored to $4 sectors"; return 0; }
    _sr1_why="מחיצה $2 נשארה $5 סקטורים במקום $4: $_sr1_why"
    return 1
}

shrink_restore_source() {
    # $1 = disk. הצד השני: כל המחיצות חוזרות לגודלן, בסדר הפוך לכיווץ --
    # ‏D: ואז C:. נקרא פעם אחת -- מסיום הקליטה או מ-_capture_failed -- והסימון
    # נמחק. ‏0 גם כשאין מה להחזיר. כשל = אזהרה בשדה error, לא כשל קליטה;
    # והרשומה בשרת (#926) נסגרת רק כש**כולן** חזרו -- מחיצה אחת שנשארה
    # מכווצת היא דיסק מכווץ.
    _sr_mark="$RUN_DIR/targets/$1/shrunk"
    [ -f "$_sr_mark" ] || return 0
    awk '{ a[NR] = $0 } END { for (i = NR; i >= 1; i--) print a[i] }' "$_sr_mark" > "$RUN_DIR/shrink.undo"
    rm -f "$_sr_mark"
    [ "$CAPTURE_SHRINK_RESTORE" = 1 ] || { log "$1 left shrunk (CAPTURE_SHRINK_RESTORE=$CAPTURE_SHRINK_RESTORE)"; return 0; }
    _sr_bad=""
    while IFS='|' read -r _sr_i _sr_s _sr_o _sr_n _sr_t _sr_u _sr_nm _sr_a <&3; do
        [ -n "$_sr_i" ] || continue
        _shrink_restore_one "$1" "$_sr_i" "$_sr_s" "$_sr_o" "$_sr_n" "$_sr_t" "$_sr_u" "$_sr_nm" "$_sr_a" \
            || _sr_bad="$_sr_bad${_sr_bad:+; }$_sr1_why"
    done 3< "$RUN_DIR/shrink.undo"
    [ -n "$_sr_bad" ] || { shrink_close_record "$1" || :; return 0; }   # #926
    _sr_msg="המקור לא הוחזר לגודלו אחרי הקליטה: $_sr_bad — ווינדוס יעלה"
    log "WARNING: $1: $_sr_msg"
    echo "$_sr_msg" > "$RUN_DIR/targets/$1/error"
    shrink_note_record "$1" "$_sr_msg"   # #926: הרשומה נשארת פתוחה, והקונסולה רואה למה
    return 1
}
