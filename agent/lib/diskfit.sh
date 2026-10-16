# diskfit.sh -- check 2.7: does the image fit this disk. Asked by name and
# before the partition table is touched (#12). POSIX sh (busybox ash).
# ‏required_bytes הוא התאום של server/images.py:required_bytes (#82). פוצל
# מ-restore.sh ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות; הקוראים הם
# run_restore (restore.sh) ו-run_restore_drawers (drawers.sh).

required_bytes() {
    # $1 = manifest file. כמה בייטים הכונן חייב להחזיק כדי שהפריסה
    # תיכתב עליו: סוף המחיצה האחרונה, ועוד מגה-בייט לעותק הגיבוי של
    # ה-GPT וליישור. **לא** ‏min_target_bytes שבמניפסט — בכל אימג'
    # שנקלט לפני #82 הוא גודל דיסק המקור, וזה חסם כל אימג' שנבנה
    # במכונה וירטואלית מלהיכנס לכונן פיזי מאותה מחלקה.
    #
    # אותו כלל בדיוק בשרת (server/images.py:required_bytes), ויש בדיקה
    # שמריצה את שניהם על אותם מניפסטים ומשווה מספר למספר. מניפסט בלי
    # גיאומטריה מלאה נופל אחורה לשדה המוצהר, שהוא השמרני מהשניים; בלי
    # שניהם הפלט ריק, והקורא נכשל במקום לנחש.
    jq -r '
      (.sector_size // 512) as $ss
      | [ .partitions[]
          | select((.start_sector != null) and (.size_bytes != null))
          | .start_sector * $ss + .size_bytes ] as $ends
      | if ($ends | length) == 0 or ($ends | length) != (.partitions | length)
        then (.min_target_bytes // empty)
        else ($ends | max) as $end
             | ($end + 262144) as $need         # ‏#1171: GPT backup, no MiB round-up
             | if (.source_disk_bytes != null) and (.source_disk_bytes >= $end)
                  and (.source_disk_bytes < $need)
               then .source_disk_bytes else $need end
        end' "$1" 2>/dev/null
}

disk_fits() {
    # $1 = disk name, $2 = manifest file. בדיקה 2.7: כונן קטן מדי נתפס
    # **בשמו ולפני שנגענו בו**, ולא כ"כשל טבלת מחיצות" עמום אחרי
    # ש-apply_gpt כבר הריץ --zap-all (מעבדה, #12). שני המספרים נכנסים
    # להודעה, כי "קטן מדי" בלי כמה וכמה אינו אבחנה.
    _n=$(required_bytes "$2")
    # בלי `|| echo 0`: ‏blockdev שנכשל אינו כונן בגודל אפס. שניהם היו
    # נחסמים, אבל ההודעה הייתה אומרת "הכונן קטן מדי, יש בו 0 בייט" על
    # כונן שלא הצלחנו למדוד — וזה בדיוק קיפול שני מצבים לאחד. ה-stderr
    # ליומן ולא ל-/dev/null, כי הסיבה היא מה שמבדיל ביניהם.
    _h=$(blockdev --getsize64 "$DEVROOT/$1" 2>> "$LOG_FILE")
    # שני הערכים חייבים להיות מספרים לפני שמשווים אותם. קודם לכן ההשוואה
    # הייתה `[ -n "$_need" ] && [ ... ] 2>/dev/null` — כלומר מניפסט שלא
    # ידענו לקרוא ממנו את הדרישה **דילג על הבדיקה כולה** והמשיך לכתוב.
    # זה בדיוק עיקרון 5: "לא הצלחנו לבדוק" נראה כמו "בדקנו, הכל תקין".
    case "$_n" in ''|*[!0-9]*) _n="" ;; esac
    case "$_h" in ''|*[!0-9]*) _h="" ;; esac
    if [ -z "$_n" ] || [ -z "$_h" ]; then
        target_set "$1" "failed" \
            "לא ניתן לדעת אם האימג' נכנס: נדרש ${_n:-לא ידוע} בייט, בדיסק ${_h:-לא ידוע}"
        return 1
    fi
    if [ "$_h" -lt "$_n" ]; then    # #1171: the gap itself in MiB, before the two 12-digit numbers
        target_set "$1" "failed" "הדיסק קטן מדי ב-$(( (_n - _h + 1048575) / 1048576 )) MiB\
 (נדרשים $_n בייט, בדיסק $_h; חסרים $((_n - _h)) בייט)"
        return 1
    fi
    # #1354: נכנס = גם נשאר מקום לעבוד. אותו יעד, אותו רגע, לפני שנגענו בו.
    _mf=$(min_free_check "$2" "$_h" "$_n")
    [ "$_mf" = ok ] || { target_set "$1" "failed" "${_mf#*|}"; return 1; }
}

# --- #1354: מינימום מקום פנוי במחיצת המערכת אחרי השחזור ----------------------
# הערך הוא הגדרה בקונסולה (min_free_gb, server/minfree.py) ומגיע בכל תשובת
# hello. ברירת המחדל כאן היא התאום של MIN_FREE_GB_DEFAULT שם -- יש טסט
# שמשווה. ערך שלא הגיע או אינו תקין = ברירת המחדל, **לא 0** (עיקרון 5):
# 0 הוא "הכלל כבוי", והוא נבחר רק במפורש בקונסולה.
MIN_FREE_GB_DEFAULT=20
MIN_FREE_GB_MAX=500

min_free_take() {
    # $1 = hello answer. The last value the server sent, kept for this boot --
    # the local clone (no server) uses it. Missing (an older server) = no file.
    _mt=$(json_get "$1" ".min_free_gb")
    case "$_mt" in null|'') return 0 ;; esac
    if ! _min_free_valid "$_mt"; then
        log "min_free_gb from the server is not 0-$MIN_FREE_GB_MAX ($_mt) -- the default ${MIN_FREE_GB_DEFAULT}GB" >&2
        rm -f "$RUN_DIR/min_free_gb"; return 0
    fi
    echo "$_mt" > "$RUN_DIR/min_free_gb"
}

_min_free_valid() {
    # 1-3 digits, no leading zero (ash reads 020 as octal), at most the max.
    case "$1" in 0) return 0 ;; ''|0*|*[!0-9]*|????*) return 1 ;; esac
    [ "$1" -le "$MIN_FREE_GB_MAX" ]
}

min_free_gb() {
    _mg=$(cat "$RUN_DIR/min_free_gb" 2>/dev/null)
    _min_free_valid "$_mg" && { echo "$_mg"; return 0; }
    log "no min_free_gb from the server -- using the default ${MIN_FREE_GB_DEFAULT}GB" >&2
    echo "$MIN_FREE_GB_DEFAULT"
}

system_free_after() {
    # $1 manifest, $2 disk bytes, $3 required bytes, $4 the index expand_last
    # stretches ("" = none). stdout: free|role -- the bytes left free in the
    # system partition after the restore; unknown|role when its used_bytes was
    # not measured; none| when there is no windows/linux partition at all.
    # The system partition is the FIRST windows partition on the platter, else
    # the first linux one (hostname.sh's choice, interfaces.md section 1). It
    # grows only when it is the one expand_last stretches, and only past
    # expand_last's own 1 GiB threshold. Twin: server/minfree.py:free_after.
    jq -r --argjson d "$2" --argjson n "$3" --arg c "$4" '
      def first_of($r): [.partitions[] | select(.role == $r and (.start_sector | type) == "number")]
                        | sort_by(.start_sector) | .[0];
      (first_of("windows") // first_of("linux")) as $s
      | if $s == null then "none|"
        elif ($s.used_bytes | type) != "number" or $s.used_bytes < 0
             or ($s.size_bytes | type) != "number" then "unknown|" + $s.role
        else (($d - $n) as $g | if ($s.index | tostring) == $c and $g > 1073741824 then $g else 0 end) as $grow
             | "\($s.size_bytes - $s.used_bytes + $grow | floor)|" + $s.role end' "$1" 2>> "$LOG_FILE"
}

min_free_check() {
    # $1 manifest, $2 disk bytes, $3 required bytes. stdout: ok |
    # short|<words> | unchecked|<words>. "Not measured" is not "fits"
    # (principle 5), and the words carry the number. The partition that grows
    # is expand_last's own choice (_expand_candidate, EXPAND_OVERRIDE included).
    _mr_gb=$(min_free_gb); [ "$_mr_gb" -gt 0 ] || { echo ok; return 0; }
    _mr=$(system_free_after "$1" "$2" "$3" "$(_expand_candidate "$1" | cut -d'|' -f1)")
    _mr_free=${_mr%%|*}; _mr_role=${_mr#*|}
    _mr_where="ב-C:"; [ "$_mr_role" = linux ] && _mr_where="במחיצת הלינוקס הראשית"
    case "$_mr_free" in
        none) echo ok; return 0 ;;
        ''|unknown|*[!0-9]*)
            echo "unchecked|לא נבדק אם יישארו ${_mr_gb}GB פנויים $_mr_where — לא נמדד כמה תפוס בה (used_bytes חסר)"
            return 0 ;;
    esac
    if [ "$_mr_free" -lt $((_mr_gb * 1073741824)) ]; then
        echo "short|לא נכנס — יישארו רק $(awk -v b="$_mr_free" 'BEGIN { printf "%.1f", b / 1073741824 }') GB פנויים $_mr_where, המינימום ${_mr_gb}GB"
    else
        echo ok
    fi
}
