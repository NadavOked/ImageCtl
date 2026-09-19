#!/usr/bin/env bash
#
# ImageCtl — האתחול הראשון אחרי התקנה מה-ISO (#1139; R25 §2.4, R62, R60).
#
# ‏late-command.sh מתקין אותו כ-/usr/local/sbin/imagectl-firstboot, ו-
# ‏imagectl-firstboot.service מריץ אותו. מה שהמתקין (d-i) לא יכול לעשות
# מתוך ה-chroot — לבנות initrd מול הקרנל המותקן ולהרים שירותים — קורה
# כאן, על המערכת האמיתית.
#
# שני שלבים, בסדר הזה בכוונה:
#   א. ‏initrd.img + initrd.img.gui + vmlinuz אל /srv/imagectl/boot —
#      מתוך /opt/imagectl-src, עם --skip-apt (החבילות כבר מותקנות
#      מה-ISO; packages.txt הוא האיחוד, ו-tests/test_iso_build.py שומר).
#      גם שרת **משני** בונה בעצמו (R60): הסיבה לא לבנות — קרנל cloud —
#      אינה תקפה כשה-ISO מביא linux-image-amd64.
#   ב. האשף הזמני ב-HTTPS 8081. הוא מקבל את עובדות הכרטיס והתפקיד דרך
#      הקבצים שכתב d-i, והמפעיל מאשר או משנה אותן. רק בלחיצה על "החל"
#      הוא מריץ את setup-boot-server.sh; רשת ההפצה נשארת לקונסולה (#1088).
#
# כרטיס השרתים — **לא heuristic** (R62 §12): ‏late-command.sh כתב
# ל-/etc/imagectl/installer-nic את הכרטיס ש-d-i עצמו הגדיר (שם + MAC).
# כאן מאמתים שה-MAC עדיין יושב על כרטיס קיים. שלושה מצבים, ולא שניים:
#   FOUND         → ‏--servers-if <הכרטיס שנושא את ה-MAC>
#   UNDECIDABLE   → אין קובץ / d-i לא הגדיר רשת / ה-MAC אינו על אף כרטיס.
#                   **לא מריצים את המתקין בלי הדגל** (הוא נעשה אינטראקטיבי).
#   CHECK_ERROR   → הבדיקה עצמה לא רצה (ip/sysfs). אינו UNDECIDABLE.
# ‏network-nic-undecidable / check-error אינם עוד סיבה לעצור: האשף מציג
# את הרשימה החיה ומחייב בחירה. הם נכתבים ביומן בשם, לא מתקפלים לבחירה.
#
# תפקיד (R60): ‏/etc/imagectl/installer-role — standalone או secondary.
# secondary בלי כתובת ראשי הוא secondary-needs-primary באשף: השדה נשאר
# ריק ונדרש למלאו; אין כתובת מומצאת ואין חסימה לפני שהמפעיל רואה מסך.
#
# הכישלון גלוי: ‏`systemctl status imagectl-firstboot` אדום, והיומן
# אומר באיזה שלב. אין Restart ואין `|| true`.

set -euo pipefail

STAMP_DIR=/var/lib/imagectl
STAMP="$STAMP_DIR/.firstboot-done"
STAGE_A_STAMP="$STAMP_DIR/.firstboot-payload-done"
ETC=/etc/imagectl
STATUS="$ETC/firstboot.status"
SRC=/opt/imagectl-src
HTTP_ROOT=/srv/imagectl/boot

log() { printf 'imagectl-firstboot: %s\n' "$*"; }
fail() {
    # state=<מצב> ואז הסבר; הקובץ נקרא על ידי האשף/המסך, היומן על ידי אדם.
    local state="$1"; shift
    install -d "$ETC"
    printf 'state=%s\ntimestamp=%s\nmessage=%s\n' "$state" "$(date -u +%FT%TZ)" "$*" > "$STATUS"
    printf 'imagectl-firstboot: [%s] %s\n' "$state" "$*" >&2
    exit 1
}

[[ -f "$STAMP" ]] && { log "כבר רץ ($STAMP קיים) — לא עושה דבר"; exit 0; }
[[ $EUID -eq 0 ]] || fail check-error "צריך root"
[[ -f "$SRC/server/main.py" ]] || fail check-error "אין קוד ב-$SRC — late-command.sh לא העתיק אותו"
install -d "$STAMP_DIR" "$ETC"

# ‏/tmp בדביאן 13 הוא tmpfs; עץ ה-initramfs לפני הדחיסה הוא מאות MB.
export TMPDIR=/var/tmp/imagectl-firstboot
install -d "$TMPDIR"

# ---------------------------------------------------------------------------
# שלב א' — הקרנל וה-initrd
# ---------------------------------------------------------------------------
if [[ -f "$STAGE_A_STAMP" ]]; then
    log "שלב א' כבר הושלם — מדלג לשלב ב'"
else
    # הקרנל המותקן שאינו cloud (‏build_initramfs.sh מסרב ל-cloud, #904).
    KVER=$(find /lib/modules -mindepth 1 -maxdepth 1 -printf '%f\n' | grep -v cloud | sort -V | tail -n1 || true)
    [[ -n "$KVER" ]] || fail payload-failed "אין קרנל לא-cloud ב-/lib/modules — linux-image-amd64 לא הותקן?"
    [[ -f "/boot/vmlinuz-$KVER" ]] || fail payload-failed "אין /boot/vmlinuz-$KVER"
    # ‏#1125: האריזה reproducible ודורשת SOURCE_DATE_EPOCH; ‏/opt/imagectl-src הוא
    # ‏git archive בלי .git, ולכן הזמן נקרא ממניפסט ה-ISO (source_date_epoch =
    # זמן הקומיט של --ref, build-iso.sh). בלי מניפסט — כישלון בשם, לא `date +%s`
    # שקט שמפרק את השחזוריות (נמדד ב-QEMU 19/09: "'git log' failed").
    EPOCH=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["source_date_epoch"]))' \
        "$ETC/iso-release.json" 2>/dev/null) \
        || fail payload-failed "אין source_date_epoch ב-$ETC/iso-release.json — late-command.sh לא העתיק את המניפסט?"
    log "שלב א': בונה initrd מול $KVER (SOURCE_DATE_EPOCH=$EPOCH)"
    install -d "$HTTP_ROOT"
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --kernel-version "$KVER" \
        --source-date-epoch "$EPOCH" --output "$HTTP_ROOT/initrd.img" \
        || fail payload-failed "בניית initrd.img נכשלה (ראה למעלה ביומן)"
    # הגרסה הגרפית — מחשבי שיכפול ובנייה עולים איתה (#835, lab-site2-runbook).
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --with-gui --kernel-version "$KVER" \
        --source-date-epoch "$EPOCH" --output "$HTTP_ROOT/initrd.img.gui" \
        || fail payload-failed "בניית initrd.img.gui נכשלה (ראה למעלה ביומן)"
    install -m 0644 "/boot/vmlinuz-$KVER" "$HTTP_ROOT/vmlinuz"
    for f in initrd.img initrd.img.gui vmlinuz; do
        [[ -s "$HTTP_ROOT/$f" ]] || fail payload-failed "$HTTP_ROOT/$f ריק אחרי הבנייה"
    done
    touch "$STAGE_A_STAMP"
    log "שלב א' הושלם: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# שלב ב' — קורא את עובדות d-i, מדווח פערים, ומוסר אותן לאשף כברירות מחדל
# ---------------------------------------------------------------------------
NIC_FILE="$ETC/installer-nic"
ROLE_FILE="$ETC/installer-role"
if [[ ! -f "$NIC_FILE" ]]; then
    log "[network-nic-undecidable] אין $NIC_FILE — האשף יחייב בחירה מהרשימה החיה"
else
    nic_state=$(sed -n 's/^state=//p' "$NIC_FILE" | head -n1)
    nic_mac=$(sed -n 's/^mac=//p' "$NIC_FILE" | head -n1 | tr 'A-F' 'a-f')
    [[ "$nic_state" == "configured" && -n "$nic_mac" ]] \
        || log "[network-nic-undecidable] installer-nic אינו מכיל כרטיס מוגדר — האשף יחייב בחירה"
fi

ROLE=standalone; PRIMARY=""
if [[ -f "$ROLE_FILE" ]]; then
    ROLE=$(sed -n 's/^role=//p' "$ROLE_FILE" | head -n1)
    PRIMARY=$(sed -n 's/^primary=//p' "$ROLE_FILE" | head -n1)
fi
case "$ROLE" in
    standalone) ;;
    secondary) [[ -n "$PRIMARY" ]] || log "[secondary-needs-primary] האשף יציג שרת משני עם כתובת ראשי ריקה" ;;
    *) log "[check-error] installer-role לא מוכר: $ROLE — האשף יציג שרת ראשי כברירת מחדל" ;;
esac

# ה-repo המקומי שה-late_command השאיר (grub-pc-bin הוסר על ידי d-i בהתקנת
# UEFI; המתקין מתקין אותו מחדש — בלי אינטרנט זה עובד רק מכאן). ראיה
# חיובית לפני שהמתקין רץ: apt רואה מועמד.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail apt-repo-missing "apt-get update נכשל — /var/lib/imagectl/apt-repo חסר או שבור"
cand=$(apt-cache policy grub-pc-bin | awk '/Candidate:/ {print $2}')
[[ -n "$cand" && "$cand" != "(none)" ]] || fail apt-repo-missing \
    "apt אינו מוצא את grub-pc-bin — ה-repo המקומי מ-ISO (/var/lib/imagectl/apt-repo) חסר, והמתקין ייכשל בלי אינטרנט"

install -m 0644 "$SRC/install/imagectl-wizard.service" /etc/systemd/system/imagectl-wizard.service
printf 'state=wizard-running\ntimestamp=%s\nrole_default=%s\n' "$(date -u +%FT%TZ)" "$ROLE" > "$STATUS"
systemctl daemon-reload || fail check-error "systemctl daemon-reload נכשל לפני הפעלת האשף"
systemctl start imagectl-wizard || fail wizard-failed "imagectl-wizard לא עלה; ראה journalctl -u imagectl-wizard"
systemctl is-active --quiet imagectl-wizard || fail wizard-failed "imagectl-wizard אינו active אחרי systemctl start"
rm -rf "$TMPDIR"
log "שלב ב' ממתין למפעיל: אשף HTTPS על פורט 8081; ברירות המחדל נקראות מ-$NIC_FILE ומ-$ROLE_FILE"
