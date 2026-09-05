#!/bin/bash
# build_initramfs.sh -- builds the ImageCtl agent initramfs on a Debian
# machine (the boot server itself is fine). The output is a cpio.gz that
# GRUB loads after the distro kernel; nothing in it needs signing.
#
# Usage:
#   sudo ./tools/build_initramfs.sh [--output FILE] [--kernel-version VER]
#                                   [--firmware DIR]... [--ssh-key FILE]
#                                   [--with-gui] [--skip-apt]
#
# --ssh-key packs a public key as the technician's authorized_keys. Without
# it dropbear has nobody to let in and never listens; with it, it still
# listens only when the kernel line carries imagectl.debug=1.
#
# The agent scripts are taken from the agent/ directory next to this
# repository checkout.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
AGENT_DIR="$SCRIPT_DIR/../agent"
OUTPUT="$PWD/imagectl-initramfs.cpio.gz"
KVER="$(uname -r)"
SKIP_APT=0
WITH_GUI=0
SSH_KEY_FILE=""
FIRMWARE_DIRS=("rtl_nic")

while [ $# -gt 0 ]; do
    case "$1" in
        --output)         OUTPUT="$2"; shift 2 ;;
        --kernel-version) KVER="$2"; shift 2 ;;
        --firmware)       FIRMWARE_DIRS+=("$2"); shift 2 ;;
        --ssh-key)        SSH_KEY_FILE="$2"; shift 2 ;;
        --with-gui)       WITH_GUI=1; shift ;;
        --skip-apt)       SKIP_APT=1; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (file ownership in the cpio)" >&2; exit 1; }
[ -f "$AGENT_DIR/init" ] || { echo "agent/ not found next to tools/" >&2; exit 1; }

# The ssh key is checked here, before apt and the compiles: a typo in the
# path should cost a second, not the whole build.
if [ -n "$SSH_KEY_FILE" ]; then
    [ -f "$SSH_KEY_FILE" ] \
        || { echo "ssh key file not found: $SSH_KEY_FILE" >&2; exit 1; }
    if grep -q 'PRIVATE KEY' "$SSH_KEY_FILE"; then
        echo "$SSH_KEY_FILE is a PRIVATE key -- pass the .pub" >&2
        exit 1
    fi
    if ! grep -qE '^(ssh-|ecdsa-|sk-)' "$SSH_KEY_FILE"; then
        echo "$SSH_KEY_FILE does not look like a public key" >&2
        exit 1
    fi
fi

# --- הצהרת הקיוסק (--with-gui) ------------------------------------------------
# שתי רשימות ולא אחת, כי הן נכשלות בשתי נקודות שונות בזמן: חבילה שאין
# ממנה מועמד ב-apt נתפסת **לפני** ש-apt רץ, ונתיב שלא הופיע על הדיסק
# נתפס אחרי ההתקנה. עד כאן לא נבדקה אף אחת מהן.
GUI_PACKAGES=(cage chromium seatd libgl1-mesa-dri
              fonts-ibm-plex fontconfig-config libinput-bin xkb-data)

# ‏`truetype` ולא `opentype`: ‏fonts-ibm-plex בדביאן מתקינה
# ל-`/usr/share/fonts/truetype/ibm-plex`, והנתיב שהיה כאן מעולם לא היה
# קיים. ‏`if [ -d "$dir" ]` דילג עליו בלי מילה, ולכן קיוסק בלי גופן עברי
# נראה כמו בנייה שהצליחה — אותו דפוס כמו ‏`[ -e ] && cp` של ה-gconv
# ב-#33. הרשימה הזאת נבדקת, ומה שאין בו עוצר את הבנייה (#120).
#
# ‏/etc/fonts ו-/usr/share/fontconfig הם הזוג ולא אחד מהם: קובצי
# ‏conf.d הם קישורים סימבוליים אל conf.avail, וקישור יתום בתוך
# ה-initramfs שקול לקובץ חסר. בלי תצורת fontconfig כרומיום אינו מוצא
# **שום** גופן — גם כשהקובץ ארוז לידו — והעברית יוצאת ריבועים.
GUI_PATHS=(/usr/lib/chromium
           /usr/share/fonts/truetype/ibm-plex
           /etc/fonts                  /usr/share/fontconfig
           /usr/lib/x86_64-linux-gnu/dri
           /usr/share/libinput         /usr/share/X11/xkb)

# החבילות נבדקות כאן, לפני apt ולפני הקומפילציות, מאותו טעם כמו מפתח
# ה-SSH למעלה. ‏`apt-get install` על חבילה שאינה בקומפוננטות המופעלות
# עונה `E: Unable to locate package` ויוצא 100 — הודעה שאינה מבדילה בין
# "אין חבילה כזאת בדביאן" לבין "היא קיימת, אבל ה-sources.list כאן לא
# מכיל את הקומפוננטה שלה". ההבדל הוא כל התשובה: ‏`fonts-ibm-plex`
# **קיימת** בדביאן 13 (‏6.1.1-1), ב-contrib, וה-sources.list של השרת
# מכיל `main non-free-firmware` בלבד (#120).
if [ "$WITH_GUI" -eq 1 ] && [ "$SKIP_APT" -eq 0 ]; then
    _no_candidate=""
    for _pkg in "${GUI_PACKAGES[@]}"; do
        # ‏`apt-cache policy` על חבילה לא מוכרת יוצא 0 עם פלט ריק, ולכן
        # קוד היציאה אינו הראיה — שורת ה-Candidate היא. גם `(none)`
        # (מוכרת באינדקס, בלי גרסה בת-התקנה) הוא "אין".
        _cand=$(apt-cache policy "$_pkg" 2>/dev/null | sed -n 's/^ *Candidate: *//p')
        case "$_cand" in
            ""|"(none)") _no_candidate="$_no_candidate $_pkg" ;;
        esac
    done
    if [ -n "$_no_candidate" ]; then
        echo "--with-gui: apt has no installable candidate for:$_no_candidate" >&2
        echo "A package that exists in Debian but sits in a component this machine" >&2
        echo "does not enable looks exactly like one that does not exist at all." >&2
        echo "fonts-ibm-plex is in contrib (Debian 13: 6.1.1-1); a sources.list" >&2
        echo "carrying only main will never find it. Enable the component it needs," >&2
        echo "run apt-get update, and build again." >&2
        exit 1
    fi
fi

# Binaries the agent scripts call. tests/test_agent.py cross-checks this
# list against the actual commands in agent/ -- update both together.
BINARIES=(curl jq zstd pv sgdisk blockdev sha256sum od hdparm ntfsresize openssl
          ntfs-3g umount blkid df mount stty
          e2fsck resize2fs btrfs
          udp-receiver partclone.ntfs partclone.fat partclone.ext4
          partclone.btrfs partclone.dd
          dropbear dropbearkey)

if [ "$SKIP_APT" -eq 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y --no-install-recommends \
        busybox-static zstd partclone udpcast gdisk curl jq pv \
        ntfs-3g libhivex-dev hdparm coreutils util-linux openssl \
        e2fsprogs btrfs-progs cpio gzip gcc libc6-dev dropbear-bin
fi

# ‏`$TMPDIR` ולא `/tmp` קשיח: עץ הבנייה הוא מאות MB לפני הדחיסה, ועל
# שרת עם `/tmp` ב-tmpfs זה אומר לכתוב מאות MB אל תוך ה-RAM. תבנית
# ‏`mktemp` שמתחילה ב-`/` מתעלמת מ-TMPDIR לגמרי, ולכן `TMPDIR=...`
# מהקורא לא עשה דבר.
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/imagectl-initramfs.XXXXXX")
trap 'rm -rf "$ROOT"' EXIT

mkdir -p "$ROOT"/{bin,sbin,usr/bin,usr/sbin,usr/lib/imagectl,etc/imagectl,proc,sys,dev,run,tmp,lib}

# --- busybox and its applets -------------------------------------------------

BUSYBOX=$(command -v busybox)
cp "$BUSYBOX" "$ROOT/bin/busybox"
for applet in $("$BUSYBOX" --list); do
    case "$applet" in
        busybox) continue ;;
    esac
    ln -sf busybox "$ROOT/bin/$applet"
done

# --- real binaries and their libraries ---------------------------------------

copy_libs() {
    # Pull in every shared library the binary needs, keeping paths.
    # `|| true`: some "binaries" are shell wrappers (hivexget on Debian 13)
    # and ldd exits nonzero on them -- under pipefail that killed the whole
    # build, silently. Found by the VM lab (issue #12).
    { ldd "$1" 2>/dev/null || true; } \
    | awk '/=>/ { print $3 } /^\s*\// { print $1 }' \
    | while read -r lib; do
        [ -f "$lib" ] || continue
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -Ln "$lib" "$ROOT$lib" 2>/dev/null || true
    done
}

copy_bin() {
    local src dst
    src=$(command -v "$1") || { echo "missing binary: $1" >&2; exit 1; }
    dst="$ROOT/usr/bin/$(basename "$src")"
    cp -L "$src" "$dst"
    copy_libs "$src"
}

for b in "${BINARIES[@]}"; do
    copy_bin "$b"
done

# The dynamic loader and the NSS libraries curl resolves hostnames with.
for extra in /lib64/ld-linux-x86-64.so.2 \
             /lib/x86_64-linux-gnu/libnss_dns.so.2 \
             /lib/x86_64-linux-gnu/libnss_files.so.2 \
             /lib/x86_64-linux-gnu/libresolv.so.2; do
    if [ -e "$extra" ]; then
        mkdir -p "$ROOT$(dirname "$extra")"
        cp -Ln "$extra" "$ROOT$extra" 2>/dev/null || true
    fi
done
# passwd/group as well as hosts: dropbear resolves the account it hands
# the session to through NSS, and a database with no line here is the #33
# failure mode again -- the lookup fails and the login is refused with
# nothing useful in the log.
cat > "$ROOT/etc/nsswitch.conf" << 'EOF'
hosts:  files dns
passwd: files
group:  files
shadow: files
EOF

# --- the agent ---------------------------------------------------------------

install -m 0755 "$AGENT_DIR/init"           "$ROOT/init"
install -m 0755 "$AGENT_DIR/imagectl-agent" "$ROOT/usr/bin/imagectl-agent"
install -m 0644 "$AGENT_DIR"/lib/*.sh       "$ROOT/usr/lib/imagectl/"

# fanout: the isolated multi-drawer writer. A shell cannot do non-blocking
# writes to several drives at once, and `tee` would let one stalled drive
# halt the whole machine.
echo "compiling fanout..."
gcc -O2 -Wall -Wextra -static -o "$ROOT/usr/bin/fanout" "$AGENT_DIR/fanout.c"

# hivewrite: single-value registry reads/writes that preserve the key's
# other values (#33). Links against libhivex (no static build shipped),
# so its shared libraries ride along like any packed binary's.
echo "compiling hivewrite..."
gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/hivewrite" "$AGENT_DIR/hivewrite.c" -lhivex
copy_libs "$ROOT/usr/bin/hivewrite"

# libhivex converts registry key names with glibc's iconv, and iconv
# loads its converters at runtime from the gconv directory. Without
# these files every hivex key lookup fails as "key not found" -- which
# is how the hostname write silently did nothing for months (#33).
# Registry names are UTF-16 or Latin-1, so only those two converters
# (plus the module index) are needed.
GCONV_SRC=$(dirname "$(find /usr/lib -name gconv-modules -path '*/gconv/*' 2>/dev/null | head -1)")
[ -d "$GCONV_SRC" ] || { echo "glibc gconv directory not found" >&2; exit 1; }
mkdir -p "$ROOT$GCONV_SRC"
# The converters are required, not best-effort. `[ -e ] && cp` skipped a
# missing one without a word, so a glibc that moved ISO8859-1.so would
# have produced an initramfs that builds clean and writes no hostname --
# #33 all over again. A missing converter is a failed build.
for f in gconv-modules ISO8859-1.so UTF-16.so; do
    [ -e "$GCONV_SRC/$f" ] || { echo "gconv converter missing: $GCONV_SRC/$f" >&2; exit 1; }
    cp -L "$GCONV_SRC/$f" "$ROOT$GCONV_SRC/$f"
    [ -s "$ROOT$GCONV_SRC/$f" ] || { echo "gconv converter empty: $f" >&2; exit 1; }
done
# The cache is a genuine optimisation -- glibc falls back to gconv-modules.
if [ -e "$GCONV_SRC/gconv-modules.cache" ]; then
    cp -L "$GCONV_SRC/gconv-modules.cache" "$ROOT$GCONV_SRC/gconv-modules.cache"
fi

cat > "$ROOT/etc/imagectl/udhcpc.script" << 'EOF'
#!/bin/sh
# Minimal udhcpc hook: configure the interface, default route and DNS.
case "$1" in
    deconfig)
        ip addr flush dev "$interface" 2>/dev/null
        ip link set "$interface" up
        ;;
    bound|renew)
        ip addr flush dev "$interface" 2>/dev/null
        ifconfig "$interface" "$ip" netmask "${subnet:-255.255.255.0}"
        [ -n "${router:-}" ] && route add default gw "${router%% *}" 2>/dev/null
        : > /etc/resolv.conf
        for d in ${dns:-}; do echo "nameserver $d" >> /etc/resolv.conf; done
        ;;
esac
exit 0
EOF
chmod 0755 "$ROOT/etc/imagectl/udhcpc.script"

# --- ssh for the technician (#44) --------------------------------------------
# The serial pipe carried two machines and will not carry twenty. dropbear
# rides in every image, but it listens only when the agent starts it, and
# the agent starts it only behind imagectl.debug=1 -- the same gate as the
# technician shell -- and only if a key was packed here. A classroom
# station therefore listens on nothing.

# The one account in the image. Something has to answer getpwnam, and the
# password field is locked on purpose: this file travels inside an
# initramfs served over plain HTTP, so any password in it is a published
# one. Authentication is by key, or not at all.
printf 'root:x:0:0:root:/root:/bin/sh\n' > "$ROOT/etc/passwd"
printf 'root:x:0:\n'                     > "$ROOT/etc/group"
printf 'root:*:19000:0:99999:7:::\n'     > "$ROOT/etc/shadow"
chmod 0600 "$ROOT/etc/shadow"
mkdir -p "$ROOT/root"
chmod 0700 "$ROOT/root"

if [ -n "$SSH_KEY_FILE" ]; then
    install -m 0600 "$SSH_KEY_FILE" "$ROOT/etc/imagectl/authorized_keys"
    echo "ssh: authorized_keys packed from $SSH_KEY_FILE"
else
    echo "ssh: no --ssh-key given -- dropbear will not listen at all"
fi
# No host key is generated here. One baked at build time would be the same
# private key on every station, in a file anyone on the VLAN can download,
# and it would make two runs of this script produce different images. The
# agent makes one in the tmpfs on every boot instead (agent/lib/sshd.sh).

# --- the kiosk (optional): the build machine's graphical face ----------------
# The station page carries the console's design, Hebrew and RTL included --
# things the Linux text console cannot render. cage is a bare Wayland
# compositor that runs exactly one fullscreen app; Chromium in kiosk mode
# is that app. Adds roughly 350MB to the image, so it is opt-in: classroom
# stations do not need it, the one build machine does.

if [ "$WITH_GUI" -eq 1 ]; then
    if [ "$SKIP_APT" -eq 0 ]; then
        apt-get install -y --no-install-recommends "${GUI_PACKAGES[@]}"
    fi
    echo "packing the kiosk (cage + chromium)..."
    copy_bin cage
    copy_bin chromium
    copy_bin seatd
    # רכיבי רינדור וגופנים — נתיבים שלמים, לא בינארי בודד. נתיב מוצהר
    # שאינו כאן עוצר את הבנייה; ‏`if [ -d "$dir" ]` דילג עליו בשקט,
    # וזה מה שהסתיר את נתיב הגופן השגוי (#120). כולם נאספים לפני
    # ההודעה, כדי שלא יתגלו אחד-אחד בשש בנייות.
    _no_path=""
    for _p in "${GUI_PATHS[@]}"; do
        [ -d "$_p" ] || _no_path="$_no_path $_p"
    done
    if [ -n "$_no_path" ]; then
        echo "--with-gui: declared paths missing after install:$_no_path" >&2
        echo "The kiosk needs every one of them; a skipped path is a kiosk that" >&2
        echo "starts and renders nothing, which looks like a clean build." >&2
        exit 1
    fi
    for _p in "${GUI_PATHS[@]}"; do
        mkdir -p "$ROOT$_p"
        cp -a "$_p/." "$ROOT$_p/"
    done

    cat > "$ROOT/usr/bin/imagectl-kiosk" << 'EOF'
#!/bin/sh
# imagectl-kiosk <url> -- one fullscreen browser, nothing else.
# seatd gives the compositor access to the display and input devices.
export XDG_RUNTIME_DIR=/run/kiosk
mkdir -p "$XDG_RUNTIME_DIR"
seatd -n 2>/dev/null &
exec cage -- chromium \
    --kiosk --no-first-run --disable-translate --noerrdialogs \
    --no-sandbox --disable-gpu-shader-disk-cache \
    --user-data-dir=/run/kiosk/chromium "$1"
EOF
    chmod 0755 "$ROOT/usr/bin/imagectl-kiosk"
fi

# --- kernel modules and firmware ---------------------------------------------
# Storage + wired NIC drivers for the serviced fleet. Built-ins cover the
# rest; a missing module is logged by init, not fatal.

MODSRC="/lib/modules/$KVER"
[ -d "$MODSRC" ] || { echo "no modules for kernel $KVER" >&2; exit 1; }

MODULE_SUBDIRS=(kernel/drivers/net/ethernet kernel/drivers/net/phy
                kernel/drivers/net/mdio     kernel/drivers/nvme
                kernel/drivers/ata          kernel/drivers/scsi
                kernel/drivers/usb/storage
                # Hyper-V: vmbus הוא התשתית, net/hyperv הוא הכרטיס.
                # בלעדיהם הסוכן במעבדת ה-VM (issue #12) עולה בלי דיסק ורשת.
                kernel/drivers/hv           kernel/drivers/net/hyperv
                # מקלדת — תמיד, לא רק לקיוסק: אשף השחזור (ESC) קורא ממנה.
                # ‏usbhid לחומרה, hyperv-keyboard למעבדת ה-VM. בלעדיהם האשף
                # מוצג אבל אף הקשה לא מגיעה (issue #43).
                kernel/drivers/hid          kernel/drivers/input/serio
                kernel/drivers/input/keyboard
                # ‏usbhid מצהיר depends: usbcore,hid — ובלי הבקר עצמו אף
                # התקן USB אינו מתאמן. ‏#43 הוסיף את hid ונעצר שם, ולכן
                # מקלדת USB עדיין לא עבדה על חומרה (‏#77). ‏PS/2 הסתירה
                # את זה: ‏i8042 הוא built-in.
                kernel/drivers/usb/core     kernel/drivers/usb/host
                # מערכות קבצים. בלעדיהן ה-initramfs יכול לעגן NTFS דרך
                # FUSE ותו לא, וזה נראה כמו שלושה באגים נפרדים: שם
                # המחשב בלינוקס לא נכתב כי `mount -t ext4` נכשל (#62),
                # ‏`used_bytes` היה 0 בכל מניפסט כי המדידה מודדת אחרי
                # מאונט, ו-`secure_boot` דיווח `false` כי efivarfs הוא
                # מודול (‏CONFIG_EFIVAR_FS=m) שלא נארז (#84).
                #
                # ‏nls במלואו ולא תת-קבוצה: ‏vfat דורש את קידוד ברירת
                # המחדל של הקרנל, וכאן זה `cp437` **וגם** `ascii`
                # (‏CONFIG_FAT_DEFAULT_CODEPAGE/IOCHARSET). בחירת תת-
                # קבוצה היא בדיוק הניחוש שנכשל ב-#33, ‏#76 ו-#77.
                # ‏mbcache, ‏jbd2, ‏crc16, ‏xor, ‏raid6_pq ו-libcrc32c
                # נגררים מ-modules.dep — לכן הם אינם ברשימה הזו.
                kernel/fs/fat               kernel/fs/nls
                kernel/fs/ext4              kernel/fs/btrfs
                kernel/fs/efivarfs)
# פלטפורמות היעד המוצהרות, וזוג המודולים שכל אחת מהן לא עולה בלעדיו:
# כרטיס הרשת ובקר הדיסק. עד כאן ה-initramfs כיסה הייפרווייזר אחד —
# זה שעליו הוא נבנה — ומכונה על ESXi, ‏KVM או Xen עלתה בלי רשת,
# וחלקן גם בלי דיסק (#78).
#
# לפי שם ולא לפי תיקייה, כי חלקם אינם יושבים בתיקייה בכלל:
# ‏`virtio_net` ו-`xen-netfront` הם קבצים בודדים ישירות תחת
# ‏kernel/drivers/net, ולולאת התיקיות לעולם לא תיגע בהם — אותה צורה
# בדיוק כמו ‏`mbcache` ב-#84.
#
# ‏`virtio`, ‏`virtio_ring`, ‏`virtio_pci` ו-`xenbus` הם built-in בקרנל
# של דביאן, ולכן אינם כאן ואין צורך בעצי `virtio`/`xen` כלל.
#
# ולארוז את ‏kernel/drivers/net כולו זו לא התשובה: ‏+11MB דחוסים על
# ‏35.9MB קיימים, ‏31% לכל אתחול PXE בכיתה, כדי לקבל דרייברים לחומרה
# שלא קיימת במכללה. ההצהרה כאן עולה ‏~150KB — והיא גם נבדקת.
REQUIRED_MODULES=(hv_netvsc   hv_storvsc      # Hyper-V
                  vmxnet3     vmw_pvscsi      # VMware ESXi
                  virtio_net  virtio_blk      # KVM / Proxmox
                  xen-netfront xen-blkfront)  # Xen

# מערכות הקבצים שהמערכת נשענת עליהן — מוצהרות, ולא נוכחות במקרה. עד
# כאן הן הגיעו כתוצר לוואי של `MODULE_SUBDIRS` וסגירת התלויות, ולכן
# נשירה של אחת מהן מסתיימת ב-exit 0 ומתגלה רק מול מכונה: ‏`exfat`
# ו-`isofs` נשרו בין שתי גרסאות, ‏kernel/fs ירד מ-73 ל-69 קבצים, ואיש
# לא ידע עד שהשוו רשימות בידיים (#121). מה תלוי במה: ‏ext4/btrfs —
# שחזור לינוקס וכתיבת `/etc/hostname` (#107, ‏#62); ‏vfat/fat — מחיצת
# ה-ESP; ‏nls_cp437/nls_ascii — הקידודים ש-vfat דורש (#84);
# ‏efivarfs — ‏`secure_boot` במניפסט (#84).
#
# ‏`exfat` ו-`isofs` אינם כאן בכוונה, אחרי חיפוש: אין בקוד קורא להם.
# הסוכן עולה מהרשת ולא ממדיה אופטית — אין `sr0`, אין `iso9660` ואין
# ‏`mount` על מדיה בשום מקום ברפו — ומחיצת exFAT באורח נשלחת
# ל-`partclone.dd` (ברירת המחדל של `_tool_for` ב-restore.sh), שקורא
# בלוקים ולא מערכת קבצים. הדבר היחיד שהמודול היה מוסיף הוא `used_bytes`
# אמיתי למחיצה כזאת, ובלעדיו `_used_bytes` מדווח 0 **עם אזהרה ביומן**
# ולא בשקט. אם יתברר שכן צריך אותם — הוספת שם לרשימה הזאת היא כל
# השינוי, והבנייה תאכוף אותו מיד.
REQUIRED_FS_MODULES=(ext4 btrfs vfat fat nls_cp437 nls_ascii efivarfs)

if [ "$WITH_GUI" -eq 1 ]; then
    # מסך וקלט מלא לקיוסק: דרייברי GPU, עכבר, evdev.
    MODULE_SUBDIRS+=(kernel/drivers/gpu kernel/drivers/input)
fi

for sub in "${MODULE_SUBDIRS[@]}"; do
    if [ -d "$MODSRC/$sub" ]; then
        mkdir -p "$ROOT/lib/modules/$KVER/$sub"
        cp -a "$MODSRC/$sub/." "$ROOT/lib/modules/$KVER/$sub/"
    fi
done

# מודול מוצהר שחסר עוצר את הבנייה — פלטפורמה ומערכת קבצים כאחת.
# ‏initramfs שנבנה בלי דרייבר של פלטפורמה מוצהרת ייראה תקין לחלוטין,
# והכשל יתגלה רק מול מכונה — `no DHCP lease on any interface`, בלי רמז
# לאיזה מודול חסר. זה בדיוק מה שקרה ב-#76, ‏#77 ו-#84, בכל פעם מול
# חומרה ולא בבנייה, וב-#121 זה קרה שוב למערכות הקבצים.
# כולם נאספים לפני ההודעה, כדי שלא יתגלו אחד-אחד בשש בנייות.
_missing=""
for _mod in "${REQUIRED_MODULES[@]}" "${REQUIRED_FS_MODULES[@]}"; do
    _hit=$(find "$MODSRC" -name "$_mod.ko*" | head -1)
    if [ -z "$_hit" ]; then
        _missing="$_missing $_mod"
        continue
    fi
    _rel=${_hit#"$MODSRC"/}
    if [ ! -f "$ROOT/lib/modules/$KVER/$_rel" ]; then
        mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_rel")"
        cp -a "$_hit" "$ROOT/lib/modules/$KVER/$_rel"
    fi
done
if [ -n "$_missing" ]; then
    echo "missing required modules:$_missing" >&2
    echo "kernel $KVER cannot serve every platform and filesystem ImageCtl" >&2
    echo "claims to support." >&2
    exit 1
fi

# ‏רשימת תיקיות ידנית תמיד תפספס תלות אחת עמוק יותר. ‏#76 היה PHY
# שנטען אחרי ה-MAC, ‏#77 היה usbhid בלי בקר USB — ואז התברר ש-usbcore
# עצמו תלוי ב-usb-common, שיושב בתיקייה שלישית שאיש לא חשב עליה.
# שלוש פעמים אותו באג בערב אחד, כולל פעם אחת אחרי שכבר ידענו בדיוק
# מה מחפשים.
#
# ‏`modules.dep` יודע את התשובה. במקום לנחש תיקיות, סוגרים את הגרף:
# כל מודול שהועתק גורר את התלויות שלו, עד שאין מה להוסיף. מודול חסר
# תלות הוא מודול שנכשל בטעינה בשקט, ובלי זה `N modules did not load`
# היה 69 מתוך ~180.
_closure_round=0
_closure_changed=1
while [ "$_closure_changed" -eq 1 ]; do
    _closure_changed=0
    _closure_round=$((_closure_round + 1))
    for _rel in $(cd "$ROOT/lib/modules/$KVER" && find . -name '*.ko*' | sed 's|^\./||'); do
        for _dep in $(awk -F: -v k="$_rel" '$1 == k {print $2}' "$MODSRC/modules.dep"); do
            if [ ! -f "$ROOT/lib/modules/$KVER/$_dep" ] && [ -f "$MODSRC/$_dep" ]; then
                mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_dep")"
                cp -a "$MODSRC/$_dep" "$ROOT/lib/modules/$KVER/$_dep"
                _closure_changed=1
            fi
        done
    done
    # תקרה: גרף התלויות של הקרנל אינו מעגלי, אבל לולאה אינסופית בבנייה
    # גרועה מאימג' חסר.
    [ "$_closure_round" -ge 10 ] && break
done
echo "module dependency closure: $(find "$ROOT/lib/modules/$KVER" -name '*.ko*' | wc -l) modules after $_closure_round rounds"
cp "$MODSRC"/modules.{order,builtin}* "$ROOT/lib/modules/$KVER/" 2>/dev/null || true
depmod -b "$ROOT" "$KVER"

# ‏דרייברי ה-PHY חייבים להיטען לפני דרייברי ה-MAC שנתלים בהם. ‏r8169
# שעושה probe לפני ש-realtek.ko נרשם נכשל ב-EADDRNOTAVAIL (‏-49), והקרנל
# **אינו מנסה שוב**: המודול נשאר טעון וההתקן נשאר בלי דרייבר. מחשב
# Lenovo עם RTL8168 לא קיבל ממשק רשת בכלל בגלל זה (‏#76).
#
# ‏`sort -u` הוא ששבר את זה: הוא מיין אלפביתית, ו-`r8169` קודם
# ל-`realtek`. הוא גם ביטל בשקט את הכוונה של "Storage first" שורה
# מעליו. ‏`awk '!seen[$0]++'` מסיר כפילויות בלי למיין, ולכן הסדר נשמר.
_phy_mods=$(find "$ROOT/lib/modules/$KVER/kernel/drivers/net/phy" \
                 "$ROOT/lib/modules/$KVER/kernel/drivers/net/mdio" \
                 -name '*.ko*' 2>/dev/null | sed 's|.*/||; s|\.ko.*||' | sort)

{
    # Storage first, then the PHY drivers, then every copied NIC driver.
    # ‏הבקר לפני מה שמתחבר אליו, מאותו טעם כמו ה-PHY: ‏usbcore ואז
    # ה-HCDים, ורק אחר כך usb-storage ו-usbhid שנשענים עליהם.
    printf '%s\n' usbcore xhci_hcd xhci_pci ehci_hcd ehci_pci
    printf '%s\n' ohci_hcd ohci_pci uhci_hcd
    printf '%s\n' ahci nvme sd_mod uas usb-storage hv_vmbus hv_storvsc
    # בקרי הדיסק של ESXi, ‏KVM ו-Xen. ‏`virtio_pci` הוא built-in, ולכן
    # האפיק כבר שם כשאלה נטענים (#78).
    printf '%s\n' vmw_pvscsi virtio_scsi virtio_blk xen-blkfront
    # ‏מקלדת לאשף השחזור — מודול חסר אינו פטאלי (issue #43).
    printf '%s\n' hid hid_generic usbhid atkbd hyperv_keyboard
    # מערכות קבצים במפורש, ולא בהסתמך על טעינה-לפי-דרישה של הקרנל.
    # ‏`mount -t ext4` אמנם מבקש `fs-ext4` דרך modules.alias, אבל זו
    # שרשרת הנחות (‏depmod, ‏busybox modprobe, ‏/proc/sys/kernel/modprobe)
    # שכל חוליה בה נכשלת בשקט. כאן כישלון נספר ומדווח (#84).
    printf '%s\n' efivarfs fat vfat nls_cp437 nls_ascii ext4 btrfs
    [ -n "$_phy_mods" ] && printf '%s\n' "$_phy_mods"
    find "$ROOT/lib/modules/$KVER/kernel/drivers/net" -name '*.ko*' 2>/dev/null \
        | sed 's|.*/||; s|\.ko.*||' | sort
    if [ "$WITH_GUI" -eq 1 ]; then
        # ‏GPU של אחד משלושת היצרנים, ועכבר/evdev לקיוסק.
        printf '%s\n' i915 amdgpu nouveau simpledrm evdev
    fi
} | awk '!seen[$0]++' > "$ROOT/etc/imagectl/modules"

for fw in "${FIRMWARE_DIRS[@]}"; do
    if [ -d "/lib/firmware/$fw" ]; then
        mkdir -p "$ROOT/lib/firmware/$fw"
        cp -a "/lib/firmware/$fw/." "$ROOT/lib/firmware/$fw/"
    fi
done

# --- pack --------------------------------------------------------------------

(cd "$ROOT" && find . | cpio -o -H newc --quiet | gzip -9) > "$OUTPUT"

SIZE=$(du -h "$OUTPUT" | cut -f1)
echo
echo "initramfs ready: $OUTPUT ($SIZE)"
echo
echo "Next steps:"
echo "  1. Copy it, together with the matching kernel, to the HTTP root the"
echo "     installer created (clients fetch /boot/vmlinuz and /boot/initrd.img):"
if [ "$WITH_GUI" = 1 ]; then
    # --with-gui builds the kiosk initramfs. It is served next to the
    # text one under a fixed name, and the menu generator hands it only
    # to the roles that have a screen -- build and classroom (#32).
    echo "       cp $OUTPUT /srv/imagectl/boot/initrd.img.gui"
    echo "     (--with-gui: this is the GUI initramfs. Do NOT overwrite"
    echo "      initrd.img with it -- cloning machines still need that one.)"
else
    echo "       cp $OUTPUT /srv/imagectl/boot/initrd.img"
fi
echo "       cp /boot/vmlinuz-$KVER /srv/imagectl/boot/vmlinuz"
echo "  2. The GRUB menu generator already points clients at those paths."
echo "  3. Updating the agent = rebuilding this file and copying it again."
echo "     No signing, no key handling."
