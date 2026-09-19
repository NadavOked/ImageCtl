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
#   ב. ‏install/setup-boot-server.sh --servers-if <כרטיס> — **בלי
#      --deploy-if**: "לא עכשיו" לרשת ההפצה, dnsmasq נשאר כבוי, המנהל
#      בוחר את כרטיס ההפצה מהקונסולה (#1088). השער בסוף המתקין
#      (verify-boot-payload.sh, #332) מוצא את הקבצים משלב א' ומצליח —
#      זו הסיבה לסדר: אילו המתקין רץ קודם, השער שלו היה נכשל על
#      תיקייה ריקה, וכישלון "צפוי" שמקפלים הוא בדיוק עיקרון 5.
#
# כרטיס השרתים — **לא heuristic** (R62 §12): ‏late-command.sh כתב
# ל-/etc/imagectl/installer-nic את הכרטיס ש-d-i עצמו הגדיר (שם + MAC).
# כאן מאמתים שה-MAC עדיין יושב על כרטיס קיים. שלושה מצבים, ולא שניים:
#   FOUND         → ‏--servers-if <הכרטיס שנושא את ה-MAC>
#   UNDECIDABLE   → אין קובץ / d-i לא הגדיר רשת / ה-MAC אינו על אף כרטיס.
#                   **לא מריצים את המתקין בלי הדגל** (הוא נעשה אינטראקטיבי).
#   CHECK_ERROR   → הבדיקה עצמה לא רצה (ip/sysfs). אינו UNDECIDABLE.
# בשניהם האחרונים: ‏/etc/imagectl/firstboot.status + journal, יציאה ≠0
# בגלוי, וההכרעה עוברת לאשף (#910) / למסך. באתחול הבא הסקריפט רץ שוב.
#
# תפקיד (R60): ‏/etc/imagectl/installer-role — standalone (ברירת מחדל) או
# secondary מתפריט האתחול. משני מחייב --primary-url במתקין; בלי
# ‏imagectl.primary=<url> בשורת האתחול — נעצרים בגלוי (state=secondary-
# needs-primary), לא ממציאים כתובת.
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
APP_DIR=/opt/imagectl
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
    log "שלב א': בונה initrd מול $KVER"
    install -d "$HTTP_ROOT"
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --kernel-version "$KVER" \
        --output "$HTTP_ROOT/initrd.img" \
        || fail payload-failed "בניית initrd.img נכשלה (ראה למעלה ביומן)"
    # הגרסה הגרפית — מחשבי שיכפול ובנייה עולים איתה (#835, lab-site2-runbook).
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --with-gui --kernel-version "$KVER" \
        --output "$HTTP_ROOT/initrd.img.gui" \
        || fail payload-failed "בניית initrd.img.gui נכשלה (ראה למעלה ביומן)"
    install -m 0644 "/boot/vmlinuz-$KVER" "$HTTP_ROOT/vmlinuz"
    for f in initrd.img initrd.img.gui vmlinuz; do
        [[ -s "$HTTP_ROOT/$f" ]] || fail payload-failed "$HTTP_ROOT/$f ריק אחרי הבנייה"
    done
    touch "$STAGE_A_STAMP"
    log "שלב א' הושלם: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# שלב ב'.1 — כרטיס השרתים: העובדה מההתקנה, מאומתת מול המכונה עכשיו
# ---------------------------------------------------------------------------
NIC_FILE="$ETC/installer-nic"
[[ -f "$NIC_FILE" ]] || fail network-nic-undecidable \
    "אין $NIC_FILE — ההתקנה לא רשמה איזה כרטיס הוגדר. השלם מהאשף (#910), או ידנית: bash $SRC/install/setup-boot-server.sh --servers-if <כרטיס>"
nic_state=$(sed -n 's/^state=//p' "$NIC_FILE" | head -n1)
nic_mac=$(sed -n 's/^mac=//p' "$NIC_FILE" | head -n1 | tr 'A-F' 'a-f')
nic_name_at_install=$(sed -n 's/^interface=//p' "$NIC_FILE" | head -n1)
[[ "$nic_state" == "configured" && -n "$nic_mac" ]] || fail network-nic-undecidable \
    "ההתקנה לא הגדירה רשת (installer-nic: state=${nic_state:-?}) — לא מנחש כרטיס. השלם מהאשף (#910) או ידנית עם --servers-if"

# הבדיקה עצמה: אם ip/sysfs לא עונים — זה CHECK_ERROR, לא "אין כרטיס".
mapfile -t all_ifaces < <(ip -o link show 2>/dev/null | awk -F': ' '$2 != "lo" {print $2}' | cut -d@ -f1) \
    || fail check-error "ip -o link show נכשל"
(( ${#all_ifaces[@]} )) || fail check-error "ip לא החזיר אף כרטיס (גם לא כזה בלי כתובת) — הבדיקה אינה אמינה"
matches=()
for ifname in "${all_ifaces[@]}"; do
    [[ -r "/sys/class/net/$ifname/address" ]] || fail check-error "אין קריאה של /sys/class/net/$ifname/address"
    if [[ "$(tr 'A-F' 'a-f' < "/sys/class/net/$ifname/address")" == "$nic_mac" ]]; then
        matches+=("$ifname")
    fi
done
case "${#matches[@]}" in
    1) SERVERS_IF="${matches[0]}" ;;
    0) fail network-nic-undecidable \
        "ה-MAC $nic_mac (היה $nic_name_at_install בהתקנה) אינו על אף כרטיס עכשיו (${all_ifaces[*]}) — לא מנחש. השלם מהאשף (#910) או ידנית עם --servers-if" ;;
    *) fail network-nic-undecidable \
        "ה-MAC $nic_mac יושב על יותר מכרטיס אחד (${matches[*]}) — לא מנחש" ;;
esac
log "כרטיס השרתים: $SERVERS_IF (MAC $nic_mac, כפי ש-d-i הגדיר כ-$nic_name_at_install)"

# ---------------------------------------------------------------------------
# שלב ב'.2 — התפקיד, ואז המתקין
# ---------------------------------------------------------------------------
ROLE=standalone; PRIMARY=""
if [[ -f "$ETC/installer-role" ]]; then
    ROLE=$(sed -n 's/^role=//p' "$ETC/installer-role" | head -n1)
    PRIMARY=$(sed -n 's/^primary=//p' "$ETC/installer-role" | head -n1)
fi
ROLE_ARGS=()
case "$ROLE" in
    standalone) ;;
    secondary)
        [[ -n "$PRIMARY" ]] || fail secondary-needs-primary \
            "הותקן כשרת משני אבל בלי imagectl.primary=<url> בשורת האתחול; המתקין מחייב --primary-url. השלם ידנית: bash $SRC/install/setup-boot-server.sh --servers-if $SERVERS_IF --storage-role secondary --primary-url <url>"
        ROLE_ARGS=(--storage-role secondary --primary-url "$PRIMARY") ;;
    *) fail check-error "installer-role לא מוכר: $ROLE" ;;
esac

# ה-repo המקומי שה-late_command השאיר (grub-pc-bin הוסר על ידי d-i בהתקנת
# UEFI; המתקין מתקין אותו מחדש — בלי אינטרנט זה עובד רק מכאן). ראיה
# חיובית לפני שהמתקין רץ: apt רואה מועמד.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail apt-repo-missing "apt-get update נכשל — /var/lib/imagectl/apt-repo חסר או שבור"
cand=$(apt-cache policy grub-pc-bin | awk '/Candidate:/ {print $2}')
[[ -n "$cand" && "$cand" != "(none)" ]] || fail apt-repo-missing \
    "apt אינו מוצא את grub-pc-bin — ה-repo המקומי מ-ISO (/var/lib/imagectl/apt-repo) חסר, והמתקין ייכשל בלי אינטרנט"

log "שלב ב': setup-boot-server.sh --servers-if $SERVERS_IF ${ROLE_ARGS[*]:-} (רשת ההפצה: לא עכשיו)"
bash "$SRC/install/setup-boot-server.sh" --servers-if "$SERVERS_IF" "${ROLE_ARGS[@]}" \
    || fail installer-failed "setup-boot-server.sh נכשל (ראה למעלה ביומן) — לא מסמן כהושלם"

# השער של #332 רץ בתוך המתקין; ההרצה הנוספת כאן היא הראיה של היחידה
# עצמה, על הקוד שהמתקין העתיק אל $APP_DIR.
bash "$APP_DIR/install/verify-boot-payload.sh" --app-dir "$APP_DIR" \
    --http-root "$HTTP_ROOT" --server-url http://127.0.0.1:8080 \
    || fail verify-failed "verify-boot-payload נכשל אחרי ההתקנה"

printf 'state=done\ntimestamp=%s\nservers_if=%s\nrole=%s\n' "$(date -u +%FT%TZ)" "$SERVERS_IF" "$ROLE" > "$STATUS"
touch "$STAMP"
rm -rf "$TMPDIR"
SERVERS_ADDR=$(ip -4 -o addr show dev "$SERVERS_IF" scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)
log "הושלם. הקונסולה: https://${SERVERS_ADDR:-<כתובת $SERVERS_IF>}:8081 (admin/admin, החלפה כפויה). רשת ההפצה: מדף הרשת בקונסולה."
