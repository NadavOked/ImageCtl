# restore.sh -- consumes the stream exactly as interfaces.md section 7
# defines it: GPT from the manifest, one transmission per partition file,
# receiver-side decompression, sha256 on the compressed bytes.
# POSIX sh (busybox ash).
#
# טבלת המחיצות כולה יושבת ב-expand.sh — ‏apply_gpt, ההרחבה והראיה
# שהטבלה הגיעה לדיסק (verify_table); שני הקבצים נטענים יחד. כאן נשאר
# מה שצורך את הזרם עצמו. התקרות של כל ההמתנות כאן יושבות ב-waits.sh.

UDPCAST_PORTBASE="${UDPCAST_PORTBASE:-9000}"

# ‏udp-receiver ממתין לשדר לנצח כברירת מחדל, וזה בדיוק "תחנה שקפאה בלי
# סיבה" שנראה במעבדה. שני הדגלים האלה הם התקרה של הכלי עצמו — הדרך
# היחידה לגרום ל-udp-receiver לצאת מרצונו במקום שנהרוג אותו מבחוץ.
# הערכים נגזרים מ-waits.sh כדי שיישארו מקום אחד; ההשמה כאן היא רק
# נפילה אחורה לקובץ שנטען לבדו (בדיקות).
UDPCAST_START_TIMEOUT="${UDPCAST_START_TIMEOUT:-${WAIT_STREAM_START_S:-600}}"
UDPCAST_STALL_TIMEOUT="${UDPCAST_STALL_TIMEOUT:-${WAIT_STREAM_STALL_S:-120}}"

manifest_plan() {
    # $1 = manifest file. One line per partition:
    # index|type_guid|role|fs|start_sector|size_bytes|file|sha256|expandable|unique_guid|uuid
    # ‏unique_guid ו-uuid אחרונים בכוונה — מניפסט ישן בלעדיהם עדיין נקרא.
    # ל-read חייב להיות משתנה לכל שדה: האחרון בולע את הזנב עם הקווים.
    #
    # ‏jq פולט שורות תוך כדי ואז נופל על האלמנט הראשון שאינו ניתן
    # לרינדור, וב-pipeline של POSIX sh (אין pipefail) קוד היציאה הזה
    # בלתי נראה: תוכנית **חלקית** נראית בדיוק כמו תוכנית שלמה, והלולאה
    # שנגמרת בשלום אחריה מכריזה done על מחיצות שאיש לא כתב (#51). לכן
    # התוכנית מחומרנת לקובץ, ה-rc נבדק, ומספר השורות מושווה למה שהמניפסט
    # מצהיר עליו — ורק אז היא נקראת. גם tmpfs שנגמר נתפס כאן.
    _pf="$RUN_DIR/plan.txt"
    jq -r '.partitions[] | [.index, .type_guid, .role, .fs, .start_sector, .size_bytes,
        .file, .sha256, .expandable, (.unique_guid // ""), (.uuid // "")] | join("|")' \
        "$1" > "$_pf" 2>> "$LOG_FILE" || { log "plan: jq failed on $1"; return 1; }
    _pw=$(jq -r '.partitions | length' "$1" 2>> "$LOG_FILE")
    case "$_pw" in ''|*[!0-9]*) log "plan: no partition count in $1"; return 1 ;; esac
    _ph=$(awk 'END { print NR }' "$_pf")
    if [ "$_ph" -ne "$_pw" ]; then
        log "plan: $_ph of $_pw partitions rendered from $1"
        return 1
    fi
    cat "$_pf"
}

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
             | ((($end + 2097151) / 1048576) | floor) * 1048576 as $need
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
    if [ "$_h" -lt "$_n" ]; then
        target_set "$1" "failed" \
            "disk too small: image needs $_n bytes, disk has $_h"
        return 1
    fi
}

partclone_for_fs() {
    case "$1" in
        ntfs)                 echo "partclone.ntfs" ;;
        vfat|fat|fat32|fat16) echo "partclone.fat" ;;
        ext4|ext3|ext2)       echo "partclone.ext4" ;;
        btrfs)                echo "partclone.btrfs" ;;
        *)                    echo "partclone.dd" ;;
    esac
}

partclone_mode() {
    # $1=הכלי $2=הדגל המבוקש (-c לקליטה, -r לשחזור). partclone.dd לא
    # מכיר אף אחד מהם — הוא תמיד מעתיק בייט-בייט, לשני הכיוונים.
    # קריאה עם הדגל נכשלת מיד (מחיצת bios-grub, מעבדת ה-VM, issue #12).
    if [ "$1" = "partclone.dd" ]; then echo ""; else echo "$2"; fi
}

partition_node() {
    # sda 3 -> /dev/sda3, nvme0n1 3 -> /dev/nvme0n1p3.
    case "$1" in
        *[0-9]) echo "$DEVROOT/${1}p$2" ;;
        *)      echo "$DEVROOT/$1$2" ;;
    esac
}

node_is_block() {
    # $1 = נתיב לצומת מחיצה. פונקציה נפרדת ולא `[ -b ]` ישיר רק כדי
    # שהבדיקות יוכלו להחליף אותה: אין דרך ליצור התקן בלוקים בלי root,
    # ובדיקה שמדולגת בחצי מהסביבות היא ירוק בלי ראיה (הלקח מ-#52).
    [ -b "$1" ]
}

stream_source() {
    # $1 = mode (multicast/unicast), $2 = server URL, $3 = image id, $4 = file.
    if [ "$1" = "multicast" ]; then
        udp-receiver --nokbd --portbase "$UDPCAST_PORTBASE" \
            --start-timeout "$UDPCAST_START_TIMEOUT" \
            --receive-timeout "$UDPCAST_STALL_TIMEOUT" 2>> "$LOG_FILE"
    else
        http_get_stream "$2/api/v1/images/$3/files/$4"
    fi
}

is_swap_partition() {     # $1 = fs, $2 = file. בלי קובץ = אין מה לחכות לו בזרם.
    [ "$1" = "swap" ] || [ "$2" = "null" ] || [ -z "$2" ]
}

make_swap() {
    # $1 = node, $2 = ה-UUID מהמניפסט. ‏swap אינה משודרת (אפיון סעיף 14) אלא
    # נבראת כאן — ה-mkswap היחיד בסוכן, כתיבה אחת על המיקום הסופי (‏swap נגררת
    # כבר הועברה לזנב, #46). ‏-U כי fstab של דביאן מפנה ל-UUID=, ובלעדיו כל
    # שחזור ממציא UUID חדש וה-swap לא עולה (#48); מניפסט ישן בלי השדה נופל
    # אחורה ל-mkswap רגיל עם UUID אקראי, בדיוק כמו קודם (עיקרון 1).
    _u=""
    [ -n "$2" ] && [ "$2" != "null" ] && _u="-U $2"
    # shellcheck disable=SC2086 # הפיצול של $_u מכוון
    mkswap $_u "$1" >> "$LOG_FILE" 2>&1
}

is_sha256() {
    # ‏64 ספרות hex ותו לא: "ה-sha לא חושב" הוא מצב נפרד מ"לא תאם" (#73) —
    # קובץ ריק שמושווה למניפסט מייצר האשמה ספציפית על בדיקה שלא נעשתה.
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" -eq 64 ]
}

restore_partition() {
    # $1=mode $2=server $3=image_id $4=disk $5=index $6=fs $7=file $8=sha256 $9=uuid
    # Pipeline: source | pv (byte counter) | tee (sha fork) | zstd -d | partclone
    _tdir="$RUN_DIR/targets/$4"
    _node=$(partition_node "$4" "$5")
    _pcl=$(partclone_for_fs "$6")

    # יעד שאינו התקן בלוקים הוא קובץ רגיל ב-devtmpfs: ‏partclone היה כותב
    # אליו את המחיצה כולה, יוצא 0, וה-sha256 היה עובר — כי הוא נלקח על
    # הבייטים שהתקבלו. ‏apply_gpt כבר אימת את כל הצמתים; זו הבדיקה
    # האחרונה, מיד לפני הכתיבה עצמה (#51).
    node_is_block "$_node" \
        || { log "partition $5: $_node is not a block device"; return 1; }

    if is_swap_partition "$6" "$7"; then
        make_swap "$_node" "$9" || { log "partition $5: mkswap failed"; return 1; }
        target_partition_done "$4"
        return 0
    fi

    rm -f "$_tdir/shafifo" "$_tdir/sha.out" "$_tdir/pipe.rc"
    mkfifo "$_tdir/shafifo"
    sha256sum < "$_tdir/shafifo" > "$_tdir/sha.out" &
    _shapid=$!

    # הצינור רץ ברקע כדי שתהיה עין שמסתכלת עליו. מונה ה-pv הוא ההוכחה
    # היחידה שמשהו זז, ו-wait_progress מודד בדיוק אותו: כונן איטי מעכב
    # ומותר לו, זרם שנעצר חייב להיגמר בדיווח ולא בקפיאה.
    # shellcheck disable=SC2046 # דגלי partclone_mode הם רשימת מילים
    (
        stream_source "$1" "$2" "$3" "$7" \
            | pv -n -b -i 2 2>> "$_tdir/bytes.raw" \
            | tee "$_tdir/shafifo" \
            | zstd -dc 2>> "$LOG_FILE" \
            | "$_pcl" $(partclone_mode "$_pcl" -r) -s - -O "$_node" \
                -L "$_tdir/partclone.log" 2>> "$LOG_FILE"
        echo "$?" > "$_tdir/pipe.rc"
    ) &
    _pipepid=$!

    if wait_progress "$_pipepid" "$_tdir/bytes.raw" \
            "$WAIT_STREAM_START_S" "$WAIT_STREAM_STALL_S" \
            "מחיצה $5 ($7) על $4"; then
        _rc=$(cat "$_tdir/pipe.rc" 2>/dev/null || echo 1)
    else
        _rc="$WAIT_TIMED_OUT"
    fi
    wait_pid "$_shapid" "$WAIT_HELPER_S" "חישוב ה-sha256 של מחיצה $5"
    rm -f "$_tdir/shafifo"

    if [ "$_rc" -ne 0 ]; then
        log "partition $5 ($7): write failed (rc=$_rc)"
        return 1
    fi

    # Verification is on the compressed bytes as received; in multicast there
    # is no asking again. ‏sha256sum שנהרג יחד עם הצינור (או שפג זמנו למעלה)
    # משאיר קובץ ריק, וההשוואה הישירה הפכה היעדר ראיה לטענה על בייטים (#73).
    _got=$(awk '{print $1}' "$_tdir/sha.out" 2>/dev/null)
    if ! is_sha256 "$_got"; then
        log "partition $5 ($7): ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"
        return 1
    elif [ "$_got" != "$8" ]; then
        log "partition $5 ($7): sha256 mismatch"
        return 1
    fi
    target_partition_done "$4"
}

run_restore() {
    # $1 = mode (multicast/unicast), $2 = disk name, $3 = server URL,
    # $4 = image id, $5 = manifest file.
    echo "writing" > "$RUN_DIR/state"
    target_set "$2" "writing"

    # דיסק קטן מדי נתפס כאן, בשמו — לא כ"כשל טבלת מחיצות" עמום אחרי
    # שכבר נגענו בדיסק (מעבדה, #12: שחזור 40GB אל כונן 20GB).
    if ! disk_fits "$2" "$5"; then
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    # הטבלה נקבעת עד תומה לפני שהנתונים מגיעים — כולל ההרחבה, כי היא זו
    # שמעבירה swap נגררת לזנב, וה-mkswap של הלולאה חייב לנחות על המקום
    # הסופי. הרחבה שנכשלה באמצע חוזרת לטבלת המניפסט ומשחזרת בגודל המקורי:
    # עוד לא נכתב בייט, ולכן הנסיגה הזו שלמה.
    _table=0
    if apply_gpt "$2" "$5"; then
        if expand_last "$2" "$5"; then
            _table=1
        else
            log "WARNING: expansion failed -- restoring at the image's own size"
            apply_gpt "$2" "$5" && _table=1
        fi
    fi
    if [ "$_table" -ne 1 ]; then
        target_set "$2" "failed" "could not write the partition table"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    # התוכנית מחומרנת פעם אחת ונקראת מקובץ, ולא מצינור: בצינור הלולאה רצה
    # בתת-מעטפת, והמונה שמכריע בסוף היה נמחק איתה. ‏fd 3 ולא stdin, כדי
    # שאף כלי בתוך הלולאה לא יבלע את התוכנית עצמה.
    _plan="$RUN_DIR/restore.plan"
    if ! manifest_plan "$5" > "$_plan"; then
        target_set "$2" "failed" "could not read the partition plan from the manifest"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi
    _expected=$(awk 'END { print NR }' "$_plan")
    _written=0
    while IFS='|' read -r _idx _guid _role _fs _start _size _f _sha _exp _uguid _uuid <&3; do
        log "partition $_idx ($_role, $_fs): receiving $_f"
        restore_partition "$1" "$3" "$4" "$2" "$_idx" "$_fs" "$_f" "$_sha" "$_uuid" \
            || break
        _written=$((_written + 1))
    done 3< "$_plan"

    # ‏done נגזר ממספר, לא מהיעדר כישלון: לולאה שנגמרה בשלום על תוכנית
    # קצרה מדי היא בדיוק "הצלחה בלי ראיה", וכך יצא לכיתה מחשב עם טבלת
    # מחיצות וכלום (#51).
    if [ "$_expected" -lt 1 ] || [ "$_written" -ne "$_expected" ]; then
        target_set "$2" "failed" "wrote $_written of $_expected partitions"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    echo "verifying" > "$RUN_DIR/state"
    grow_expanded "$2" || log "WARNING: expansion failed, image still usable"

    target_set "$2" "done"
    echo "done" > "$RUN_DIR/state"
}
