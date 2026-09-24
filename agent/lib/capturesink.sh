# capturesink.sh -- where capture_disk's results go: each partition to the
# server while it is still being read (capture_sink), the manifest last
# (upload_manifest), and a refusal or a failure to the target's error field
# (_capture_failed). POSIX sh (busybox ash).
# פוצל מ-capture.sh ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות; שם נשאר
# capture_disk, שקורא את הדיסק. שני הקבצים נטענים יחד ב-imagectl-agent.

_capture_failed() {
    # $1 = disk, $2 = הסיבה. היומן לבדו נעלם (‏tmpfs, ו-ui_clear מוחק את המסך),
    # ולכן הסיבה נכתבת גם לשדה `error` של היעד — אותו מסלול ככשל מחיצה (#106).
    log "capture failed on $1: $2"
    # ‏#87: המקור חוזר לגודלו קודם. החזרה שנכשלה מצטרפת לסיבה — היומן
    # לבדו נעלם, והמפעיל חייב לדעת אם דיסק הבנייה נשאר מכווץ.
    _cf_why="$2"
    shrink_restore_source "$1" || _cf_why="$2 | $(cat "$RUN_DIR/targets/$1/error")"
    target_set "$1" "failed" "$_cf_why"
    echo "failed" > "$RUN_DIR/state"
}

capture_sink() {
    # $1 = fifo, $2 = task id, $3 = file. Consumes one compressed partition.
    # ‏CAPTURE_SINK=discard (#715): הדיסק נקרא ומגובב בדיוק כמו בקליטה —
    # אותם שערים, אותו מניפסט — אבל אף בייט לא יוצא מהמכונה; מחשב הבנייה
    # משדר אותם בעצמו בקריאה השנייה (directsend.sh).
    [ "${CAPTURE_SINK:-upload}" = discard ] && { cat "$1" > /dev/null; return; }
    # ‏-T ולא --data-binary: ‏--data-binary קורא את כל ה-FIFO לזיכרון כדי
    # לחשב Content-Length — מחיצה גדולה מה-RAM נהרגת ב-OOM (‏#15). ‏-T
    # מזרים ב-chunked. ‏--max-time 0 = בלי תקרת משך (100GB לוקחים זמן),
    # אבל עם תקרת חוסר-התקדמות: חיבור שנפל באמצע יוצא, לא נתלה.
    curl -sfS --max-time 0 --speed-limit 1 --speed-time "$HTTP_STALL_TIMEOUT" \
        -H "Content-Type: application/octet-stream" \
        -H "X-Imagectl-Task-Token: ${TASK_TOKEN:-}" \
        -T "$1" "$SERVER/api/v1/capture/$2/files/$3"
}

upload_manifest() {
    # $1 = task id, $2 = manifest path.
    curl -sfS -X PUT -H "Content-Type: application/json" \
        -H "X-Imagectl-Task-Token: ${TASK_TOKEN:-}" \
        --data-binary "@$2" \
        "$SERVER/api/v1/capture/$1/manifest" >> "$LOG_FILE" 2>&1
}
