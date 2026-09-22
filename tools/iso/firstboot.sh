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
BUILD_LOG=/var/log/imagectl-firstboot-build.log

log() { printf 'imagectl-firstboot: %s\n' "$*"; }
fail() {
    # state=<מצב> ואז הסבר; הקובץ נקרא על ידי האשף/המסך, היומן על ידי אדם.
    local state="$1"; shift
    install -d "$ETC"
    printf 'state=%s\ntimestamp=%s\nmessage=%s\n' "$state" "$(date -u +%FT%TZ)" "$*" > "$STATUS"
    printf 'imagectl-firstboot: [%s] %s\n' "$state" "$*" >&2
    exit 1
}

[[ -f "$STAMP" ]] && { log "Already completed ($STAMP exists); nothing to do"; exit 0; }
[[ $EUID -eq 0 ]] || fail check-error "Root privileges are required"
[[ -f "$SRC/server/main.py" ]] || fail check-error "Source is missing from $SRC; late-command.sh did not copy it"
install -d "$STAMP_DIR" "$ETC"
touch "$BUILD_LOG"
chmod 0600 "$BUILD_LOG"

# ‏/tmp בדביאן 13 הוא tmpfs; עץ ה-initramfs לפני הדחיסה הוא מאות MB.
export TMPDIR=/var/tmp/imagectl-firstboot
install -d "$TMPDIR"

# ---------------------------------------------------------------------------
# שלב א' — הקרנל וה-initrd
# ---------------------------------------------------------------------------
if [[ -f "$STAGE_A_STAMP" ]]; then
    log "Stage A already completed; continuing to stage B"
else
    # הקרנל המותקן שאינו cloud (‏build_initramfs.sh מסרב ל-cloud, #904).
    KVER=$(find /lib/modules -mindepth 1 -maxdepth 1 -printf '%f\n' \
        | awk '$0 !~ /cloud/' | sort -V | tail -n1) \
        || fail check-error "Could not inspect installed kernels in /lib/modules"
    [[ -n "$KVER" ]] || fail payload-failed "No non-cloud kernel found in /lib/modules; is linux-image-amd64 installed?"
    [[ -f "/boot/vmlinuz-$KVER" ]] || fail payload-failed "Missing /boot/vmlinuz-$KVER"
    # ‏#1125: האריזה reproducible ודורשת SOURCE_DATE_EPOCH; ‏/opt/imagectl-src הוא
    # ‏git archive בלי .git, ולכן הזמן נקרא ממניפסט ה-ISO (source_date_epoch =
    # זמן הקומיט של --ref, build-iso.sh). בלי מניפסט — כישלון בשם, לא `date +%s`
    # שקט שמפרק את השחזוריות (נמדד ב-QEMU 19/09: "'git log' failed").
    EPOCH=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["source_date_epoch"]))' \
        "$ETC/iso-release.json" 2>/dev/null) \
        || fail payload-failed "Missing source_date_epoch in $ETC/iso-release.json; late-command.sh did not copy the manifest"
    log "Stage A: building initramfs for $KVER; detailed output is in $BUILD_LOG"
    install -d "$HTTP_ROOT"
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --kernel-version "$KVER" \
        --source-date-epoch "$EPOCH" --output "$HTTP_ROOT/initrd.img" \
        >>"$BUILD_LOG" 2>&1 \
        || fail payload-failed "initrd.img build failed; see $BUILD_LOG"
    # הגרסה הגרפית — מחשבי שיכפול ובנייה עולים איתה (#835, lab-site2-runbook).
    bash "$SRC/tools/build_initramfs.sh" --skip-apt --with-gui --kernel-version "$KVER" \
        --source-date-epoch "$EPOCH" --output "$HTTP_ROOT/initrd.img.gui" \
        >>"$BUILD_LOG" 2>&1 \
        || fail payload-failed "initrd.img.gui build failed; see $BUILD_LOG"
    install -m 0644 "/boot/vmlinuz-$KVER" "$HTTP_ROOT/vmlinuz"
    for f in initrd.img initrd.img.gui vmlinuz; do
        [[ -s "$HTTP_ROOT/$f" ]] || fail payload-failed "$HTTP_ROOT/$f is empty after the build"
    done
    touch "$STAGE_A_STAMP"
    log "Stage A complete: $(stat -c '%n=%s' "$HTTP_ROOT"/initrd.img "$HTTP_ROOT"/initrd.img.gui "$HTTP_ROOT"/vmlinuz | tr '\n' ' ')"
fi

# ---------------------------------------------------------------------------
# שלב ב' (#1190) — מהתשובות של המתקין החי: הכול כבר נענה על מסך השרת, אין
# אשף. ‏setup-boot-server מהדגלים, אימות המטען, חותמת, ואז ה-DCUI.
# ---------------------------------------------------------------------------
if [[ -f "$ETC/answers" ]]; then
    [[ -f "$SRC/tools/iso/firstboot-answers.sh" ]] || fail check-error "$ETC/answers exists but $SRC/tools/iso/firstboot-answers.sh is missing"
    # shellcheck source=tools/iso/firstboot-answers.sh
    . "$SRC/tools/iso/firstboot-answers.sh"
    firstboot_from_answers
    install -m 0644 "$SRC/install/imagectl-dcui.service" /etc/systemd/system/imagectl-dcui.service
    rm -f /etc/systemd/system/imagectl-dcui.service.d/firstboot.conf
    systemctl daemon-reload || fail check-error "systemctl daemon-reload failed before starting the DCUI"
    systemctl mask getty@tty1.service || fail check-error "Could not mask getty@tty1.service"
    systemctl enable imagectl-dcui.service || fail check-error "Could not enable imagectl-dcui.service"
    systemctl restart imagectl-dcui.service || fail check-error "Could not start imagectl-dcui.service"
    rm -rf "$TMPDIR"
    log "Stage B complete from the installer's answers: the DCUI is on tty1, the console on 8081"
    exit 0
fi

# ---------------------------------------------------------------------------
# שלב ב' (ללא תשובות — שרת בלי מסך) — קורא את עובדות ההתקנה, מדווח פערים,
# ומוסר אותן לאשף בדפדפן כברירות מחדל
# ---------------------------------------------------------------------------
NIC_FILE="$ETC/installer-nic"
ROLE_FILE="$ETC/installer-role"
if [[ ! -f "$NIC_FILE" ]]; then
    log "[network-nic-undecidable] $NIC_FILE is missing; the wizard will require a live-interface selection"
else
    nic_state=$(sed -n 's/^state=//p' "$NIC_FILE" | head -n1)
    nic_mac=$(sed -n 's/^mac=//p' "$NIC_FILE" | head -n1 | tr 'A-F' 'a-f')
    [[ "$nic_state" == "configured" && -n "$nic_mac" ]] \
        || log "[network-nic-undecidable] installer-nic has no configured interface; the wizard will require a selection"
fi

ROLE=standalone; PRIMARY=""
if [[ -f "$ROLE_FILE" ]]; then
    ROLE=$(sed -n 's/^role=//p' "$ROLE_FILE" | head -n1)
    PRIMARY=$(sed -n 's/^primary=//p' "$ROLE_FILE" | head -n1)
fi
case "$ROLE" in
    standalone) ;;
    secondary) [[ -n "$PRIMARY" ]] || log "[secondary-needs-primary] the wizard will require a primary server address" ;;
    *) log "[check-error] unknown installer-role: $ROLE; the wizard will default to standalone" ;;
esac

# ה-repo המקומי שה-late_command השאיר (grub-pc-bin הוסר על ידי d-i בהתקנת
# UEFI; המתקין מתקין אותו מחדש — בלי אינטרנט זה עובד רק מכאן). ראיה
# חיובית לפני שהמתקין רץ: apt רואה מועמד.
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail apt-repo-missing "apt-get update failed; /var/lib/imagectl/apt-repo is missing or invalid"
cand=$(apt-cache policy grub-pc-bin | awk '/Candidate:/ {print $2}')
[[ -n "$cand" && "$cand" != "(none)" ]] || fail apt-repo-missing \
    "apt cannot find grub-pc-bin; the ISO repository is missing and offline setup cannot continue"

temporary_dhcp() {
    local nic="$1" carrier="0" address
    if ! ip link set dev "$nic" up; then
        log "nic $nic: link setup failed"
        return 0
    fi
    if [[ ! -r "/sys/class/net/$nic/carrier" ]]; then
        log "nic $nic: carrier check failed"
        return 0
    fi
    for _ in 1 2 3; do
        carrier=$(<"/sys/class/net/$nic/carrier")
        [[ "$carrier" == "1" ]] && break
        sleep 1
    done
    if [[ "$carrier" != "1" ]]; then
        log "nic $nic: no carrier"
        return 0
    fi
    if ! dhcpcd -4 -1 -t 20 "$nic" >>"$BUILD_LOG" 2>&1; then
        log "nic $nic: no dhcp offer"
        return 0
    fi
    if ! address=$(ip -o -4 address show dev "$nic" scope global | awk 'NR == 1 {print $4}'); then
        log "nic $nic: address check failed"
        return 0
    fi
    # 169.254/16 is dhcpcd's link-local fallback, not an offer (seen on the
    # deploy NIC, 21/09) -- naming it "dhcp" would send the operator there.
    if [[ -z "$address" || "$address" == 169.254.* ]]; then
        log "nic $nic: no dhcp offer"
    else
        log "nic $nic: $address (dhcp)"
    fi
}

dhcp_pids=()
for nic_path in /sys/class/net/*; do
    nic=${nic_path##*/}
    [[ "$nic" == "lo" ]] && continue
    temporary_dhcp "$nic" &
    dhcp_pids+=("$!")
done
for dhcp_pid in "${dhcp_pids[@]}"; do
    wait "$dhcp_pid" || fail check-error "A temporary DHCP worker failed"
done

install -m 0644 "$SRC/install/imagectl-wizard.service" /etc/systemd/system/imagectl-wizard.service
install -m 0644 "$SRC/install/imagectl-wizard-rerun.service" /etc/systemd/system/imagectl-wizard-rerun.service
install -m 0644 "$SRC/install/imagectl-dcui.service" /etc/systemd/system/imagectl-dcui.service
install -m 0644 "$SRC/install/imagectl-installer-gui.service" /etc/systemd/system/imagectl-installer-gui.service
install -d /etc/systemd/system/imagectl-dcui.service.d
printf '[Service]\nWorkingDirectory=/opt/imagectl-src\n' \
    > /etc/systemd/system/imagectl-dcui.service.d/firstboot.conf
printf 'state=wizard-running\ntimestamp=%s\nrole_default=%s\n' "$(date -u +%FT%TZ)" "$ROLE" > "$STATUS"
systemctl daemon-reload || fail check-error "systemctl daemon-reload failed before starting the installer UI"
systemctl mask getty@tty1.service || fail check-error "Could not mask getty@tty1.service"
systemctl enable imagectl-dcui.service || fail check-error "Could not enable imagectl-dcui.service"
systemctl start imagectl-wizard || fail wizard-failed "imagectl-wizard did not start; see journalctl -u imagectl-wizard"
systemctl is-active --quiet imagectl-wizard || fail wizard-failed "imagectl-wizard is not active after systemctl start"

has_keyboard=0
for event in /dev/input/event*; do
    [[ -e "$event" ]] || continue
    if udevadm info --query=property --name="$event" | grep -qx 'ID_INPUT_KEYBOARD=1'; then
        has_keyboard=1
        break
    fi
done
if [[ -e /dev/fb0 && "$has_keyboard" == 1 ]]; then
    systemctl start imagectl-installer-gui.service \
        || fail check-error "Could not start imagectl-installer-gui.service"
    systemctl is-active --quiet imagectl-installer-gui.service \
        || fail check-error "imagectl-installer-gui.service is not active after start"
    log "installer gui: started on tty1"
else
    systemctl restart imagectl-dcui.service || fail check-error "Could not start imagectl-dcui.service"
    systemctl is-active --quiet imagectl-dcui.service || fail check-error "imagectl-dcui.service is not active after restart"
    log "installer gui: no framebuffer/keyboard, DCUI only"
fi
rm -rf "$TMPDIR"
log "Stage B is ready: use tty1 or the HTTPS wizard on port 8081"
