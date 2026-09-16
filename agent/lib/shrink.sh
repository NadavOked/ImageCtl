# shrink.sh -- ‏#87: כיווץ מחיצת ה-NTFS של דיסק המקור לפני הקליטה,
# והחזרתה לגודלה אחריה ("resizable image" של FOG). POSIX sh (busybox ash).
#
# דיסק בנייה של 512GB עם 100GB בשימוש מייצר אימג' של 512GB, ובדיקה 2.7
# (‏disk_fits) חוסמת אותו בצדק על כל כונן 256. הרצפה של מחיצה היא הגודל
# שבו נקלטה — partclone מסרב לשחזר לתוך מחיצה קטנה ממנה — ולכן הדרך
# היחידה היא לכווץ את המחיצה **על המקור** לפני שהזרם מתחיל:
# ‏ntfsresize --info (קריאה בלבד) → גודל מינימלי + מרווח → אישור של אדם
# ליד המכונה → ntfsresize -s → כניסת GPT קטנה יותר → הקרנל קורא את הטבלה
# → ntfsfix -d. השחזור (#648, ‏expand_last/grow_expanded) מותח את המחיצה
# בחזרה לכונן היעד בלי שינוי.
#
# ‏**המקור אינו יעד שיכפול.** ‏grow.sh מריץ ntfsfix עם `-b -d` על דיסק חדש שקיבל
# עותק; כאן `-b` היה מוחק את רשימת הקלאסטרים הפגומים ($BadClus) של דיסק
# הבנייה — ו-ntfsresize, שמסרב בעצמו לדיסק עם סקטורים פגומים (יוצא 1 בלי
# ‏-b משלו), היה מעביר נתונים לתוכם. לכן ntfsfix כאן רק `-d`, ורק **אחרי**
# ‏resize — שהשער של #651 כבר הוכיח שהווליום נקי לפניו. ובאותה רוח: הכיווץ
# רץ **בלי `-f`** — ווליום מלוכלך מסורב על ידי ntfsresize בקול ("Volume is
# scheduled for check"), ועם "y" מפורש ל-stdin: השאלה "proceed?" שלו רואה
# ‏EOF כ**המשך** (proceed_question: fgets שנכשל אינו עוצר), ולא כביטול.
#
# מחיצה שיושבת **אחרי** ווינדוס (‏recovery של Windows 11, הפריסה השכיחה)
# אינה מוזזת על המקור: המניפסט מתאר פריסה **מצומצמת** — תחילתה זזה
# אחורה בדיוק בהפרש הכיווץ, ו-`source_start_sector` שומר את מקומה
# האמיתי — ו-apply_gpt בונה את הטבלה הזו על היעד ו-expand_last דוחף את
# הזנב לסופו. ההפרש הוא כפולה של MiB ולכן היישור (2048) נשמר.
#
# מה שאסור: כיווץ NTFS מהובר/מלוכלך (השער של #651 רץ לפני הקורא הזה),
# ושידור ישיר (‏CAPTURE_SINK=discard, ‏#715) — הקריאה השנייה שם משווה sha256
# לראשונה, ומחיצה שהוחזרה לגודלה ביניהן אינה אותם בייטים. ‏ext4 אינו
# מכווץ כאן (‏resize2fs -M הוא מסלול נפרד עם e2fsck) — NTFS בלבד, מתועד.
#
# כל צעד נבדק בקוד יציאה ובקריאה חוזרת (עיקרון 5): "לא הצלחנו לכווץ"
# הוא כשל גלוי של הקליטה, לא "כווצנו". ‏SHRINK_ERROR נושא את הסיבה אל
# ‏_capture_failed, כמו PLAN_ERROR ב-expand.sh.

#: אחרי הקליטה המקור חוזר לגודלו (1) — מחשב הבנייה נשאר כפי שהיה.
#: ‏0 = משאירים אותו מכווץ. כשל בהחזרה הוא אזהרה, לא כשל קליטה: ווינדוס
#: עולה גם ממחיצה קטנה מהדיסק, והאימג' כבר בספרייה.
CAPTURE_SHRINK_RESTORE="${CAPTURE_SHRINK_RESTORE:-1}"
SHRINK_ERROR=""

_shrink_gb() { awk -v b="$1" 'BEGIN { printf "%.1f GB", b / 1e9 }'; }

_shrink_candidate() {
    # $1 = parts file (idx|type|uguid|start|size_sectors). אותו כלל כמו
    # ‏_mark_expandable: מחיצת windows/linux האחרונה **על הדיסק**, לפי
    # ‏start_sector. ריק = אין מועמדת.
    _cd_best=""; _cd_top=-1
    while IFS='|' read -r _cd_idx _cd_guid _cd_uguid _cd_first _cd_size; do
        [ -n "$_cd_idx" ] || continue
        case "$(_partition_role "$_cd_guid")" in windows|linux) ;; *) continue ;; esac
        [ "$_cd_first" -gt "$_cd_top" ] || continue
        _cd_top=$_cd_first; _cd_best="$_cd_idx|$_cd_guid|$_cd_uguid|$_cd_first|$_cd_size"
    done < "$1"
    [ -n "$_cd_best" ] && printf '%s\n' "$_cd_best"
}

shrink_not_last_hint() {
    # $1 = parts file, $2 = sector size. ‏#929: הסירוב על הרצפה (capture.sh)
    # אומר גם **למה** הכיווץ לא עזר — כשמחיצת windows/linux גדולה מהמועמדת
    # יושבת לפניה (‏C: ואחריה D:), הכיווץ נגע רק ב-D:. הגדלים הם של המקור
    # (parts.orig.txt כשכווץ) — מה שהמפעיל רואה ב-Windows. ריק = אין מה להוסיף.
    _nl_src="$1"; [ -f "$RUN_DIR/parts.orig.txt" ] && _nl_src="$RUN_DIR/parts.orig.txt"
    _nl_c=$(_shrink_candidate "$_nl_src"); [ -n "$_nl_c" ] || return 0
    _nl_cidx=${_nl_c%%|*}; _nl_csize=${_nl_c##*|}
    while IFS='|' read -r _nl_idx _nl_guid _nl_uguid _nl_first _nl_size; do
        [ -n "$_nl_idx" ] && [ "$_nl_idx" != "$_nl_cidx" ] || continue
        case "$(_partition_role "$_nl_guid")" in windows|linux) ;; *) continue ;; esac
        [ "$_nl_size" -gt "$_nl_csize" ] || continue
        printf ' — מחיצה %s (%s) אינה האחרונה על הדיסק, והכיווץ פועל רק על המחיצה האחרונה (מחיצה %s, %s): מחקו/מזגו את המחיצה שאחריה ב-Windows וקלטו מחדש'             "$_nl_idx" "$(_shrink_gb $((_nl_size * $2)))" "$_nl_cidx" "$(_shrink_gb $((_nl_csize * $2)))"
        return 0
    done < "$_nl_src"
}

_shrink_target() {
    # $1 = המינימום של ntfsresize, $2 = הגודל הנוכחי. היעד: מינימום ועוד
    # מרווח (10% או 2GiB, הגדול מביניהם), מעוגל למעלה ל-MiB — וההפרש מהנוכחי
    # מעוגל **למטה** ל-MiB, כדי שהזנב שיזוז במניפסט יישאר מיושר. הפרש קטן
    # ממגה = אין מה לכווץ: מודפס הגודל הנוכחי.
    awk -v m="$1" -v c="$2" 'BEGIN {
        margin = m * 0.10; if (margin < 2147483648) margin = 2147483648
        t = int((m + margin + 1048575) / 1048576) * 1048576
        d = int((c - t) / 1048576) * 1048576; if (d < 1048576) d = 0
        printf "%.0f\n", c - d }'
}

_shrink_gpt() {
    # $1 disk $2 idx $3 start $4 size_sectors $5 type $6 uguid $7 name $8 attrs.
    # הכניסה נכתבת מחדש (מחיקה+יצירה באותו אינדקס) עם ה-GUID הייחודי, השם
    # והדגלים — ה-BCD מאתר את מחיצת המערכת לפי זוג ה-GUIDים (#26). ‏`-a 1`
    # לפני `-n`: ‏CreatePartition של sgdisk מיישר תחילה שאינה כפולה של 2048
    # בלי לצעוק (Align), ו-NTFS שאינו בתחילת המחיצה שלו הוא ווינדוס שלא
    # עולה — כניסה שכבר הייתה כאן נבנית **בדיוק** במקומה. ואז הקרנל מתבקש
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

shrink_ask() {
    # $1 idx $2 current bytes $3 new bytes. הציור ל-stderr, ההכרעה בלבד
    # ל-stdout (הקורא לוכד ב-`$( )`): continue / plain / cancel. בלי תשובה
    # (EOF, מסך שנעלם) — cancel: שום דבר לא מכווץ בלי אדם שאמר כן.
    while :; do
        {
            ui_clear; ui_header
            echo "  The capture will shrink the Windows partition on THIS computer:"
            echo "    partition $1: $(_shrink_gb "$2") -> $(_shrink_gb "$3")"
            echo "  It is grown back when the capture ends. Windows boots either way."
            echo
            echo "    [1] Shrink and capture"
            echo "    [2] Capture without shrinking (the image will not fit smaller disks)"
            echo "    [3] Cancel the capture"
            echo
            printf "  Choose [1-3]: "
        } >&2
        read -r _sa_c || { echo cancel; return; }
        case "$_sa_c" in 1) echo continue; return ;; 2) echo plain; return ;; 3) echo cancel; return ;; esac
    done
}

shrink_gui_release() {
    # המסך הגרפי (‏imagectl-kiosk) תופס את התצוגה ואת המקלדת (EVIOCGRAB):
    # שאלה על מסך הטקסט מאחוריו לא נראית ולא נענית — והקליטה הייתה
    # ממתינה לנצח. הוא נעצר כמו ב-handoff (gui_cleanup מחזיר את הקונסולה),
    # והקליטה ממשיכה על מסך הטקסט כפי שעבדה לפני שהיה מסך גרפי. דיאלוג
    # גרפי הוא עבודת C בקיוסק — Issue המשך; בינתיים אין המתנה עיוורת.
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 0
    log "native GUI stopped for the shrink question -- the capture continues on the text screen"
    kill "$_gui_pid" 2>/dev/null
    wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI before the shrink question" || :
    _gui_pid=""; _gui_disabled=1
}

shrink_before_capture() {
    # $1 disk $2 parts file $3 sector size. ‏0 = אין מה לכווץ, או כווץ
    # ו-$2 כבר מתאר את הפריסה המצומצמת; ‏1 = הקליטה נעצרת, SHRINK_ERROR אומר למה.
    SHRINK_ERROR=""
    rm -f "$RUN_DIR/targets/$1/shrunk" "$RUN_DIR/parts.orig.txt"
    [ "${CAPTURE_SINK:-upload}" = discard ] && return 0
    _sb_line=$(_shrink_candidate "$2"); [ -n "$_sb_line" ] || return 0
    _sb_idx=$(echo "$_sb_line" | cut -d'|' -f1); _sb_guid=$(echo "$_sb_line" | cut -d'|' -f2)
    _sb_uguid=$(echo "$_sb_line" | cut -d'|' -f3); _sb_start=$(echo "$_sb_line" | cut -d'|' -f4)
    _sb_size=$(echo "$_sb_line" | cut -d'|' -f5)
    _sb_node=$(partition_node "$1" "$_sb_idx")
    case "$(_fs_of "$_sb_node")" in ntfs) ;; *) return 0 ;; esac
    _sb_cur=$((_sb_size * $3))
    # ‏--info קורא בלבד ומריץ 100% מהחישוב; "You might resize at N bytes".
    # בלי -f: ווליום מלוכלך או עם סקטורים פגומים נעצר כאן, בהודעה של הכלי.
    ntfsresize --info --no-progress-bar "$_sb_node" > "$RUN_DIR/ntfsresize.info" 2>&1
    _sb_min=$(sed -n 's/.*You might resize at \([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.info" | head -n 1)
    case "$_sb_min" in ''|*[!0-9]*)
        SHRINK_ERROR="לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה $_sb_idx (ntfsresize --info): $(tail -n 1 "$RUN_DIR/ntfsresize.info" 2>/dev/null)"
        return 1 ;;
    esac
    _sb_new=$(_shrink_target "$_sb_min" "$_sb_cur")
    [ "$_sb_new" -lt "$_sb_cur" ] || { log "partition $_sb_idx: $_sb_cur bytes, minimum $_sb_min -- nothing to shrink"; return 0; }
    _sb_q="הקליטה תכווץ את מחיצת ווינדוס במחשב הזה מ-$(_shrink_gb "$_sb_cur") ל-$(_shrink_gb "$_sb_new") — המשך/ביטול"
    shrink_gui_release
    # ‏#906: hello ממשיך בזמן ההמתנה לאדם, והשאלה מגיעה לקונסולה.
    _sb_dec=$(attended "$_sb_q" shrink_ask "$_sb_idx" "$_sb_cur" "$_sb_new")
    case "$_sb_dec" in
        continue) ;;
        plain) log "partition $_sb_idx: captured at its full size by the operator's choice"; return 0 ;;
        *) SHRINK_ERROR="הכיווץ לא אושר ליד המכונה ($_sb_q) — הקליטה בוטלה"; return 1 ;;
    esac
    _sb_info=$(sgdisk -i "$_sb_idx" "$DEVROOT/$1" 2>> "$LOG_FILE")
    _sb_name=$(printf '%s\n' "$_sb_info" | sed -n "s/^Partition name: '\(.*\)'.*/\1/p")
    _sb_attr=$(printf '%s\n' "$_sb_info" | awk -F': ' '/^Attribute flags/ { print $2 }' | awk '{print $1}')
    # הדגלים נכתבים מחדש עם הכניסה; קריאה שלא הניבה 16 ספרות הקס אינה
    # "אין דגלים" — היא סירוב, לפני שנכתב בייט (עיקרון 5).
    case "$_sb_attr" in ????????????????) ;; *)
        SHRINK_ERROR="לא הצלחנו לקרוא את כניסת מחיצה $_sb_idx בטבלה (sgdisk -i: דגלים '$_sb_attr') — הדיסק לא שונה"; return 1 ;;
    esac
    log "shrinking partition $_sb_idx on $1: $_sb_cur -> $_sb_new bytes (minimum $_sb_min)"
    # בלי -f ועם "y" מפורש — ראו למעלה: ווליום מלוכלך מסורב על ידי ntfsresize
    # עצמו, ו-EOF על השאלה שלו היה המשך. ‏$? של הצינור הוא של ntfsresize.
    printf 'y\n' | ntfsresize -s "$_sb_new" --no-progress-bar "$_sb_node" >> "$LOG_FILE" 2>&1; _sb_rc=$?
    [ "$_sb_rc" -eq 0 ] || {
        SHRINK_ERROR="ntfsresize נכשל בכיווץ מחיצה $_sb_idx (rc=$_sb_rc): $(tail -n 1 "$LOG_FILE" 2>/dev/null | tr -d '\r') — מערכת הקבצים עלולה להיות במצב לא ידוע; הריצו chkdsk בווינדוס לפני קליטה חוזרת"
        return 1; }
    _sb_newsec=$((_sb_new / $3))
    # הסימון נכתב לפני הטבלה: מכאן כל כשל מחזיר את המקור לגודלו (‏_capture_failed).
    mkdir -p "$RUN_DIR/targets/$1"
    printf '%s\n' "$_sb_idx|$_sb_start|$_sb_size|$_sb_newsec|$_sb_guid|$_sb_uguid|$_sb_name|$_sb_attr" > "$RUN_DIR/targets/$1/shrunk"
    _shrink_gpt "$1" "$_sb_idx" "$_sb_start" "$_sb_newsec" "$_sb_guid" "$_sb_uguid" "$_sb_name" "$_sb_attr"; _sb_rc=$?
    [ "$_sb_rc" -eq 0 ] || {
        SHRINK_ERROR="מערכת הקבצים במחיצה $_sb_idx כווצה ל-$_sb_new בייט, אך טבלת המחיצות לא אושרה (שלב $_sb_rc: 1=sgdisk 2=rereadpt 3=התחילה או הגודל שנקראו בחזרה שונים) — המקור הוחזר לגודלו, אלא אם מצורפת כאן סיבה נוספת"
        return 1; }
    # ‏ntfsresize מסמן dirty לבדיקה בווינדוס; ניקוי אחרון, אחרת partclone -I
    # מעתיק את הדגל לכל תחנה (‏chkdsk באתחול הראשון).
    ntfsfix -d "$_sb_node" >> "$LOG_FILE" 2>&1; _sb_rc=$?
    [ "$_sb_rc" -eq 0 ] || { SHRINK_ERROR="דגל ה-dirty לא נוקה אחרי הכיווץ של מחיצה $_sb_idx (ntfsfix -d rc=$_sb_rc)"; return 1; }
    cp "$2" "$RUN_DIR/parts.orig.txt" || { SHRINK_ERROR="לא הצלחנו לשמור את הפריסה המקורית"; return 1; }
    # הפריסה המצומצמת: המועמדת בגודלה החדש, וכל מה שאחריה זז אחורה בהפרש.
    awk -F'|' -v OFS='|' -v i="$_sb_idx" -v s="$_sb_start" -v n="$_sb_newsec" -v d="$((_sb_size - _sb_newsec))" '
        $1 == i { $5 = n } $4 + 0 > s + 0 { $4 = sprintf("%.0f", $4 - d) } { print }' \
        "$RUN_DIR/parts.orig.txt" > "$2" || { SHRINK_ERROR="לא הצלחנו לכתוב את הפריסה המצומצמת"; return 1; }
    log "partition $_sb_idx shrunk: $_sb_size -> $_sb_newsec sectors"
}

shrink_json_extra() {
    # $1 idx $2 start $3 size_sectors $4 sector size. השדות שמבדילים פריסה
    # מכווצת מזו שעל דיסק המקור — או כלום: שדה חסר = כמו על המקור.
    [ -f "$RUN_DIR/parts.orig.txt" ] || return 0
    awk -F'|' -v i="$1" -v s="$2" -v z="$3" -v ss="$4" '$1 == i {
        if ($5 + 0 != z + 0) printf ",\"shrunk_from_bytes\":%.0f", $5 * ss
        if ($4 + 0 != s + 0) printf ",\"source_start_sector\":%s", $4 }' "$RUN_DIR/parts.orig.txt"
}

shrink_restore_source() {
    # $1 = disk. הצד השני: המחיצה חוזרת לגודלה המקורי (טבלה, ואז מערכת
    # הקבצים). נקרא פעם אחת — מסיום הקליטה או מ-_capture_failed — והסימון
    # נמחק. ‏0 גם כשאין מה להחזיר. כשל = אזהרה בשדה error, לא כשל קליטה.
    _sr_mark="$RUN_DIR/targets/$1/shrunk"
    [ -f "$_sr_mark" ] || return 0
    IFS='|' read -r _sr_idx _sr_start _sr_old _sr_new _sr_type _sr_uguid _sr_name _sr_attr < "$_sr_mark"
    rm -f "$_sr_mark"
    [ "$CAPTURE_SHRINK_RESTORE" = 1 ] || { log "partition $_sr_idx left shrunk (CAPTURE_SHRINK_RESTORE=$CAPTURE_SHRINK_RESTORE)"; return 0; }
    _sr_node=$(partition_node "$1" "$_sr_idx")
    log "restoring partition $_sr_idx on $1 to $_sr_old sectors"
    _sr_why=""
    # ‏-f -f: מותח גם ווליום שנשאר מלוכלך (ntfsfix -d אחרי הכיווץ נכשל), בלי
    # שאלה. אין ntfsfix לפני — ראו למעלה, המקור אינו יעד שיכפול.
    if _shrink_gpt "$1" "$_sr_idx" "$_sr_start" "$_sr_old" "$_sr_type" "$_sr_uguid" "$_sr_name" "$_sr_attr"; then
        if ntfsresize -f -f --no-progress-bar "$_sr_node" >> "$LOG_FILE" 2>&1; then
            ntfsfix -d "$_sr_node" >> "$LOG_FILE" 2>&1 || _sr_why="dirty flag not cleared after the grow (ntfsfix -d)"
        else _sr_why="ntfsresize could not grow the filesystem back"; fi
    else _sr_why="the partition table was not restored (step $?)"; fi
    [ -n "$_sr_why" ] || { log "partition $_sr_idx restored to $_sr_old sectors"; return 0; }
    _sr_msg="המקור לא הוחזר לגודלו אחרי הקליטה: $_sr_why — ווינדוס יעלה; מחיצה $_sr_idx נשארה $_sr_new סקטורים במקום $_sr_old"
    log "WARNING: $1: $_sr_msg"
    echo "$_sr_msg" > "$RUN_DIR/targets/$1/error"
    return 1
}
