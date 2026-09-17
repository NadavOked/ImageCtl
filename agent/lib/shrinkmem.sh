# shrinkmem.sh -- ‏#926: זיכרון הכיווץ בשרת. POSIX sh (busybox ash).
#
# ‏#87 מכווץ את מחיצת ה-NTFS של דיסק הבנייה לפני הקליטה ומחזיר אותה
# אחריה, והפריסה המקורית ישבה ב-`targets/<disk>/shrunk` — ‏tmpfs. אובדן
# חשמל או אתחול בין `ntfsresize -s` לבין ההחזרה השאיר את הדיסק מכווץ
# בלי שאיש יודע: ווינדוס עולה, ‏C: קטן, והסוכן כבר לא זוכר מה היה. אותה
# משפחה כמו #856 ו-#874, ואותו פתרון — **השרת זוכר**, לפי סידורי:
#
# 1. **לפני הכתיבה הראשונה למקור** (‏shrink_before_capture, לפני
#    `ntfsresize -s`) הסוכן שולח `shrink-open` עם הכניסה המלאה — כל מה
#    ש-shrink_restore_source צריך. בלי 2xx עם `ok` — **הכיווץ לא מתחיל**
#    (עיקרון 5: בלי רשומה אין כתיבה). דיסק בלי סידורי אינו נרשם, ולכן
#    אינו מכווץ.
# 2. אחרי החזרה מוצלחת — `shrink-close`. סגירה שנכשלה היא אזהרה: הדיסק
#    כבר בגודלו, הרשומה נשארת כתומה בקונסולה, והאתחול הבא סוגר אותה בעצמו
#    (‏shrink_offer_pending רואה שהמחיצה כבר בגודלה המקורי).
# 3. **באתחול** hello עונה `shrink_open` (ממשק 3) לדיסק עם רשומה פתוחה.
#    מחשב הבנייה, לפני התפריט, קורא את הכניסה **מהדיסק** (‏sgdisk -i) ורק
#    אם התחילה וה-GUID הייחודי תואמים לרשומה והגודל קטן מהמקורי — שואל
#    (‏attended, ‏#906): החזר / השאר. **בלי תשובה — השאר** (עיקרון 1: שום
#    דבר לא נכתב על דיסק בלי אדם שאמר כן). ההחזרה עצמה היא
#    shrink_restore_source עם הנתונים **מהרשומה**, לא מ-tmpfs.
#
# ‏HTTP כאן בלי `-f`: הקוד הוא הפסק (‏buildmenu.sh:console_signin) — 409
# "כבר פתוח" ו-000 "לא נשאל" הם שתי תשובות שונות, ו-`-f` היה מקפל את
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
    # $1 disk $2 idx $3 start $4 size_sectors $5 type $6 uguid $7 name $8 attrs
    # $9 ntfs bytes. ‏0 = השרת ענה 200 עם ok ו-id (נכתב ל-targets/$1/shrink_id).
    # כל דבר אחר = 1 עם SHRINKMEM_ERROR, והקורא אינו מכווץ.
    SHRINKMEM_ERROR=""
    _so_ser=$(disk_serial "$1")
    [ -n "$_so_ser" ] || { SHRINKMEM_ERROR="לדיסק $1 אין מספר סידורי — לשרת אין במה לזכור שהוא כווץ; קלטו בלי לכווץ. הדיסק לא שונה"; return 1; }
    _so_port=$(disk_port "$1"); case "$_so_port" in ''|*[!0-9]*) _so_port=null ;; esac
    _so_resp="${RESP:-$RUN_DIR/response.json}"
    printf '{"mac":"%s","dev":"%s","serial":%s,"port":%s,"model":%s,"image_name":%s,"task_id":%s,"idx":%s,"start_sector":%s,"size_sectors":%s,"type_guid":"%s","unique_guid":%s,"attrs":%s,"name":%s,"ntfs_bytes":%s}' \
        "${MAC:-}" "$1" "$(_sm_json_str "$_so_ser")" "$_so_port" \
        "$(_sm_json_str "$(trim "$(cat "$SYSROOT/sys/block/$1/device/model" 2>/dev/null)")")" \
        "$(_sm_json_str "$(json_get "$_so_resp" ".task.name")")" "$(_sm_json_str "$(json_get "$_so_resp" ".task.id")")" \
        "$2" "$3" "$4" "$5" "$(_sm_json_str "$6")" "$(_sm_json_str "$8")" "$(_sm_json_str "$7")" "${9:-null}" \
        > "$RUN_DIR/shrink_open.json"
    _so_code=$(_sm_post shrink-open "$RUN_DIR/shrink_open.json" "$RUN_DIR/shrink_open.resp")
    _so_id=$(json_get "$RUN_DIR/shrink_open.resp" ".id")
    if [ "$_so_code" = 200 ] && [ "$(json_get "$RUN_DIR/shrink_open.resp" ".ok")" = true ]; then
        case "$_so_id" in ''|*[!0-9]*) ;; *)
            mkdir -p "$RUN_DIR/targets/$1"; echo "$_so_id" > "$RUN_DIR/targets/$1/shrink_id"
            log "shrink record #$_so_id opened on the server for $1 (serial $_so_ser, partition $2)"
            return 0 ;;
        esac
    fi
    if [ "$_so_code" = 409 ]; then
        SHRINKMEM_ERROR="לפי זיכרון השרת הדיסק הזה כבר מכווץ (רשומה #$_so_id מ-$(json_get "$RUN_DIR/shrink_open.resp" ".opened_at")) ולא הוחזר לגודלו — אתחלו את מחשב הבנייה ובחרו החזרה, או נקו את הרשומה בקונסולה. הדיסק לא שונה"
    else
        SHRINKMEM_ERROR="השרת לא רשם את הפריסה המקורית של מחיצה $2 לפני הכיווץ (http $_so_code: $(json_get "$RUN_DIR/shrink_open.resp" ".error")) — בלי רשומה אין כיווץ. הדיסק לא שונה"
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
    # תשובת ה-hello האחרונה → $RUN_DIR/shrink_open, שורה לרשומה. כמו
    # disk_failures_refresh: "ok" ראשונה היא הראיה שה-JSON נקרא בשלמותו;
    # תשובה שלא נקראה משאירה את הקובץ הקודם (עיקרון 5). 0 = רוענן.
    _sr_n="$RUN_DIR/shrink_open.next"
    json_get_join "${RESP:-$RUN_DIR/response.json}" \
        '["ok"] + ((.shrink_open // []) | map("\(.id)|\(.serial // "")|\(.idx)|\(.start_sector)|\(.size_sectors)|\(.type_guid)|\(.unique_guid // "")|\(.name // "")|\(.attrs // "")|\(.ntfs_bytes // "")|\(.opened_at // "")"))' \
        > "$_sr_n" 2>/dev/null
    [ "$(head -n 1 "$_sr_n" 2>/dev/null)" = ok ] || { rm -f "$_sr_n"; return 1; }
    mv "$_sr_n" "$RUN_DIR/shrink_open"
}

shrink_pending_for() {
    # $1 = disk. מדפיס את הרשומה הפתוחה של הסידורי של הדיסק הזה (השורה
    # מ-shrink_records_refresh) ומחזיר 0; אין רשומה, או אין סידורי — 1.
    [ -f "$RUN_DIR/shrink_open" ] || return 1
    _pf_s=$(disk_serial "$1"); [ -n "$_pf_s" ] || return 1
    awk -F'|' -v s="$_pf_s" 'NF >= 6 && $2 == s { print; f = 1; exit } END { exit !f }' "$RUN_DIR/shrink_open" 2>/dev/null
}

shrink_restore_ask() {
    # $1 disk $2 idx $3 partition/original bytes $4 current/filesystem bytes;
    # $5 = "fs": הטבלה כבר בגודלה ורק מערכת הקבצים קטנה (סקירת Fable). ההכרעה
    # לבדה ל-stdout: restore / leave. בלי תשובה (EOF) — leave: עיקרון 1.
    while :; do
        {
            ui_clear; ui_header
            if [ "${5:-}" = fs ]; then
                echo "  The NTFS filesystem in partition $2 on /dev/$1 is smaller than its partition"
                echo "  (the partition was grown back after a capture, the filesystem was not):"
                echo "    filesystem $(_shrink_gb "$4"), partition $(_shrink_gb "$3")"
                echo
                echo "    [1] Grow the filesystem to fill the partition (recommended)"
            else
                echo "  Partition $2 on /dev/$1 was shrunk for a capture and never grown back"
                echo "  (a power loss or a reboot before the capture ended):"
                echo "    now $(_shrink_gb "$4"), originally $(_shrink_gb "$3")"
                echo
                echo "    [1] Grow it back to its original size (recommended)"
            fi
            echo "    [2] Leave it as it is"
            echo
            printf "  Choose [1-2]: "
        } >&2
        read -r _ra_c || { echo leave; return; }
        case "$_ra_c" in 1) echo restore; return ;; 2) echo leave; return ;; esac
    done
}

shrink_note_record() {
    # $1 = disk, $2 = למה הרשומה עדיין פתוחה. best-effort: הקונסולה מציגה את
    # הסיבה ליד הרשומה; כשל בשליחה נרשם ביומן ואינו משנה דבר.
    _sn_id=$(cat "$RUN_DIR/targets/$1/shrink_id" 2>/dev/null); case "$_sn_id" in ''|*[!0-9]*) _sn_id=null ;; esac
    printf '{"mac":"%s","serial":%s,"id":%s,"note":"%s"}' "${MAC:-}" "$(_sm_json_str "$(disk_serial "$1")")" "$_sn_id" "$(json_escape "$2")" > "$RUN_DIR/shrink_note.json"
    _sn_code=$(_sm_post shrink-note "$RUN_DIR/shrink_note.json" "$RUN_DIR/shrink_note.resp")
    [ "$_sn_code" = 200 ] || log "WARNING: $1: the reason was not delivered to the server (shrink-note http $_sn_code)"
}

shrink_fs_bytes() {
    # $1 = partition node. מדפיס "<גודל הווליום>|<גודל קלאסטר>" מ-ntfsresize --info
    # (קריאה בלבד). בלי שורת גודל — 1: "לא הצלחנו לבדוק" אינו "מתאים".
    ntfsresize --info --no-progress-bar "$1" > "$RUN_DIR/ntfsresize.fit" 2>&1
    _fb_v=$(sed -n 's/^Current volume size: *\([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.fit" | head -n 1)
    _fb_c=$(sed -n 's/^Cluster size *: *\([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.fit" | head -n 1)
    case "$_fb_v" in ''|*[!0-9]*) return 1 ;; esac
    case "$_fb_c" in ''|*[!0-9]*) _fb_c=4096 ;; esac
    printf '%s|%s' "$_fb_v" "$_fb_c"
}

shrink_grow_fs() {
    # $1 = disk, $2 = idx. מותח את מערכת הקבצים למחיצה **בלי לגעת בטבלה**
    # (היא כבר בגודלה): ntfsresize -f -f → ntfsfix -d. כשל = הסיבה ב-error
    # ובשרת (shrink-note), הרשומה נשארת פתוחה.
    _gf_node=$(partition_node "$1" "$2")
    if ntfsresize -f -f --no-progress-bar "$_gf_node" >> "$LOG_FILE" 2>&1; then
        if ntfsfix -d "$_gf_node" >> "$LOG_FILE" 2>&1; then log "$1: partition $2: filesystem grown to the partition"; return 0; fi
        _gf_why="דגל ה-dirty לא נוקה אחרי המתיחה (ntfsfix -d)"
    else _gf_why="ntfsresize -f -f rc=$?"; fi
    _gf_msg="מערכת הקבצים במחיצה $2 לא נמתחה לגודל המחיצה אחרי שהטבלה הוחזרה ($_gf_why) — ווינדוס לא יכול להרחיב אותה בעצמו"
    log "WARNING: $1: $_gf_msg"; echo "$_gf_msg" > "$RUN_DIR/targets/$1/error"
    shrink_note_record "$1" "$_gf_msg"
    return 1
}

shrink_offer_disk() {
    # $1 = disk, $2 = הרשומה. הכניסה נקראת **מהדיסק** ומושווית לרשומה לפני
    # שמוצע דבר: תחילה או GUID ייחודי שונים = לא אותה מחיצה, כלום לא נכתב.
    _od_t="$RUN_DIR/targets/$1"; mkdir -p "$_od_t"
    printf '%s\n' "$2" > "$_od_t/shrunk_pending"
    IFS='|' read -r _od_id _od_ser _od_idx _od_start _od_size _od_type _od_uguid _od_name _od_attr _od_ntfs _od_when < "$_od_t/shrunk_pending"
    _od_info=$(sgdisk -i "$_od_idx" "$DEVROOT/$1" 2>> "$LOG_FILE")
    _od_got=$(printf '%s\n' "$_od_info" | awk -F': ' '/^First sector/ { split($2, f, " ") } /^Partition size/ { split($2, z, " ") }
                                                     /^Partition unique GUID/ { u = $2 } END { print f[1] "|" z[1] "|" u }')
    _od_first=${_od_got%%|*}; _od_rest=${_od_got#*|}; _od_now=${_od_rest%%|*}; _od_u=${_od_rest#*|}
    case "$_od_now" in ''|*[!0-9]*) _od_now="" ;; esac
    if [ "$_od_first" != "$_od_start" ] || [ "$_od_u" != "$_od_uguid" ] || [ -z "$_od_now" ]; then
        log "WARNING: $1: shrink record #$_od_id (partition $_od_idx at $_od_start, $_od_uguid) does not match the disk (read: $_od_got) -- nothing changed; clear it in the console if the layout was changed on purpose"
        return 1
    fi
    _od_ss=$(cat "$SYSROOT/sys/block/$1/queue/logical_block_size" 2>/dev/null); case "$_od_ss" in ''|*[!0-9]*) _od_ss=512 ;; esac
    if [ "$_od_now" -ge "$_od_size" ]; then
        # הטבלה בגודלה — אבל האם מערכת הקבצים? "טבלה נכתבה, המתיחה נפלה" נראה
        # בדיוק כך (סקירת Fable): ווינדוס לא יכול להרחיב (אין שטח לא-מוקצה),
        # וסגירה שקטה הייתה מוחקת את הראיה היחידה. גודל קטן מהמחיצה ביותר
        # מקלאסטר = שאלה; לא נקרא = לא נסגר.
        if ! _od_fs=$(shrink_fs_bytes "$(partition_node "$1" "$_od_idx")"); then
            log "WARNING: $1: partition $_od_idx is at its size but could not read the NTFS volume size (ntfsresize --info) -- record #$_od_id stays open, nothing changed"
            return 1
        fi
        _od_vol=${_od_fs%%|*}
        if [ $((_od_vol + ${_od_fs#*|})) -ge $((_od_now * _od_ss)) ]; then
            log "$1: partition $_od_idx is already $_od_now sectors and the filesystem fills it (record #$_od_id says $_od_size) -- closing the record"
            echo "$_od_id" > "$_od_t/shrink_id"; shrink_close_record "$1" || :
            return 0
        fi
        shrink_gui_release
        _od_q="מערכת הקבצים במחיצה $_od_idx קטנה מהמחיצה ($(_shrink_gb "$_od_vol") מתוך $(_shrink_gb $((_od_now * _od_ss)))) — הטבלה הוחזרה והמתיחה לא; מתח/השאר"
        case "$(attended "$_od_q" shrink_restore_ask "$1" "$_od_idx" $((_od_now * _od_ss)) "$_od_vol" fs)" in
            restore) ;;
            *) log "$1: partition $_od_idx: filesystem left at $_od_vol bytes by the operator's choice (record #$_od_id stays open)"; return 0 ;;
        esac
        echo "$_od_id" > "$_od_t/shrink_id"
        if shrink_grow_fs "$1" "$_od_idx"; then shrink_close_record "$1" || :; _od_rc=0; else _od_rc=1; fi
        _shrink_offer_screen "$1" "$_od_idx" "$_od_rc" "filesystem now fills the partition"; return "$_od_rc"
    fi
    shrink_gui_release
    _od_q="מחיצה $_od_idx בדיסק הזה כווצה לקליטה ולא הוחזרה לגודלה ($(_shrink_gb $((_od_now * _od_ss))) מתוך $(_shrink_gb $((_od_size * _od_ss)))) — החזר/השאר"
    case "$(attended "$_od_q" shrink_restore_ask "$1" "$_od_idx" $((_od_size * _od_ss)) $((_od_now * _od_ss)))" in
        restore) ;;
        *) log "$1: partition $_od_idx left at $_od_now sectors by the operator's choice (record #$_od_id stays open)"; return 0 ;;
    esac
    printf '%s\n' "$_od_idx|$_od_start|$_od_size|$_od_now|$_od_type|$_od_uguid|$_od_name|$_od_attr" > "$_od_t/shrunk"
    echo "$_od_id" > "$_od_t/shrink_id"
    _od_keep="$CAPTURE_SHRINK_RESTORE"; CAPTURE_SHRINK_RESTORE=1
    shrink_restore_source "$1"; _od_rc=$?
    CAPTURE_SHRINK_RESTORE="$_od_keep"
    _shrink_offer_screen "$1" "$_od_idx" "$_od_rc" "back to its original size"; return "$_od_rc"
}

_shrink_offer_screen() {
    # $1 disk $2 idx $3 rc $4 what succeeded. המסך אחרי ההחזרה, לשני המסלולים.
    ui_clear; ui_header
    if [ "$3" -eq 0 ]; then echo "  Partition $2 on /dev/$1: $4."
    else echo "  FAILED: $(cat "$RUN_DIR/targets/$1/error" 2>/dev/null)"; echo; echo "  The record stays in the console. Contact IT."; fi
    [ "${IMAGECTL_TEST:-0}" = 1 ] || sleep 8
}

shrink_offer_pending() {
    # מלולאת מחשב הבנייה, לפני שהמסך הגרפי תופס את התצוגה. שואל פעם אחת
    # לאתחול; 0 תמיד — ההצעה אינה עוצרת את המכונה.
    [ "${_shrink_offered:-0}" = 0 ] || return 0
    shrink_records_refresh || return 0
    _op_all=$(list_disks)
    for _op_d in $_op_all; do
        _op_line=$(shrink_pending_for "$_op_d") || continue
        _shrink_offered=1
        # שני דיסקים כאן עם אותו סידורי (‏"0000000000000000" של גשר USB, ‏VM בלי
        # סידורי): הרשומה יכולה להיות של כל אחד, והשער השני אינו מבחין — שחזור
        # מאותו אימג' שומר תחילה ו-GUID ייחודי (#26), ואח שכבר "בגודלו" היה
        # סוגר בשקט את הרשומה של המכווץ. לא נוגעים באף אחד, ולא סוגרים.
        _op_s=$(disk_serial "$_op_d"); _op_n=0
        for _op_x in $_op_all; do [ "$(disk_serial "$_op_x")" = "$_op_s" ] && _op_n=$((_op_n + 1)); done
        if [ "$_op_n" -gt 1 ]; then
            log "WARNING: $_op_d: $_op_n disks here report the same serial '$_op_s' -- shrink record #${_op_line%%|*} cannot be tied to one of them; nothing changed, nothing closed"
            continue
        fi
        shrink_offer_disk "$_op_d" "$_op_line" || :
    done
    return 0
}
