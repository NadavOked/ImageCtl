#!/bin/sh
#
# ImageCtl — ‏preseed/late_command של ה-ISO (#1139). רץ **בתוך debian-installer**
# (busybox sh, POSIX): ‏/cdrom הוא ה-ISO, ‏/target היא המערכת שזה עתה
# הותקנה. כישלון כאן הוא כישלון של ההתקנה — מסך אדום — ולא "הותקן, אבל".
#
# מה נכתב ל-/target/etc/imagectl — **עובדות שנמדדו כאן**, לא ניחושים
# (R62 §12): ‏firstboot.sh באתחול הראשון קורא אותן ואינו מריץ heuristic.
#   installer-nic    הכרטיס ש-netcfg הגדיר (שם, MAC, dhcp/static) — או none
#   installer-role   standalone/secondary מתפריט האתחול (R60), + primary אם ניתן
#   iso-release.json מניפסט ה-ISO (R61) — איזה תג/קומיט/netinst הותקן
#
# רץ **לפני** שנתקבל copy-config של netcfg אל /target (‏finish-install.d
# ‏07preseed לפני 55netcfg-copy-config), ולכן קורא את /etc/network/interfaces
# של סביבת ההתקנה עצמה.

set -e

log() { logger -t imagectl-late "$*" 2>/dev/null || true; echo "imagectl-late: $*" >&2; }

[ -d /target ] || { log "אין /target"; exit 1; }
[ -f /cdrom/imagectl-src/server/main.py ] || { log "אין /cdrom/imagectl-src — ה-ISO נבנה בלי הקוד"; exit 1; }

ETC=/target/etc/imagectl
mkdir -p "$ETC" /target/opt /target/usr/local/sbin /target/etc/systemd/system

# --- הקוד + יחידת האתחול הראשון ----------------------------------------------
cp -a /cdrom/imagectl-src /target/opt/imagectl-src
cp /cdrom/imagectl/firstboot.sh /target/usr/local/sbin/imagectl-firstboot
chmod 0755 /target/usr/local/sbin/imagectl-firstboot
cp /cdrom/imagectl/imagectl-firstboot.service /target/etc/systemd/system/imagectl-firstboot.service
in-target systemctl enable imagectl-firstboot.service

# --- ה-repo המקומי נשאר על הדיסק --------------------------------------------
# נמדד ב-QEMU (19/09): ‏grub-installer של d-i **מסיר** grub-pc-bin בהתקנת
# UEFI, ושורת ה-cdrom מושבתת בסיום — ואז `apt-get install` של
# setup-boot-server.sh באתחול הראשון (grub-pc-bin ל-PXE של BIOS) לא היה
# מוצא אותה. גם ארגז הכלים מהקונסולה (build_initramfs.sh בלי --skip-apt)
# צריך apt שעובד בלי אינטרנט. לכן הסגירה מ-packages.txt (‏pool/*/imagectl)
# + אינדקסי dists/ נשארים ב-/var/lib/imagectl/apt-repo, כמקור apt מקומי.
REPO=/target/var/lib/imagectl/apt-repo
mkdir -p "$REPO/pool"
for comp in /cdrom/pool/*/imagectl; do
    [ -d "$comp" ] || continue
    c=$(basename "$(dirname "$comp")")
    mkdir -p "$REPO/pool/$c"
    cp -a "$comp" "$REPO/pool/$c/"
done
cp -a /cdrom/dists "$REPO/"
n_src=$(find /cdrom/pool -path '*/imagectl/*.deb' | wc -l)
n_dst=$(find "$REPO/pool" -name '*.deb' | wc -l)
if [ "$n_src" -eq 0 ] || [ "$n_dst" -ne "$n_src" ]; then
    log "העתקת ה-pool נכשלה ($n_dst מתוך $n_src)"; exit 1
fi
printf 'deb [trusted=yes] file:/var/lib/imagectl/apt-repo trixie main contrib\n' \
    > /target/etc/apt/sources.list.d/imagectl-iso.list
log "apt-repo: $n_dst קבצי .deb ב-/var/lib/imagectl/apt-repo"

# --- installer-nic: מה netcfg הגדיר בפועל --------------------------------------
IFACES_FILE=/etc/network/interfaces
[ -f "$IFACES_FILE" ] || IFACES_FILE=/target/etc/network/interfaces
NIC=""; METHOD=""
if [ -f "$IFACES_FILE" ]; then
    # השורה 'iface <שם> inet <dhcp|static>' הראשונה שאינה lo.
    line=$(grep -E '^[[:space:]]*iface[[:space:]]+[^[:space:]]+[[:space:]]+inet[[:space:]]+(dhcp|static)' "$IFACES_FILE" \
           | grep -v -E '^[[:space:]]*iface[[:space:]]+lo[[:space:]]' | head -n1 || true)
    if [ -n "$line" ]; then
        NIC=$(printf '%s\n' "$line" | awk '{print $2}')
        METHOD=$(printf '%s\n' "$line" | awk '{print $4}')
    fi
fi
CHOSEN=$(debconf-get netcfg/choose_interface 2>/dev/null || true)
{
    if [ -n "$NIC" ] && [ -r "/sys/class/net/$NIC/address" ]; then
        printf 'state=configured\ninterface=%s\nmac=%s\nreason=%s\n' \
            "$NIC" "$(cat "/sys/class/net/$NIC/address")" "$METHOD"
    else
        printf 'state=none\nreason=%s\n' "${NIC:+no-mac-for-$NIC}"
    fi
    printf 'd_i_choose_interface=%s\nsource=%s\n' "$CHOSEN" "$IFACES_FILE"
} > "$ETC/installer-nic"
log "installer-nic: $(tr '\n' ' ' < "$ETC/installer-nic")"

# --- installer-role: מתפריט האתחול של ה-ISO (R60) -----------------------------
# ‏/proc/cmdline של המערכת המותקנת יהיה של GRUB שלה — לא של ה-ISO — ולכן
# התפקיד נלכד כאן, בסביבה היחידה שרואה את שורת האתחול של המתקין.
ROLE=$(tr ' ' '\n' < /proc/cmdline | sed -n 's/^imagectl\.role=//p' | head -n1)
PRIMARY=$(tr ' ' '\n' < /proc/cmdline | sed -n 's/^imagectl\.primary=//p' | head -n1)
case "$ROLE" in
    ""|standalone) ROLE=standalone ;;
    secondary) ;;
    *) log "imagectl.role לא מוכר: $ROLE"; exit 1 ;;
esac
printf 'role=%s\nprimary=%s\n' "$ROLE" "$PRIMARY" > "$ETC/installer-role"
log "installer-role: role=$ROLE primary=${PRIMARY:-(none)}"

# --- מניפסט ה-ISO (R61) -----------------------------------------------------
[ -f /cdrom/imagectl-iso.json ] || { log "אין /cdrom/imagectl-iso.json"; exit 1; }
cp /cdrom/imagectl-iso.json "$ETC/iso-release.json"

# --- סיסמת root: מוחלפת בכניסה הראשונה ---------------------------------------
in-target chage -d 0 root

log "late_command הושלם"
