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
#
# הקובץ הזה: קריאת smartctl, הסיווג והמטמון לכל דיסק (מה שה-hello וה-kiosk
# צריכים). השער מול המפעיל -- תפריט, disk_event ומסך ההחלפה -- ב-smartgate.sh
# (#1211: פוצל לפני קיר 300 השורות).

SMARTCTL="${SMARTCTL:-smartctl}"

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
