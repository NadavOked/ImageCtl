# crcdelta.sh -- שגיאות CRC (מאפיין 199) כהפרש בסבב, לא כמונה מצטבר (#872).
# POSIX sh (busybox ash).
#
# המונה המצטבר מספר על ההיסטוריה של הכבלים ולא על הדיסק: על 6 דיסקי הכיתה
# הוא 137–55,242 (15/09), והדיסק עם 55,242 שיכפל 20GB מושלם. מה שאומר משהו
# על *הסבב הזה* הוא בכמה הוא עלה בו. הקריאה הראשונה היא המטמון של
# smart_probe (השער, לפני הכתיבה); השנייה כאן -- פעם אחת אחרי סיום/כשל
# הכתיבה, לא בלולאה. ההפרש אינו צובע ואינו עוצר: הוא השדה `crc_delta` בדוח
# הסיום (interfaces.md סעיף 4), והקונסולה מציגה "CRC +N · לבדוק כבל".

crc_delta_measure() {
    # $@ = disks. כותב targets/<dev>/crc_delta לכל דיסק ששתי הקריאות שלו
    # הצליחו; אחרת הקובץ **לא נכתב** -- "לא נקרא" אינו 0 (עיקרון 5).
    # ‏target_init מוחק את הקובץ, ולכן ערך ישן לא שורד סבב חדש.
    for _cd_d in "$@"; do
        # המטמון נושא 199 רק כש-smartctl באמת קרא את הדיסק -- לא על unchecked
        # מכשל פתיחה / אין smartctl / אין SMART, שם ה-0 שנשמר אינו מדידה.
        case "$(smart_field "$_cd_d" 2)" in
            ok|health_failed|uncorrectable|pending_sectors|no_health) ;;   # #1366: the F1 reasons read it too
            *) log "$_cd_d: crc delta not measured (no baseline)"; continue ;;
        esac
        _cd_out="$RUN_DIR/smart/$_cd_d.after"
        # ‏#1367: בתקרה (waits.sh) -- אחרי הכתיבה ולפני הדוח, דיסק תקוע אינו
        # עוצר את report_final. bit 1 (rc & 2) = פתיחה נכשלה -> דרך SAT, אותו
        # כלל כמו smart_probe.
        capped_run "$SMARTCTL_MAX_S" "$_cd_out" "$SMARTCTL" -A -n never "$DEVROOT/$_cd_d"; _cd_rc=$?
        [ "$CAPPED_TIMED_OUT" = 0 ] && [ "$((_cd_rc / 2 % 2))" -eq 1 ] &&
            { capped_run "$SMARTCTL_MAX_S" "$_cd_out" "$SMARTCTL" -d sat -A -n never "$DEVROOT/$_cd_d"; _cd_rc=$?; }
        if [ "$CAPPED_TIMED_OUT" = 1 ]; then
            log "$_cd_d: crc delta not measured (smartctl did not answer within ${SMARTCTL_MAX_S}s)"
            continue
        fi
        if [ "$((_cd_rc / 2 % 2))" -eq 1 ]; then
            log "$_cd_d: crc delta not measured (smartctl could not open the disk)"
            continue
        fi
        _cd_after=$(_smart_int "$_cd_out" 199 UDMA_CRC_Error_Count)
        echo $((_cd_after - $(smart_field "$_cd_d" 6))) > "$RUN_DIR/targets/$_cd_d/crc_delta"
    done
}

crc_delta_after() {
    # $@ = disks. לשימוש באותה שורה אחרי פקודת הכתיבה,
    # ‏`{ run_restore ...; crc_delta_after sda; }`: מודד, ומחזיר את קוד
    # היציאה של הכתיבה כמות שהוא -- הסיום נמדד בהצלחה ובכשל כאחד, בלי לשנות
    # את ההכרעה. (‏$? בכניסה לפונקציה הוא של הפקודה שקדמה לה.)
    _cda_rc=$?
    crc_delta_measure "$@"
    return "$_cda_rc"
}
