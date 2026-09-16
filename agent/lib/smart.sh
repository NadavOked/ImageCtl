# smart.sh -- SMART health gate on the restore side (#652), like Clonezilla.
# POSIX sh (busybox ash). Checked only before writing to a target, never on
# capture and never on a machine that is only booting local.
#
# עיקרון 5, בצורתו החזקה ביותר: **SMART הוא שער החלטה, לא שער חסימה.**
# רק ממצא בריאות שלילי עוצר ומבקש בחירה מהמפעיל. אין SMART, כונן USB,
# בקר RAID, או `smartctl` שנכשל לפתוח את ההתקן -> `unchecked`, וכונן
# ‏`unchecked` נכתב **בלי שאלה**. "לא הצלחנו לבדוק" איננו "בדקנו ונכשל".
#
# חוט ההוכחה: הקוד היוצא של `smartctl` הוא מפת-סיביות שבה "הדיסק נכשל"
# הוא סיבית לגיטימית (bit 3 = exit 8), בדיוק כמו `grep`/`git check-ignore`
# שבהם `1` הוא תשובה ולא כשל (CLAUDE.md). לכן לעולם לא מסיקים "נכשל
# לפתוח" מ-`rc != 0` — רק מ-bit 1 (`rc & 2`). המסקנה מגיעה מהפלט שנקרא,
# לא מהיעדר-כשל.

SMARTCTL="${SMARTCTL:-smartctl}"

smart_disk_label() {
    # $1 = disk name (sda). התווית שהמפעיל רואה על ה-TTY. שם ההתקן בקרנל
    # הוא סדר הגילוי ולא החריץ; ה-`port` הוא החריץ (#27), ולכן דיסק עם
    # port הוא "Disk N". בלי port (NVMe/USB/VM) — שם ההתקן, כי מספר שגוי
    # גרוע ממספר חסר (אותו כלל כמו `port` ב-hello). ה-TTY של הסוכן הוא
    # ASCII/אנגלית (הקונסולה הלינוקסית אינה מרנדרת RTL); הקונסולה
    # ה-web מציגה "דיסק N" מאותו port.
    _sl_p=$(disk_port "$1")
    case "$_sl_p" in
        ''|*[!0-9]*) printf 'Disk %s' "$1" ;;
        *)           printf 'Disk %s' "$_sl_p" ;;
    esac
}

smart_classify() {
    # $1=health(PASSED|FAILED|unknown) $2=realloc $3=pending
    # $4=uncorrectable $5=crc. מדפיס "<verdict> <reason>". פונקציה טהורה,
    # נבדקת שורה-שורה בלי smartctl.
    #
    # ‏#872 (נדב 16/09): **שורת הבריאות לבדה מכריעה** — מה שה-BIOS מציג.
    # המונים 5/197/198/199 נקראים ונשמרים (מידע) ואינם צובעים: על 6 דיסקי
    # הכיתה CRC הוא 137–55,242 והם משכפלים מושלם — מונה מצטבר מספר על
    # ההיסטוריה של הכבלים, ושאלה שנשאלת על 6/6 דיסקים אינה אומרת כלום.
    if [ "$1" = "FAILED" ]; then echo "fail health_failed"; return; fi
    if [ "$1" = "PASSED" ]; then echo "ok ok"; return; fi
    # ‏-H לא PASSED ולא FAILED = לא קראנו בריאות; **אינו** `ok` (עיקרון 5).
    echo "unchecked no_health"
}

_smart_int() {
    # מדפיס את ערך ה-RAW של מאפיין SMART לפי מזהה (199) או שם, כמספר שלם או 0.
    # ‏$1 = פלט `smartctl -A`, $2.. = מזהים/שמות; המזהה יציב בין יצרנים, השם
    # לא — סמסונג מדפיסה `CRC_Error_Count` ולא `UDMA_CRC_Error_Count` (#866).
    # ה-RAW הוא העמודה האחרונה; ממנו רק הספרות המובילות ("35 (Min/Max ..)").
    # ‏#858: NVMe אינו טבלה אלא `Media and Data Integrity Errors:    1,234` —
    # השם עד הנקודתיים (רווחים -> קו תחתון) מושווה גם הוא, ופסיקי-אלפים מוסרים.
    _si_f="$1"; shift
    for _si_name in "$@"; do
        _si_v=$(awk -v n="$_si_name" '{ k = $0; sub(/:.*/, "", k); gsub(/ /, "_", k) }
            $1 == n || $2 == n || k == n { v = $NF; gsub(/,/, "", v)
                sub(/[^0-9].*/, "", v); if (v != "") print v; exit }' \
            "$_si_f" 2>/dev/null)
        [ -n "$_si_v" ] && { printf '%s' "$_si_v"; return; }
    done
    printf '0'
}

_smart_store() {
    # $1=disk $2=verdict $3=reason $4=realloc $5=pending $6=uncorr $7=crc.
    # רשומה אחת בשורה, שממנה גם ה-hello (שדה `smart`) וגם ה-disk_event
    # קוראים בלי לחזור על ה-probe.
    _ss_d="$RUN_DIR/smart"; mkdir -p "$_ss_d"
    printf '%s %s %s %s %s %s\n' \
        "$2" "$3" "${4:-0}" "${5:-0}" "${6:-0}" "${7:-0}" > "$_ss_d/$1.v"
}

smart_field() {
    # $1=disk, $2=field index (1=verdict 2=reason 3=realloc 4=pending
    # 5=uncorr 6=crc). ריק אם אין רשומה.
    awk -v i="$2" '{print $i}' "$RUN_DIR/smart/$1.v" 2>/dev/null
}

smart_probe() {
    # $1 = disk name. מריץ smartctl פעם אחת, מסווג, ושומר רשומה. מדפיס
    # את ה-verdict. לעולם אינו חוסם: כל כשל בקריאת SMART הוא `unchecked`,
    # שנכתב. תוצאה נשמרת במטמון — קריאה שנייה על אותו דיסק לא מריצה שוב.
    _sp_d="$1"; _sp_dev="$DEVROOT/$1"
    mkdir -p "$RUN_DIR/smart"
    if [ -f "$RUN_DIR/smart/$1.v" ]; then smart_field "$1" 1; return; fi
    if ! command -v "$SMARTCTL" >/dev/null 2>&1; then
        _smart_store "$1" unchecked no_smartctl; echo unchecked; return
    fi
    _sp_out="$RUN_DIR/smart/$1.out"
    "$SMARTCTL" -H -A -n never "$_sp_dev" > "$_sp_out" 2>>"$LOG_FILE"; _sp_rc=$?
    # bit 1 (rc & 2) = פתיחת ההתקן נכשלה. רק אז מנסים דרך שכבת ה-SAT
    # (גשר USB-SATA); שאר הסיביות (בעיקר bit 3 = הדיסק נכשל) הן תשובה.
    if [ "$((_sp_rc / 2 % 2))" -eq 1 ]; then
        "$SMARTCTL" -d sat -H -A -n never "$_sp_dev" > "$_sp_out" 2>>"$LOG_FILE"
        _sp_rc=$?
        if [ "$((_sp_rc / 2 % 2))" -eq 1 ]; then
            _smart_store "$1" unchecked open_failed; echo unchecked; return
        fi
    fi
    # בקר RAID / התקן בלי SMART: smartctl פותח אבל אין הערכת בריאות.
    if grep -qiE 'SMART (support is: )?Unavailable|does not support SMART' \
            "$_sp_out" 2>/dev/null; then
        _smart_store "$1" unchecked no_smart; echo unchecked; return
    fi
    # ‏#858: SCSI/SAS מדפיס `SMART Health Status: FAILURE` (לא FAILED), NVMe `FAILED!`;
    # ו-bit 3 של קוד היציאה ("הדיסק נכשל") הוא ראיה חיובית גם בניסוח זר (עיקרון 5).
    case "$(grep -iE 'overall-health|SMART Health Status' "$_sp_out" 2>/dev/null)" in
        *FAIL*|*BAD*) _sp_h=FAILED ;;
        *PASS*|*OK*)  _sp_h=PASSED ;;
        *)            _sp_h=unknown ;;
    esac
    [ "$((_sp_rc / 8 % 2))" -eq 1 ] && _sp_h=FAILED
    _sp_re=$(_smart_int "$_sp_out" 5 Reallocated_Sector_Ct Reallocated_Event_Count)
    _sp_pe=$(_smart_int "$_sp_out" 197 Current_Pending_Sector)
    _sp_un=$(_smart_int "$_sp_out" 198 Offline_Uncorrectable Media_and_Data_Integrity_Errors)
    _sp_cr=$(_smart_int "$_sp_out" 199 UDMA_CRC_Error_Count)
    # shellcheck disable=SC2046 # פיצול המילים מכוון — ל-$1/$2
    set -- $(smart_classify "$_sp_h" "$_sp_re" "$_sp_pe" "$_sp_un" "$_sp_cr")
    _smart_store "$_sp_d" "$1" "$2" "$_sp_re" "$_sp_pe" "$_sp_un" "$_sp_cr"
    echo "$1"
}

smart_hello_field() {
    # $1 = disk. **מטמון בלבד — לעולם אינו מריץ probe** (בלי זה כל אתחול
    # של תחנת כיתה שעולה לדיסק מקומי היה משלם שנייה על smartctl מיותר).
    # מלא ע"י smart_preflight בצד השחזור וע"י smart_probe_idle במחשב
    # השיכפול הממתין — ומשם הקונסולה מציגה בריאות פר-דיסק *לפני* start.
    case "$(smart_field "$1" 1)" in
        ok|warn|fail) smart_field "$1" 1 ;;
        *)            printf 'unchecked' ;;
    esac
}

smart_probe_idle() {
    # $@ = disks. במחשב השיכפול הממתין: probe חד-פעמי לכל מגירה כדי
    # שה-hello יישא בריאות והקונסולה תראה אותה לפני שהמפעיל משגר סבב
    # (דרישת נדב #652). ממוסמס ע"י המטמון של smart_probe.
    for _pi_d in "$@"; do smart_probe "$_pi_d" >/dev/null; done
}

smart_choice() { attended "$1: $2" smart_ask "$@"; }   # #906: hello keeps going while a person is asked (attended.sh)
smart_ask() {
    # $1 = label (Disk N), $2 = context line, $3 = disk, $4 = verdict,
    # $5 = reason, $6 = ברירת המחדל בלי תשובה. מצייר תפריט ASCII (כמו
    # buildmenu.sh) ומחזיר את ההכרעה: replace / rescue / skip. הקלט נקרא
    # מ-stdin, ולכן הפונקציה נבדקת בהזרקת מקש.
    #
    # ‏#872: לדיסק **אדום** (‏failed_last -- נכשל בכתיבה הקודמת, הוכח) אין
    # "כתוב בכל זאת": החלף, או המשך בלעדיו (= דלג). בלי תשובה (EOF, מסך
    # שנעלם) -- "המשך" של אותו צבע: כתום כותב, אדום מדלג; הברירה מגיעה
    # מהקורא (‏$6), וריק = skip. ‏#666: כשיש מסך cloner חי ההכרעה נלקחת
    # ממנו -- מסלול קלט אחד; נעלם המסך, gui_smart_choice נכשל והתפריט נכנס.
    _sc_red=0; [ "${4:-}" = failed_last ] && _sc_red=1
    if [ "$#" -ge 5 ] &&
            _sc_gui=$(gui_smart_choice "$3" "$4" "$5"); then
        # מסך ישן שהציע "כתוב בכל זאת" על אדום: לא מתקבל, מדלגים.
        [ "$_sc_red" = 1 ] && [ "$_sc_gui" = rescue ] && _sc_gui=skip
        printf '%s\n' "$_sc_gui"
        return 0
    fi
    while :; do
        # הציור הולך ל-stderr, וההכרעה בלבד ל-stdout: הקורא לוכד את
        # הפלט ב-`$( )`, ותפריט על stdout היה נבלע לתוך ההכרעה.
        {
            ui_clear; ui_header
            echo "  $1: $2"
            echo
            echo "    [1] Power off, swap the disk, power on"
            if [ "$_sc_red" = 1 ]; then
                echo "    [2] Continue without this disk"
            else
                echo "    [2] Write anyway (rescue)"
                echo "    [3] Skip this disk"
            fi
            echo
            printf "  Choose [1-%d]: " $((3 - _sc_red))
        } >&2
        read -r _sc_c || { echo "${6:-skip}"; return; }
        case "$_sc_c$_sc_red" in
            1?)    echo replace; return ;;
            20)    echo rescue;  return ;;
            21|30) echo skip;    return ;;
            *) ;;   # קלט לא-חוקי: מציירים שוב, לא בוחרים בשקט
        esac
    done
}

smart_event_json() {
    # חוזה disk_event (interfaces.md סעיף 12): הסוכן מכריע מקומית, השרת
    # שומר לריבוט-החלפה ולצפיית #380. $1=session $2=disk $3=verdict
    # $4=reason $5=write.state $6=decision(''|replace|rescue|skip).
    _ej_dec="null"; [ -n "$6" ] && _ej_dec="\"$6\""
    _ej_port=$(disk_port "$2"); [ -n "$_ej_port" ] || _ej_port=null
    _ej_ser=$(disk_serial "$2")
    _ej_ser_j="null"; [ -n "$_ej_ser" ] && _ej_ser_j="\"$(json_escape "$_ej_ser")\""
    printf '{"session_id":"%s","mac":"%s","disk":"%s","port":%s,"serial":%s,"smart":{"verdict":"%s","reason":"%s","realloc":%s,"pending":%s,"uncorrectable":%s,"crc":%s},"write_state":"%s","decision":%s}' \
        "$1" "${MAC:-}" "$2" "$_ej_port" "$_ej_ser_j" \
        "$3" "$4" "$(smart_field "$2" 3)" "$(smart_field "$2" 4)" \
        "$(smart_field "$2" 5)" "$(smart_field "$2" 6)" "$5" "$_ej_dec"
}

smart_send_event() {
    # שולח disk_event לשרת. best-effort כמו trace_step: אבחון שמפיל
    # שחזור הוא נזק. לעולם מחזיר 0, ומדווח אם לא נמסר.
    smart_event_json "$@" > "$RUN_DIR/disk_event.json" 2>/dev/null
    if http_post_json "$SERVER/api/v1/agent/disk-event" \
            "$RUN_DIR/disk_event.json" >/dev/null 2>&1; then
        return 0
    fi
    # ‏#875: ל-stderr -- הפונקציה רצה בתוך smart_preflight, ש-smart_gate לוכד
    # ב-`$( )`; שורת יומן על stdout הייתה נכנסת לרשימת הדיסקים לכתיבה.
    log "disk_event for $2 was not delivered -- continuing" >&2
    return 0
}

smart_preflight() {
    # $1 = session id, $2.. = disks. השער לפני כתיבה: probe לכל דיסק,
    # disk_event לכל דיסק, ותפריט בחירה על fail/failed_last (בתור, אחד-אחד).
    # מדפיס את רשימת הדיסקים שייכתבו בפועל (skipped ו-replace מוסרים).
    # ‏ok/unchecked לא נשאלים — unchecked נכתב, זה קריטי (עיקרון 5).
    _pf_ses="$1"; shift
    rm -f "$RUN_DIR/smart/awaiting_replace"
    disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
    _pf_write=""
    for _pf_d in "$@"; do
        # תיקיית היעד קיימת כבר מ-target_init; ה-mkdir הופך את השער לעצמאי --
        # target_set חייב תיקייה, וקפיאה על היעדרה תהיה כשל שקט (עיקרון 5).
        mkdir -p "$RUN_DIR/targets/$_pf_d"
        _pf_v=$(smart_probe "$_pf_d")
        _pf_r=$(smart_field "$_pf_d" 2)
        # ‏#872/#874: זיכרון השרת גובר על SMART -- דיסק שנכשל בכתיבה הקודמת
        # (לפי סידורי או חריץ) הוא אדום גם כשהבריאות שלו PASSED, וה-reason הוא
        # הסיבה שהשרת סיווג (cable/disk). בלי תשובה: אדום מדלג, כתום כותב.
        _pf_def=rescue
        if _pf_c=$(disk_failure_cause "$_pf_d"); then _pf_v=failed_last; _pf_r="$_pf_c"; _pf_def=skip; fi
        case "$_pf_v" in
            ok|unchecked)
                smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" pending ""
                _pf_write="$_pf_write $_pf_d"
                ;;
            warn|fail|failed_last)
                _pf_dec=$(smart_choice "$(smart_disk_label "$_pf_d")" \
                    "$([ "$_pf_v" = failed_last ] && echo 'failed the previous clone' || echo "SMART $_pf_v ($_pf_r)")" \
                    "$_pf_d" "$_pf_v" "$_pf_r" "$_pf_def")
                case "$_pf_dec" in
                    rescue)
                        smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" rescue rescue
                        _pf_write="$_pf_write $_pf_d"
                        ;;
                    skip)
                        target_set "$_pf_d" "skipped" "operator skipped ($_pf_v: $_pf_r)"
                        smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" skipped skip
                        ;;
                    replace)
                        target_set "$_pf_d" "replacing" "awaiting disk swap ($_pf_v: $_pf_r)"
                        smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" replacing replace
                        echo "$_pf_d" >> "$RUN_DIR/smart/awaiting_replace"
                        ;;
                esac
                ;;
        esac
    done
    # רווח מוביל מוסר; מילה אחת או ריק חוזרים כמות שהם.
    echo $_pf_write
}

smart_awaiting_replace() {
    # אמת אם המפעיל בחר "החלף" על דיסק כלשהו. אז המכונה מדפיסה את הוראת
    # ההחלפה ומתכבה (כיבוי הוא כל המכונה — אסור להתחיל כתיבה ואז לכבות).
    [ -s "$RUN_DIR/smart/awaiting_replace" ]
}

smart_gate() {
    # $1 = session, $2.. = disks. השער השלם, כדי שמסלולי השחזור יישארו
    # קצרים: מריץ את ה-preflight, מדפיס את רשימת הדיסקים לכתיבה, ומחזיר
    #   0 = כתוב את הרשימה שהודפסה
    #   2 = המפעיל בחר "החלף" -- המכונה מתכבה להחלפה, **לפני** כל כתיבה
    #   1 = לא נשאר מה לכתוב (הכול דולג)
    _sg_ses="$1"; shift
    _sg_list=$(smart_preflight "$_sg_ses" "$@")
    smart_awaiting_replace && { echo "$_sg_list"; return 2; }
    [ -n "$_sg_list" ] || return 1
    echo "$_sg_list"
}

smart_replace_screen() {
    # מסך "כבה -> החלף -> הדלק" לפני שהמכונה מתכבה. כיבוי הוא כל המכונה,
    # ולכן זה קורה לפני שנכתב בייט; ה-job כבר נשמר בשרת ב-disk_event
    # (write_state=replacing), ואחרי הריבוט הסוכן מפקד מחדש לפי port+serial.
    ui_clear; ui_header
    echo "  Replace the flagged disk, then power the machine back on."
    echo
    while read -r _rs_d; do
        echo "    $(smart_disk_label "$_rs_d") -- swap it out"
    done < "$RUN_DIR/smart/awaiting_replace"
    echo
    echo "  Powering off for the swap."
}
