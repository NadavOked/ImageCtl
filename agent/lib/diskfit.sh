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
            "cannot tell whether the image fits: needs=${_n:-unknown} disk=${_h:-unknown}"
        return 1
    fi
    if [ "$_h" -lt "$_n" ]; then    # #1171: the gap itself in MiB, before the two 12-digit numbers
        target_set "$1" "failed" "disk too small by $(( (_n - _h + 1048575) / 1048576 )) MiB\
 (needs $_n bytes, has $_h; $((_n - _h)) bytes short)"
        return 1
    fi
}
