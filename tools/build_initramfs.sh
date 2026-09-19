#!/bin/bash
# build_initramfs.sh -- builds the ImageCtl agent initramfs on a Debian
# machine (the boot server itself is fine). The output is a cpio.gz that
# GRUB loads after the distro kernel; nothing in it needs signing.
#
# Usage:
#   sudo ./tools/build_initramfs.sh [--output FILE] [--kernel-version VER]
#                                   [--firmware DIR]... [--ssh-key FILE]
#                                   [--with-gui] [--skip-apt]
#                                   [--tools-selection FILE]
#                                   [--source-date-epoch SECONDS]
#
# The output is reproducible (#1125, feeding #1078): the same checkout and
# the same builder give the same bytes. Every mtime in the image is set to
# SOURCE_DATE_EPOCH -- the flag, the environment variable, or the time of
# the commit the checkout is at, in that order.
#
# --ssh-key packs a public key as the technician's authorized_keys. Without
# it dropbear has nobody to let in and never listens; with it, it still
# listens only when the kernel line carries imagectl.debug=1.
#
# --tools-selection packs the IT toolbox the operator chose in the console
# (#1050): FILE is <data_dir>/tools-selection.json (interfaces.md §23). Its
# "build" ids are mapped to binaries through agent/lib/toolbins.sh -- the
# same table the station filters by -- and the file itself rides into the
# initrd as /etc/imagectl/tools-selection.json. Without the flag no toolbox
# binary beyond the base set is packed and the tools screen is empty.
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
TOOLS_SELECTION_FILE=""
#: `auto` (ברירת המחדל — `finish_and_stop` גוזר מהתפקיד: כיתה=reboot,
#: בנייה/שיכפול=poweroff), `poweroff` או `reboot`. `reboot` הוא ההגדרה
#: שכלי המעבדה כותב (tools/lab/after-task-reboot.sh).
AFTER_TASK=auto
FIRMWARE_DIRS=("rtl_nic")

while [ $# -gt 0 ]; do
    case "$1" in
        --output)         OUTPUT="$2"; shift 2 ;;
        --kernel-version) KVER="$2"; shift 2 ;;
        --firmware)       FIRMWARE_DIRS+=("$2"); shift 2 ;;
        --ssh-key)        SSH_KEY_FILE="$2"; shift 2 ;;
        --after-task)     AFTER_TASK="$2"; shift 2 ;;
        --with-gui)       WITH_GUI=1; shift ;;
        --skip-apt)       SKIP_APT=1; shift ;;
        --tools-selection) TOOLS_SELECTION_FILE="$2"; shift 2 ;;
        --source-date-epoch) SOURCE_DATE_EPOCH="$2"; shift 2 ;;
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

# --- the toolbox selection (--tools-selection, #1050) ---------------------------
# Resolved here, before apt and the compiles, for the same reason as the ssh
# key: an id the table does not know should cost a second, not a build.
# tools_bins_plan (agent/lib/toolbins.sh -- POSIX sh, bash sources it fine)
# prints one binary per line and exits 1 naming every unknown id; an empty
# "build" list is a valid answer and packs nothing.
TOOL_BINS=()
if [ -n "$TOOLS_SELECTION_FILE" ]; then
    [ -f "$TOOLS_SELECTION_FILE" ] \
        || { echo "tools selection file not found: $TOOLS_SELECTION_FILE" >&2; exit 1; }
    . "$AGENT_DIR/lib/toolbins.sh"
    _plan=$(tools_bins_plan "$TOOLS_SELECTION_FILE") \
        || { echo "--tools-selection: the selection is not packable (see above)" >&2; exit 1; }
    [ -z "$_plan" ] || mapfile -t TOOL_BINS <<< "$_plan"
    echo "tools: packing ${#TOOL_BINS[@]} toolbox binaries: ${TOOL_BINS[*]:-(none)}"
fi

# The Debian 13 package for each toolbox binary the table can name. Only the
# packages of the binaries actually planned are installed -- a selection of
# two tools does not pull testdisk. A planned binary missing from this map
# is a build error (it would fail in copy_bin anyway, but with a worse
# message): add the binary to toolbins.sh and its package here together.
declare -A TOOL_PKG=(
    [blkdiscard]=util-linux  [wipefs]=util-linux   [hdparm]=hdparm
    [sgdisk]=gdisk           [nvme]=nvme-cli       [nwipe]=nwipe
    [tpm2_clear]=tpm2-tools  [chntpw]=chntpw       [ntfscat]=ntfs-3g
    [ntfsundelete]=ntfs-3g   [photorec]=testdisk   [testdisk]=testdisk
    [scalpel]=scalpel        [foremost]=foremost   [extundelete]=extundelete
    [magicrescue]=magicrescue [recoverjpeg]=recoverjpeg
    [unsquashfs]=squashfs-tools [bsdtar]=libarchive-tools
    [ddrescue]=gddrescue     [safecopy]=safecopy   [fsck.vfat]=dosfstools
)
TOOL_PKGS=()
for _tb in "${TOOL_BINS[@]}"; do
    [ -n "${TOOL_PKG[$_tb]:-}" ] \
        || { echo "tools: no apt package known for binary $_tb (toolbins.sh and TOOL_PKG drifted)" >&2; exit 1; }
    case " ${TOOL_PKGS[*]:-} " in *" ${TOOL_PKG[$_tb]} "*) ;; *) TOOL_PKGS+=("${TOOL_PKG[$_tb]}") ;; esac
done

# --- הצהרת הקיוסק (--with-gui) ------------------------------------------------
# שתי רשימות ולא אחת, כי הן נכשלות בשתי נקודות שונות בזמן: חבילה שאין
# ממנה מועמד ב-apt נתפסת **לפני** ש-apt רץ, ונתיב שלא הופיע על הדיסק
# נתפס אחרי ההתקנה. עד כאן לא נבדקה אף אחת מהן.
GUI_PACKAGES=(fonts-ibm-plex fontconfig-config
              libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0
              libcairo2 libpixman-1-0 libharfbuzz0b libfribidi0
              libfreetype6 libfontconfig1 libglib2.0-0t64 libdrm2
              # #690: LibVNCServer runtime for imagectl-monitor. Its .so
              # closure rides along via ldd like any packed binary's.
              libvncserver1)

# ‏`truetype` ולא `opentype`: ‏fonts-ibm-plex בדביאן מתקינה
# ל-`/usr/share/fonts/truetype/ibm-plex`, והנתיב שהיה כאן מעולם לא היה
# קיים. ‏`if [ -d "$dir" ]` דילג עליו בלי מילה, ולכן קיוסק בלי גופן עברי
# נראה כמו בנייה שהצליחה — אותו דפוס כמו ‏`[ -e ] && cp` של ה-gconv
# ב-#33. הרשימה הזאת נבדקת, ומה שאין בו עוצר את הבנייה (#120).
#
# ‏/etc/fonts ו-/usr/share/fontconfig הם הזוג ולא אחד מהם: קובצי
# ‏conf.d הם קישורים סימבוליים אל conf.avail, וקישור יתום בתוך
# ה-initramfs שקול לקובץ חסר. בלי תצורת fontconfig הממשק אינו מוצא
# **שום** גופן — גם כשהקובץ ארוז לידו — והעברית יוצאת ריבועים.
GUI_PATHS=(/usr/share/fonts/truetype/ibm-plex
           /etc/fonts                  /usr/share/fontconfig)

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
# udp-sender (#715): the build machine streams its own disk to the cloning
# machines; the udpcast package ships both halves, so nothing new to install.
# efibootmgr (#433): the Boot#### entry for the restored Windows on UEFI;
# copy_bin pulls libefiboot/libefivar/libpopt through ldd and stops the
# build if one is missing.
BINARIES=(curl jq zstd pv sgdisk blockdev sha256sum od hdparm ntfsresize openssl
          ntfs-3g ntfs-3g.probe ntfsfix umount blkid df mount stty ethtool smartctl
          e2fsck resize2fs btrfs xfs_growfs
          udp-receiver udp-sender partclone.ntfs partclone.fat partclone.ext4
          partclone.btrfs partclone.dd
          dropbear dropbearkey
          dmidecode efibootmgr)

if [ "$SKIP_APT" -eq 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y --no-install-recommends \
        busybox-static zstd partclone udpcast gdisk curl jq pv \
        ntfs-3g libhivex-dev hdparm coreutils util-linux openssl \
        e2fsprogs btrfs-progs xfsprogs cpio gzip gcc libc6-dev dropbear-bin ethtool \
        smartmontools dmidecode efibootmgr
    # #1050: the toolbox delta -- only what the selection needs, or nothing.
    if [ "${#TOOL_PKGS[@]}" -gt 0 ]; then
        echo "tools: installing ${TOOL_PKGS[*]}"
        apt-get install -y --no-install-recommends "${TOOL_PKGS[@]}" \
            || { echo "tools: apt-get install of the toolbox packages failed" >&2; exit 1; }
    fi
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
    # ldd on a shell wrapper (hivexget on Debian 13) exits nonzero —
    # that is fine and must not kill the build (#12). A real ELF whose
    # ldd prints "not found", or a cp that fails, must (#507).
    _ldd_out=$(ldd "$1" 2>/dev/null) || true
    case "$_ldd_out" in
        *"not found"*)
            echo "missing shared library for $1:" >&2
            printf '%s\n' "$_ldd_out" >&2
            exit 1
            ;;
    esac
    [ -n "$_ldd_out" ] || return 0
    while read -r lib; do
        [ -f "$lib" ] || continue
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -Ln "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_ldd_out" | awk '/=>/ { print $3 } /^\s*\// { print $1 }')
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
# Required, not best-effort: `cp ... 2>/dev/null || true` let a missing
# loader or libnss build clean, and the station then failed in curl, DNS
# and dropbear with messages that name none of them (#1125, the #33
# pattern). `-n` stays: the loader is in every ldd closure and copy_libs
# has usually packed it already -- that skip exits 0 (coreutils 9.7).
for extra in /lib64/ld-linux-x86-64.so.2 \
             /lib/x86_64-linux-gnu/libnss_dns.so.2 \
             /lib/x86_64-linux-gnu/libnss_files.so.2 \
             /lib/x86_64-linux-gnu/libresolv.so.2; do
    [ -e "$extra" ] || { echo "loader/NSS library missing: $extra" >&2; exit 1; }
    mkdir -p "$ROOT$(dirname "$extra")"
    cp -Ln "$extra" "$ROOT$extra"
    [ -s "$ROOT$extra" ] || { echo "loader/NSS library not packed: $extra" >&2; exit 1; }
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

# lldpsniff: one LLDP frame → JSON in hello (#1048). libc only; packed in
# every image (classroom stations report switch/port too, not only GUI).
echo "compiling lldpsniff..."
gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/imagectl-lldpsniff" "$AGENT_DIR/lldpsniff.c"
[ -s "$ROOT/usr/bin/imagectl-lldpsniff" ] && [ -x "$ROOT/usr/bin/imagectl-lldpsniff" ] \
    || { echo "imagectl-lldpsniff missing or not executable" >&2; exit 1; }

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

# --- tools: framework (#649) -------------------------------------------------
# The IT toolbox: agent/lib/tools.sh and every agent/lib/tools_<domain>.sh
# ride with the lib/*.sh glob above, so a new domain module is packed by
# existing -- this gate proves it landed. The framework's built-in tools need
# dmidecode (in BINARIES and in the apt list, so tests/test_agent.py
# cross-checks it) and smartctl (packed since #652).
for _t in "$AGENT_DIR"/lib/tools_*.sh "$AGENT_DIR/lib/toolbins.sh"; do
    [ -f "$_t" ] || continue
    [ -s "$ROOT/usr/lib/imagectl/$(basename "$_t")" ] \
        || { echo "tools: $(basename "$_t") was not packed" >&2; exit 1; }
done
[ -x "$ROOT/usr/bin/dmidecode" ] || copy_bin dmidecode

# --- tools: the selection (#1050) ----------------------------------------------
# The binaries TOOL_BINS planned above, each with its ldd closure (copy_bin
# stops the build on a missing one -- a selected tool that is not on the
# builder is a failed build, not an empty row on the station). The selection
# file itself is what the station filters by: no file, no tools.
for _tb in "${TOOL_BINS[@]}"; do
    copy_bin "$_tb"
    if [ "$_tb" = tpm2_clear ]; then
        # libtss2-tctildr picks the kernel-device transport with dlopen, so
        # ldd on tpm2_clear cannot see it; without it every tpm2_* call fails
        # "no TCTI" on a machine whose TPM is right there.
        _tcti=$(ldconfig -p | awk '/libtss2-tcti-device\.so/{print $NF; exit}')
        [ -n "$_tcti" ] && [ -f "$_tcti" ] \
            || { echo "tools: tpm2_clear packed but libtss2-tcti-device.so is missing" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$_tcti")"
        cp -L "$_tcti" "$ROOT$_tcti"
        copy_libs "$_tcti"
    fi
done
if [ -n "$TOOLS_SELECTION_FILE" ]; then
    install -m 0644 "$TOOLS_SELECTION_FILE" "$ROOT/etc/imagectl/tools-selection.json"
    [ -s "$ROOT/etc/imagectl/tools-selection.json" ] \
        || { echo "tools: tools-selection.json was not packed" >&2; exit 1; }
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

# מה עושים כשמשימה נגמרה. ברירת המחדל היא `auto`: `finish_and_stop`
# גוזר מהתפקיד — תחנת כיתה מאתחלת, בנייה/שיכפול מתכבים (מגירות
# מוחלפות במכונה כבויה, נספח א׳). ‏`reboot` מפורש נועד למעבדה מרוחקת,
# שבה מכונה שכבתה היא מכונה שאיש אינו יכול להדליק — הצי מתאתחל לבד
# (tools/lab/after-task-reboot.sh). נקרא ב-`finish_and_stop`.
case "$AFTER_TASK" in
    auto|poweroff|reboot) ;;
    *) echo "build_initramfs: --after-task must be auto, poweroff or reboot, got '$AFTER_TASK'" >&2; exit 2 ;;
esac
echo "$AFTER_TASK" > "$ROOT/etc/imagectl/after-task"
echo "after-task: $AFTER_TASK"

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
# Native Pango/Cairo rendering onto DRM/KMS, with evdev input. Apt installs
# transitive runtime dependencies; ldd selects the actual shared-library
# closure, including any X11 libraries linked by Debian's Cairo build.

if [ "$WITH_GUI" -eq 1 ]; then
    if [ "$SKIP_APT" -eq 0 ]; then
        apt-get install -y --no-install-recommends "${GUI_PACKAGES[@]}"
        # Build-host tools/headers only; none are copied into the image.
        apt-get install -y --no-install-recommends make pkg-config \
            libpango1.0-dev libcairo2-dev libdrm-dev libvncserver-dev
    fi
    echo "compiling the native station GUI..."
    GUI_DIR="$SCRIPT_DIR/../native-gui"
    # Force a fresh host build rather than reuse a binary from another host.
    make -B -C "$GUI_DIR" imagectl-station-gui
    GUI_BIN="$GUI_DIR/imagectl-station-gui"
    [ -s "$GUI_BIN" ] && [ -x "$GUI_BIN" ] \
        || { echo "--with-gui: native GUI binary missing or not executable: $GUI_BIN" >&2; exit 1; }
    install -m 0755 "$GUI_BIN" "$ROOT/usr/bin/imagectl-station-gui"
    # Same closure as measure-size.sh/copy_libs(), but this is a known ELF:
    # unresolved libraries or a failed copy must stop the GUI build.
    #
    # ldd is the whole closure here -- measured, not assumed (#1125, 19/09):
    # a 32-screen `--png` render inside a chroot of the packed initrd under
    # LD_DEBUG=files loaded 0 libraries dynamically (every load was a
    # NEEDED one); libpango, libcairo and libvncserver import no dlopen at
    # all, and Debian 13 ships no pango/cairo module directory. The dlopen
    # extras that do exist -- gconv for hivex, the TCTI for tpm2 -- are
    # handled by name above.
    _gui_ldd=$(ldd "$GUI_BIN")
    if [[ "$_gui_ldd" == *"not found"* ]]; then
        printf '%s\n' "--with-gui: unresolved native GUI libraries:" "$_gui_ldd" >&2
        exit 1
    fi
    while read -r lib; do
        [ -f "$lib" ] \
            || { echo "--with-gui: native GUI library missing: $lib" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -L "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_gui_ldd" | awk '/=>/ { print $3 } /^[[:space:]]*\// { print $1 }')
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
        # The package carries every Plex family; copy only the six faces
        # named by native-gui/tools/measure-size.sh below.
        [ "$_p" = /usr/share/fonts/truetype/ibm-plex ] && continue
        cp -a "$_p/." "$ROOT$_p/"
    done
    fontdir=/usr/share/fonts/truetype/ibm-plex
    for face in IBMPlexSansHebrew-Regular IBMPlexSansHebrew-Medium IBMPlexSansHebrew-SemiBold \
                IBMPlexSansHebrew-Bold IBMPlexMono-Regular IBMPlexMono-Medium; do
        src=$(find "$fontdir" -iname "$face.ttf" -print -quit)
        [ -n "$src" ] && [ -s "$src" ] \
            || { echo "--with-gui: font face missing or empty: $fontdir/$face.ttf" >&2; exit 1; }
        cp -L "$src" "$ROOT$fontdir/"
    done

    cat > "$ROOT/usr/bin/imagectl-kiosk" << 'EOF'
#!/bin/sh
LIB_DIR=${LIB_DIR:-/usr/lib/imagectl}
export LIB_DIR
. "$LIB_DIR/guibridge.sh"
gui_main "$@"
EOF
    chmod 0755 "$ROOT/usr/bin/imagectl-kiosk"

    # --- the remote monitor (#690) -------------------------------------------
    # An RFB server exposing /dev/fb0 over TCP 5900, packed only in the GUI
    # image: build and cloner have a framebuffer, and a classroom station
    # never starts it (agent/lib/monitor.sh gates by role). Input mode needs
    # the uinput module, declared in REQUIRED_MODULES below.
    echo "compiling the remote monitor..."
    gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/imagectl-monitor" \
        "$AGENT_DIR/monitor.c" "$AGENT_DIR/hmac_sha256.c" -lvncserver
    [ -s "$ROOT/usr/bin/imagectl-monitor" ] && [ -x "$ROOT/usr/bin/imagectl-monitor" ] \
        || { echo "--with-gui: imagectl-monitor missing or not executable" >&2; exit 1; }
    # Same closure gate as the native GUI binary: an unresolved .so or a
    # failed copy is a build that looks clean and a monitor that never
    # listens -- the #78 failure mode, caught here instead of on the metal.
    _mon_ldd=$(ldd "$ROOT/usr/bin/imagectl-monitor")
    if [[ "$_mon_ldd" == *"not found"* ]]; then
        printf '%s\n' "--with-gui: unresolved imagectl-monitor libraries:" "$_mon_ldd" >&2
        exit 1
    fi
    echo "imagectl-monitor ldd closure:"
    printf '%s\n' "$_mon_ldd"
    while read -r lib; do
        [ -f "$lib" ] \
            || { echo "--with-gui: imagectl-monitor library missing: $lib" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -L "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_mon_ldd" | awk '/=>/ { print $3 } /^[[:space:]]*\// { print $1 }')
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
                kernel/fs/xfs               kernel/fs/efivarfs)
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
# לא ידע עד שהשוו רשימות בידיים (#121). מה תלוי במה: ‏ext4/btrfs/xfs —
# שחזור לינוקס, כתיבת `/etc/hostname` (#107, ‏#62) והרחבת XFS (#667);
# ‏vfat/fat — מחיצת
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
REQUIRED_FS_MODULES=(ext4 btrfs xfs vfat fat nls_cp437 nls_ascii efivarfs)

if [ "$WITH_GUI" -eq 1 ]; then
    # רק מודולי התצוגה/קלט של חומרת הקיוסק; modules.dep מוסיף תלויות
    # (#641: אריזת כל drivers/gpu+input ניפחה וקרסה על הברזל). את הדרייבר
    # הגנרי של HID מספק `hid.ko` עצמו — אין `hid_generic.ko` נפרד בקרנל
    # דביאן 13, ולכן הוא **אינו** נכנס לרשימה כאן (הלולאה מחפשת קובץ ותיכשל);
    # ה-modules-load עדיין קורא ל-hid-generic כ-no-op לא-מזיק.
    # ‏uinput (#690): המוניטור במצב input יוצר מקלדת/עכבר וירטואליים דרכו.
    # מודול מוצהר חסר עוצר את הבנייה, כמו כל השאר.
    REQUIRED_MODULES+=(i915 evdev usbhid hid uinput)
    # ‏i915 דורש firmware; modules.dep לא מעתיק firmware, ורק rtl_nic ברירת
    # מחדל — בלעדיו התצוגה עלולה לצאת מנוונת/שחורה.
    FIRMWARE_DIRS+=(i915)
fi

# תת-עץ מוצהר שחסר נאסף כאן ומדווח יחד עם המודולים החסרים למטה, באותה
# עצירה. ‏`if [ -d ]` לבדו העלים אותו בשקט (#1125 — הדפוס של #84: ‏usb/host
# חסר הוא מקלדת USB בלי בקר, ובנייה שיצאה 0). אין רשימת "אופציונליים":
# כל 20 העצים קיימים בקרנל דביאן 13 הרגיל (נמדד 19/09), ומה שאין בו הוא
# קרנל שאינו מתאים לבנייה — ההודעה למטה אומרת גם את זה.
_missing_sub=""
for sub in "${MODULE_SUBDIRS[@]}"; do
    if [ -d "$MODSRC/$sub" ]; then
        mkdir -p "$ROOT/lib/modules/$KVER/$sub"
        cp -a "$MODSRC/$sub/." "$ROOT/lib/modules/$KVER/$sub/"
    else
        _missing_sub="$_missing_sub $sub"
    fi
done

# מודול מוצהר שחסר עוצר את הבנייה — פלטפורמה ומערכת קבצים כאחת.
# ‏initramfs שנבנה בלי דרייבר של פלטפורמה מוצהרת ייראה תקין לחלוטין,
# והכשל יתגלה רק מול מכונה — `no DHCP lease on any interface`, בלי רמז
# לאיזה מודול חסר. זה בדיוק מה שקרה ב-#76, ‏#77 ו-#84, בכל פעם מול
# חומרה ולא בבנייה, וב-#121 זה קרה שוב למערכות הקבצים.
# כולם נאספים לפני ההודעה, כדי שלא יתגלו אחד-אחד בשש בנייות.
_missing=""
# Intel watchdogs are optional (software recovery remains available). The
# LPC bridge registers the TCO device on older HP boards; it is not a
# module dependency of iTCO_wdt, so copy it explicitly as well.
for _mod in iTCO_wdt lpc_ich; do
    _hit=$(find "$MODSRC" -name "$_mod.ko*" | head -1)
    if [ -n "$_hit" ]; then
        _rel=${_hit#"$MODSRC"/}
        mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_rel")"
        cp -a "$_hit" "$ROOT/lib/modules/$KVER/$_rel"
    else
        echo "optional watchdog module $_mod absent (may be built-in); software fallback available" >&2
    fi
done
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
if [ -n "$_missing" ] || [ -n "$_missing_sub" ]; then
    [ -z "$_missing_sub" ] || echo "missing module trees under $MODSRC:$_missing_sub" >&2
    [ -z "$_missing" ] || echo "missing required modules:$_missing" >&2
    echo "kernel $KVER cannot serve every platform and filesystem ImageCtl" >&2
    echo "claims to support." >&2
    # ‏#904: על קרנל cloud (linux-image-cloud-amd64 — אימג' הענן של
    # דביאן) זה צפוי: הוא נבנה בלי חלק מהדרייברים ומערכות הקבצים.
    # המסלול שעבד לשרת המשני במעבדה (16/09) הוא העתקת שלושת הקבצים
    # מהשרת הראשי — לא בנייה מקומית. ההודעה אומרת זאת בשמה.
    case "$KVER" in
        *cloud*)
            echo "kernel $KVER is a cloud kernel: do not build here." >&2
            echo "Copy vmlinuz, initrd.img and initrd.img.gui from the primary" >&2
            echo "server's boot dir (/srv/imagectl/boot) instead, or install" >&2
            echo "linux-image-amd64 and build against it (docs/server-install.md)." >&2
            ;;
    esac
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
# modules.order and modules.builtin are depmod's input next to the .ko
# files; without them it indexes an incomplete tree and init reports
# "N modules did not load" with no hint why. The copy was `2>/dev/null ||
# true` -- a kernel package missing either built clean (#1125).
for _f in modules.order modules.builtin; do
    [ -f "$MODSRC/$_f" ] || { echo "$MODSRC/$_f is missing -- depmod needs it" >&2; exit 1; }
done
cp "$MODSRC"/modules.{order,builtin}* "$ROOT/lib/modules/$KVER/"
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
    # Optional Intel TCO hardware recovery; dependencies follow modules.dep.
    printf '%s\n' lpc_ich iTCO_wdt
    # בקרי הדיסק של ESXi, ‏KVM ו-Xen. ‏`virtio_pci` הוא built-in, ולכן
    # האפיק כבר שם כשאלה נטענים (#78).
    printf '%s\n' vmw_pvscsi virtio_scsi virtio_blk xen-blkfront
    # ‏מקלדת לאשף השחזור — מודול חסר אינו פטאלי (issue #43).
    printf '%s\n' hid hid-generic usbhid atkbd hyperv_keyboard
    # מערכות קבצים במפורש, ולא בהסתמך על טעינה-לפי-דרישה של הקרנל.
    # ‏`mount -t ext4` אמנם מבקש `fs-ext4` דרך modules.alias, אבל זו
    # שרשרת הנחות (‏depmod, ‏busybox modprobe, ‏/proc/sys/kernel/modprobe)
    # שכל חוליה בה נכשלת בשקט. כאן כישלון נספר ומדווח (#84).
    printf '%s\n' efivarfs fat vfat nls_cp437 nls_ascii ext4 btrfs xfs
    [ -n "$_phy_mods" ] && printf '%s\n' "$_phy_mods"
    find "$ROOT/lib/modules/$KVER/kernel/drivers/net" -name '*.ko*' 2>/dev/null \
        | sed 's|.*/||; s|\.ko.*||' | sort
    if [ "$WITH_GUI" -eq 1 ]; then
        # ‏GPU של אחד משלושת היצרנים, ועכבר/evdev לקיוסק, ו-uinput למוניטור.
        printf '%s\n' i915 amdgpu nouveau simpledrm evdev uinput
    fi
} | awk '!seen[$0]++' > "$ROOT/etc/imagectl/modules"

# ‏מודולי החובה, בנפרד: מערכות הקבצים שהמערכת נשענת עליהן בכל מכונה. אלה
# מודולי תוכנה טהורים שנטענים בכל פלטפורמה, ולכן כישלון טעינה שלהם הוא
# ‏initramfs שבור — לא חומרה נעדרת כמו דרייבר וירטואליזציה על ברזל.
# ‏agent/init מפריד לפי הרשימה הזאת: מודול חובה שנכשל נקרא בשם וכ-ERROR,
# ומודול פלטפורמה שנכשל מדווח כמצב הצפוי. בלעדיה `N modules did not load`
# קיפל את שניהם למספר אחד (#407).
printf '%s\n' "${REQUIRED_FS_MODULES[@]}" > "$ROOT/etc/imagectl/modules.required"

# קושחה מוצהרת שחסרה עוצרת: ‏rtl_nic הוא ה-NIC של הצי (‏firmware-realtek),
# ו-i915 מצטרף עם --with-gui (‏firmware-intel-graphics בדביאן 13). ‏`if [ -d ]`
# דילג בשקט, והתחנה עלתה בלי רשת או עם מסך שחור (#1125). כולם נאספים
# לפני ההודעה, כמו המודולים.
_no_fw=""
for fw in "${FIRMWARE_DIRS[@]}"; do
    if [ -d "/lib/firmware/$fw" ]; then
        mkdir -p "$ROOT/lib/firmware/$fw"
        cp -a "/lib/firmware/$fw/." "$ROOT/lib/firmware/$fw/"
    else
        _no_fw="$_no_fw $fw"
    fi
done
if [ -n "$_no_fw" ]; then
    echo "firmware directories missing under /lib/firmware:$_no_fw" >&2
    echo "Debian 13: rtl_nic is firmware-realtek, i915 is firmware-intel-graphics." >&2
    exit 1
fi

# --- pack --------------------------------------------------------------------
# Reproducible (#1125, feeding #1078 -- the initrd hash in grub.cfg): the
# same source must give the same bytes. Three sources of noise, each closed
# by name: `find` walks in inode order (LC_ALL=C sort), mtimes are the build
# time (every one clamped to SOURCE_DATE_EPOCH), and cpio stores inode and
# device numbers (--reproducible) while gzip stores a timestamp (-n).
# The epoch is the flag, the environment, or the commit the checkout is at.
# No epoch and no git is a build with no stable hash -- that stops here
# rather than falling back to `date` and printing a one-off hash that looks
# exactly like a stable one (principle 5).
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(git -C "$SCRIPT_DIR" log -1 --format=%ct) \
        || { echo "SOURCE_DATE_EPOCH is unset and 'git log' failed: build from a checkout or pass --source-date-epoch" >&2; exit 1; }
fi
case "$SOURCE_DATE_EPOCH" in
    ''|*[!0-9]*) echo "SOURCE_DATE_EPOCH must be seconds since the epoch, got '$SOURCE_DATE_EPOCH'" >&2; exit 1 ;;
esac
export SOURCE_DATE_EPOCH
echo "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
find "$ROOT" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
(cd "$ROOT" && find . -print0 | LC_ALL=C sort -z \
    | cpio -o -H newc --null --reproducible --quiet | gzip -9n) > "$OUTPUT"

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
