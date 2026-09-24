# smartgate.sh -- the SMART gate before writing (#652): the operator's choice,
# disk_event, and the power-off-and-swap screen. POSIX sh (busybox ash).
# נשען על smart.sh (smart_probe/smart_field -- הקריאה, הסיווג והמטמון), שנטען
# לפניו; פוצל ממנו ב-#1211 לפני קיר 300 השורות, בלי שינוי התנהגות.

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

smart_replace_stop() {
    # smart_gate rc=2, on either restore path (drawers, #1222; station,
    # #1224): the screen, then the power-off -- before a byte is written.
    # Never returns.
    smart_replace_screen
    [ "${IMAGECTL_TEST:-0}" = "1" ] && exit 0
    finish_and_stop
    # finish_and_stop returns only when Wake-on-LAN was not armed (#587).
    # Nothing failed and the disk still has to come out, so this is not
    # "skipped" / "no drawer completed", and not back into the round either
    # (the next hello hands the same round back, with this progress_loop
    # still running): hold on this screen, heartbeat on (#64), for a
    # power-off by hand.
    log "smart: replace chosen -- wol not armed, staying powered on"
    echo "  Wake-on-LAN could not be armed: power off by hand for the swap."
    HOLD_PROMPT="Replace the flagged disk: power off by hand"
    hold_watch hold_beat
}
