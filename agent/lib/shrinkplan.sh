# shrinkplan.sh -- ‏#929: מה מכווצים, בכמה, ומה שואלים -- קריאה בלבד.
# POSIX sh (busybox ash).
#
# פוצל מ-shrink.sh כשהכיווץ הורחב מהמחיצה האחרונה לכל מחיצות ה-NTFS
# (‏#929, הכרעת נדב 17/09). כאן יושב כל מה ש**אינו כותב לדיסק**: המועמדות,
# המדידה (ntfsresize --info), היעד, השאלה למפעיל והרמז בסירוב. shrink.sh
# נשאר הנתיב ההרסני -- ntfsresize -s, הטבלה, וההחזרה.
#
# המועמדות הן **כל** מחיצות ה-windows/linux על הדיסק, לפי start_sector עולה,
# ולא רק האחרונה: דיסק בנייה עם D: אחרי C: מכווץ את שתיהן, כל אחת
# **במקומה** -- ntfsresize מקטין מהסוף, והמחיצה נשארת בתחילתה -- בלי להזיז
# נתונים על דיסק הבנייה. המניפסט והיעד מקבלים את הפריסה הצפופה
# (‏source_start_sector זוכר את המקום האמיתי), ו-expand_last מותח על היעד
# רק את האחרונה.
#
# מחיצה שאי-אפשר למדוד (‏--info נכשל: מלוכלכת, מהוברת, לא עקבית) מסרבת את
# **כל** הקליטה, בשם -- לא "מכווצים חלקית" (עיקרון 5). מחיצה שאין מה לכווץ
# בה, או שאינה NTFS (‏ext4 הוא מסלול נפרד עם e2fsck, לא כאן), פשוט אינה
# בתוכנית; כשהאימג' עדיין גדול מהיעד, ‏shrink_unshrinkable_hint אומר זאת.

_shrink_gb() { awk -v b="$1" 'BEGIN { printf "%.1f GB", b / 1e9 }'; }

_shrink_candidates() {
    # $1 = parts file (idx|type|uguid|start|size_sectors). כל מחיצת
    # windows/linux, לפי start_sector עולה -- הסדר שבו הן מכווצות ושבו
    # ההפרשים מצטברים אחורה. המיון ב-awk כמו ב-_move_to_tail; ריק = אין.
    while IFS='|' read -r _cd_idx _cd_guid _cd_rest; do
        [ -n "$_cd_idx" ] || continue
        case "$(_partition_role "$_cd_guid")" in
            windows|linux) printf '%s|%s|%s\n' "$_cd_idx" "$_cd_guid" "$_cd_rest" ;;
        esac
    done < "$1" | awk -F'|' '{ a[NR] = $0; k[NR] = $4 + 0 }
        END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++)
                  if (k[j] < k[i]) { t = k[i]; k[i] = k[j]; k[j] = t; u = a[i]; a[i] = a[j]; a[j] = u }
              for (i = 1; i <= NR; i++) print a[i] }'
}

_shrink_target() {
    # $1 = המינימום של ntfsresize, $2 = הגודל הנוכחי. היעד: מינימום ועוד
    # מרווח (10% או 2GiB, הגדול מביניהם), מעוגל למעלה ל-MiB -- וההפרש מהנוכחי
    # מעוגל **למטה** ל-MiB, כדי שהזנב שיזוז במניפסט יישאר מיושר. הפרש קטן
    # ממגה = אין מה לכווץ: מודפס הגודל הנוכחי.
    awk -v m="$1" -v c="$2" 'BEGIN {
        margin = m * 0.10; if (margin < 2147483648) margin = 2147483648
        t = int((m + margin + 1048575) / 1048576) * 1048576
        d = int((c - t) / 1048576) * 1048576; if (d < 1048576) d = 0
        printf "%.0f\n", c - d }'
}

shrink_plan() {
    # $1 disk $2 parts file $3 sector size. כותב $RUN_DIR/shrink.plan -- שורה
    # לכל מחיצה שתכווץ: idx|type|uguid|start|size_sectors|cur_bytes|new_bytes,
    # לפי start_sector. ‏0 = התוכנית נכתבה (גם ריקה = אין מה לכווץ); ‏1 =
    # מחיצת NTFS שלא ניתן למדוד, SHRINK_ERROR אומר איזו. ‏--info קורא בלבד
    # ומריץ 100% מהחישוב ("You might resize at N bytes"); בלי -f: ווליום
    # מלוכלך או עם סקטורים פגומים נעצר כאן, בהודעה של הכלי.
    _sp="$RUN_DIR/shrink.plan"; : > "$_sp"
    _shrink_candidates "$2" > "$RUN_DIR/shrink.cand"
    while IFS='|' read -r _sp_idx _sp_guid _sp_uguid _sp_start _sp_size <&3; do
        [ -n "$_sp_idx" ] || continue
        _sp_node=$(partition_node "$1" "$_sp_idx")
        case "$(_fs_of "$_sp_node")" in ntfs) ;; *) log "partition $_sp_idx: not NTFS -- not shrunk here"; continue ;; esac
        _sp_cur=$((_sp_size * $3))
        ntfsresize --info --no-progress-bar "$_sp_node" > "$RUN_DIR/ntfsresize.info.$_sp_idx" 2>&1
        _sp_min=$(sed -n 's/.*You might resize at \([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.info.$_sp_idx" | head -n 1)
        case "$_sp_min" in ''|*[!0-9]*)
            SHRINK_ERROR="לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה $_sp_idx (ntfsresize --info): $(tail -n 1 "$RUN_DIR/ntfsresize.info.$_sp_idx" 2>/dev/null | tr -d '\r') — הריצו chkdsk בווינדוס וקלטו מחדש; הדיסק לא שונה"
            return 1 ;;
        esac
        _sp_new=$(_shrink_target "$_sp_min" "$_sp_cur")
        [ "$_sp_new" -lt "$_sp_cur" ] || { log "partition $_sp_idx: $_sp_cur bytes, minimum $_sp_min -- nothing to shrink"; continue; }
        printf '%s|%s|%s|%s|%s|%s|%s\n' "$_sp_idx" "$_sp_guid" "$_sp_uguid" "$_sp_start" "$_sp_size" "$_sp_cur" "$_sp_new" >> "$_sp"
    done 3< "$RUN_DIR/shrink.cand"
}

shrink_question() {
    # $1 = plan file. השאלה במילים -- לקונסולה (‏attended, ‏#906) ולשדה error
    # כשבוטלה: כל מחיצה עם המספרים שלה.
    _sq=""
    while IFS='|' read -r _sq_i _sq_g _sq_u _sq_s _sq_z _sq_cur _sq_new; do
        [ -n "$_sq_i" ] || continue
        _sq="$_sq${_sq:+, }מחיצה $_sq_i מ-$(_shrink_gb "$_sq_cur") ל-$(_shrink_gb "$_sq_new")"
    done < "$1"
    printf 'הקליטה תכווץ במחשב הזה: %s — המשך/ביטול' "$_sq"
}

shrink_ask() {
    # $1 = plan file. הציור ל-stderr, ההכרעה בלבד ל-stdout (הקורא לוכד
    # ב-`$( )`): continue / plain / cancel. שאלה **אחת** עם הטבלה של כל
    # המחיצות -- לא שאלה למחיצה. בלי תשובה (EOF, מסך שנעלם) -- cancel: שום
    # דבר לא מכווץ בלי אדם שאמר כן.
    while :; do
        {
            ui_clear; ui_header
            echo "  The capture will shrink these partitions on THIS computer:"
            while IFS='|' read -r _sa_i _sa_g _sa_u _sa_s _sa_z _sa_cur _sa_new; do
                [ -n "$_sa_i" ] && echo "    partition $_sa_i: $(_shrink_gb "$_sa_cur") -> $(_shrink_gb "$_sa_new")"
            done < "$1"
            echo "  They are grown back when the capture ends. Windows boots either way."
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
    # שאלה על מסך הטקסט מאחוריו לא נראית ולא נענית -- והקליטה הייתה
    # ממתינה לנצח. הוא נעצר כמו ב-handoff (gui_cleanup מחזיר את הקונסולה),
    # והקליטה ממשיכה על מסך הטקסט כפי שעבדה לפני שהיה מסך גרפי. דיאלוג
    # גרפי הוא עבודת C בקיוסק -- Issue המשך; בינתיים אין המתנה עיוורת.
    # ‏$1 = איזו שאלה (ליומן); ריק = שאלת הכיווץ. גם שאלת סוף הקליטה (#409).
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 0
    log "native GUI stopped for ${1:-the shrink question} -- the text screen takes over"
    kill "$_gui_pid" 2>/dev/null
    wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI before ${1:-the shrink question}" || :
    _gui_pid=""; _gui_disabled=1
}

shrink_unshrinkable_hint() {
    # $1 disk $2 parts file $3 sector size. ‏#929: הסירוב על הרצפה
    # (‏capture.sh) אומר גם **למה** הכיווץ לא הספיק -- מחיצת windows/linux
    # שאינה NTFS אינה מכווצת כאן, ומה לעשות. הגדלים של המקור (parts.orig.txt
    # כשכווץ) -- מה שהמפעיל רואה. ריק = אין מה להוסיף.
    _uh_src="$2"; [ -f "$RUN_DIR/parts.orig.txt" ] && _uh_src="$RUN_DIR/parts.orig.txt"
    _shrink_candidates "$_uh_src" | while IFS='|' read -r _uh_idx _uh_guid _uh_uguid _uh_first _uh_size; do
        _uh_fs=$(_fs_of "$(partition_node "$1" "$_uh_idx")")
        [ "$_uh_fs" = ntfs ] && continue
        printf ' — מחיצה %s (%s, %s) אינה NTFS והכיווץ בקליטה פועל על NTFS בלבד: כווצו אותה בעצמכם (resize2fs) או מחקו אותה, וקלטו מחדש' \
            "$_uh_idx" "$(_shrink_gb $((_uh_size * $3)))" "$_uh_fs"
        break
    done
}
