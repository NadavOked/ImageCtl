# shrinkmem.sh -- ‏#926: זיכרון הכיווץ בשרת. POSIX sh (busybox ash).
#
# ‏#87 מכווץ את מחיצות ה-NTFS של דיסק הבנייה לפני הקליטה ומחזיר אותן
# אחריה, והפריסה המקורית ישבה ב-`targets/<disk>/shrunk` — ‏tmpfs. אובדן
# חשמל או אתחול בין `ntfsresize -s` לבין ההחזרה השאיר את הדיסק מכווץ
# בלי שאיש יודע: ווינדוס עולה, ‏C: קטן, והסוכן כבר לא זוכר מה היה. אותה
# משפחה כמו #856 ו-#874, ואותו פתרון — **השרת זוכר**, לפי סידורי:
#
# 1. **לפני הכתיבה הראשונה למקור** (‏shrink_before_capture, לפני
#    `ntfsresize -s`) הסוכן שולח `shrink-open` — **רשומה אחת לדיסק** עם
#    `partitions`: הכניסה המלאה של **כל** מחיצה שתכווץ (‏#929), כל מה
#    ש-shrink_restore_source צריך. בלי 2xx עם `ok` — **הכיווץ לא מתחיל**
#    (עיקרון 5: בלי רשומה אין כתיבה). דיסק בלי סידורי אינו נרשם, ולכן
#    אינו מכווץ. שרת שאינו מכיר `partitions` (לפני #929) עונה 400 — ולכן
#    גם הוא אינו מכווץ: זיכרון של חצי מהמחיצות גרוע מאין זיכרון.
# 2. אחרי החזרה מוצלחת של **כולן** — `shrink-close`. סגירה שנכשלה היא
#    אזהרה: הדיסק כבר בגודלו, הרשומה נשארת כתומה בקונסולה, והאתחול הבא
#    סוגר אותה בעצמו (‏shrink_offer_pending רואה שהכול כבר בגודלו).
# 3. **באתחול** hello עונה `shrink_open` (ממשק 3) לדיסק עם רשומה פתוחה,
#    ו-shrinkoffer.sh מציע להחזיר — שורה **למחיצה** ב-`$RUN_DIR/shrink_open`.
#
# ‏HTTP כאן בלי `-f`: הקוד הוא הפסק (‏buildmenu.sh:console_signin) — 409
# "כבר פתוח" ו-000 "לא נשאל" הן שתי תשובות שונות, ו-`-f` היה מקפל את
# שתיהן ל-22.

SHRINKMEM_ERROR=""

_sm_json_str() { if [ -n "$1" ] && [ "$1" != null ]; then printf '"%s"' "$(json_escape "$1")"; else printf null; fi; }

_sm_post() {
    # $1 = path under /api/v1/agent, $2 = body file, $3 = response file.
    # Prints the HTTP code; 000 when the server was never reached.
    _mp=$(curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$2" "$SERVER/api/v1/agent/$1") || _mp=000
    printf '%s' "$_mp"
}

shrink_open_record() {
    # $1 disk $2 stage file (idx|start|size|newsec|type|uguid|name|attrs, שורה
    # למחיצה) $3 plan file (…|cur_bytes|new_bytes, לפי idx). ‏0 = השרת ענה 200
    # עם ok ו-id (נכתב ל-targets/$1/shrink_id). כל דבר אחר = 1 עם
    # SHRINKMEM_ERROR, והקורא אינו מכווץ.
    SHRINKMEM_ERROR=""
    _so_ser=$(disk_serial "$1")
    [ -n "$_so_ser" ] || { SHRINKMEM_ERROR="לדיסק $1 אין מספר סידורי — לשרת אין במה לזכור שהוא כווץ; קלטו בלי לכווץ. הדיסק לא שונה"; return 1; }
    _so_port=$(disk_port "$1"); case "$_so_port" in ''|*[!0-9]*) _so_port=null ;; esac
    _so_resp="${RESP:-$RUN_DIR/response.json}"
    _so_parts=""; _so_list=""
    while IFS='|' read -r _so_idx _so_start _so_size _so_new _so_type _so_uguid _so_name _so_attr <&3; do
        [ -n "$_so_idx" ] || continue
        _so_ntfs=$(awk -F'|' -v i="$_so_idx" '$1 == i { print $6 }' "$3"); case "$_so_ntfs" in ''|*[!0-9]*) _so_ntfs=null ;; esac
        _so_parts="$_so_parts${_so_parts:+,}$(printf '{"idx":%s,"start_sector":%s,"size_sectors":%s,"type_guid":"%s","unique_guid":%s,"attrs":%s,"name":%s,"ntfs_bytes":%s}' \
            "$_so_idx" "$_so_start" "$_so_size" "$_so_type" "$(_sm_json_str "$_so_uguid")" "$(_sm_json_str "$_so_attr")" "$(_sm_json_str "$_so_name")" "$_so_ntfs")"
        _so_list="$_so_list${_so_list:+, }$_so_idx"
    done 3< "$2"
    printf '{"mac":"%s","dev":"%s","serial":%s,"port":%s,"model":%s,"image_name":%s,"task_id":%s,"partitions":[%s]}' \
        "${MAC:-}" "$1" "$(_sm_json_str "$_so_ser")" "$_so_port" \
        "$(_sm_json_str "$(trim "$(cat "$SYSROOT/sys/block/$1/device/model" 2>/dev/null)")")" \
        "$(_sm_json_str "$(json_get "$_so_resp" ".task.name")")" "$(_sm_json_str "$(json_get "$_so_resp" ".task.id")")" \
        "$_so_parts" > "$RUN_DIR/shrink_open.json"
    _so_code=$(_sm_post shrink-open "$RUN_DIR/shrink_open.json" "$RUN_DIR/shrink_open.resp")
    _so_id=$(json_get "$RUN_DIR/shrink_open.resp" ".id")
    if [ "$_so_code" = 200 ] && [ "$(json_get "$RUN_DIR/shrink_open.resp" ".ok")" = true ]; then
        case "$_so_id" in ''|*[!0-9]*) ;; *)
            mkdir -p "$RUN_DIR/targets/$1"; echo "$_so_id" > "$RUN_DIR/targets/$1/shrink_id"
            log "shrink record #$_so_id opened on the server for $1 (serial $_so_ser, partitions $_so_list)"
            return 0 ;;
        esac
    fi
    if [ "$_so_code" = 409 ]; then
        SHRINKMEM_ERROR="לפי זיכרון השרת הדיסק הזה כבר מכווץ (רשומה #$_so_id מ-$(json_get "$RUN_DIR/shrink_open.resp" ".opened_at")) ולא הוחזר לגודלו — אתחלו את מחשב הבנייה ובחרו החזרה, או נקו את הרשומה בקונסולה. הדיסק לא שונה"
    else
        SHRINKMEM_ERROR="השרת לא רשם את הפריסה המקורית של מחיצה $_so_list לפני הכיווץ (http $_so_code: $(json_get "$RUN_DIR/shrink_open.resp" ".error")) — בלי רשומה אין כיווץ. הדיסק לא שונה"
    fi
    return 1
}

shrink_close_record() {
    # $1 = disk. ‏0 = השרת סגר את הרשומה. כשל נרשם ביומן ואינו מפיל דבר:
    # הדיסק כבר בגודלו, והרשומה נשארת כתומה בקונסולה עד "נקה" או עד
    # האתחול הבא (ראו למעלה, 2).
    _sc_id=$(cat "$RUN_DIR/targets/$1/shrink_id" 2>/dev/null); case "$_sc_id" in ''|*[!0-9]*) _sc_id=null ;; esac
    rm -f "$RUN_DIR/targets/$1/shrink_id"
    printf '{"mac":"%s","serial":%s,"id":%s}' "${MAC:-}" "$(_sm_json_str "$(disk_serial "$1")")" "$_sc_id" > "$RUN_DIR/shrink_close.json"
    _sc_code=$(_sm_post shrink-close "$RUN_DIR/shrink_close.json" "$RUN_DIR/shrink_close.resp")
    if [ "$_sc_code" = 200 ] && [ "$(json_get "$RUN_DIR/shrink_close.resp" ".ok")" = true ]; then
        log "shrink record $_sc_id closed on the server for $1"; return 0
    fi
    log "WARNING: $1: the server did not close shrink record $_sc_id (http $_sc_code) -- it stays in the console until cleared"
    return 1
}

shrink_records_refresh() {
    # תשובת ה-hello האחרונה → $RUN_DIR/shrink_open, שורה **למחיצה** (‏#929):
    # id|serial|idx|start|size|type|uguid|name|attrs|ntfs|opened_at. רשומה של
    # שרת שקדם ל-#929 (בלי `partitions`) נקראת מהשדות העליונים — שורה אחת.
    # כמו disk_failures_refresh: "ok" ראשונה היא הראיה שה-JSON נקרא בשלמותו;
    # תשובה שלא נקראה משאירה את הקובץ הקודם (עיקרון 5). 0 = רוענן.
    _sr_n="$RUN_DIR/shrink_open.next"
    # shellcheck disable=SC2016 # ‏$r הוא משתנה של jq, לא של המעטפת
    json_get_join "${RESP:-$RUN_DIR/response.json}" \
        '["ok"] + [(.shrink_open // [])[] as $r | ($r.partitions // [$r])[] | "\($r.id)|\($r.serial // "")|\(.idx)|\(.start_sector)|\(.size_sectors)|\(.type_guid)|\(.unique_guid // "")|\(.name // "")|\(.attrs // "")|\(.ntfs_bytes // "")|\($r.opened_at // "")"]' \
        > "$_sr_n" 2>/dev/null
    [ "$(head -n 1 "$_sr_n" 2>/dev/null)" = ok ] || { rm -f "$_sr_n"; return 1; }
    mv "$_sr_n" "$RUN_DIR/shrink_open"
}

shrink_pending_for() {
    # $1 = disk. מדפיס את **כל** שורות הרשומה הפתוחה של הסידורי של הדיסק
    # הזה (שורה למחיצה, מ-shrink_records_refresh) ומחזיר 0; אין רשומה, או
    # אין סידורי — 1.
    [ -f "$RUN_DIR/shrink_open" ] || return 1
    _pf_s=$(disk_serial "$1"); [ -n "$_pf_s" ] || return 1
    awk -F'|' -v s="$_pf_s" 'NF >= 6 && $2 == s { print; f = 1 } END { exit !f }' "$RUN_DIR/shrink_open" 2>/dev/null
}

shrink_note_record() {
    # $1 = disk, $2 = למה הרשומה עדיין פתוחה. best-effort: הקונסולה מציגה את
    # הסיבה ליד הרשומה; כשל בשליחה נרשם ביומן ואינו משנה דבר.
    _sn_id=$(cat "$RUN_DIR/targets/$1/shrink_id" 2>/dev/null); case "$_sn_id" in ''|*[!0-9]*) _sn_id=null ;; esac
    printf '{"mac":"%s","serial":%s,"id":%s,"note":"%s"}' "${MAC:-}" "$(_sm_json_str "$(disk_serial "$1")")" "$_sn_id" "$(json_escape "$2")" > "$RUN_DIR/shrink_note.json"
    _sn_code=$(_sm_post shrink-note "$RUN_DIR/shrink_note.json" "$RUN_DIR/shrink_note.resp")
    [ "$_sn_code" = 200 ] || log "WARNING: $1: the reason was not delivered to the server (shrink-note http $_sn_code)"
}
