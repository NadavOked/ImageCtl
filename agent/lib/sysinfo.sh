# sysinfo.sh -- gathers hardware facts and builds the hello body
# (interfaces.md section 2). Pure sh string assembly, no jq needed.
# POSIX sh (busybox ash).
#
# Test hooks: SYSROOT prefixes /sys and /proc, DEVROOT prefixes /dev.
# In the real initramfs both are empty.

SYSROOT="${SYSROOT:-}"
DEVROOT="${DEVROOT:-/dev}"

detect_firmware() {
    [ -d "$SYSROOT/sys/firmware/efi" ] && echo "uefi" || echo "bios"
}

detect_secure_boot() {
    # EFI variable: 4 attribute bytes, then 1 data byte (1 = enabled).
    #
    # שלושה מצבים ולא שניים. למכונת BIOS אין Secure Boot, ולכן `false`
    # הוא נכון עליה. אבל מכונת UEFI שה-efivars שלה לא נקרא היא מכונה
    # שאיננו יודעים — ו-`false` שם הוא טענה חיובית שגויה, לא שדה חסר.
    # ככה מכונה שה-Secure Boot דלוק בה דיווחה "כבוי" חודשים: efivarfs
    # הוא מודול שלא נארז ב-initramfs, והמאונט נכשל בשקט (#84).
    #
    # `null` אומר "לא הצלחנו לבדוק". ההבדל בינו לבין `false` הוא ההבדל
    # בין השערה לידיעה.
    [ -d "$SYSROOT/sys/firmware/efi" ] || { echo "false"; return; }
    _f="$SYSROOT/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
    [ -r "$_f" ] || { echo "null"; return; }
    _b=$(dd if="$_f" bs=1 skip=4 count=1 2>/dev/null | od -An -tu1 | tr -d ' \n')
    case "$_b" in
        1) echo "true" ;;
        0) echo "false" ;;
        *) echo "null" ;;   # נקרא ולא הובן — עדיין לא ידוע
    esac
}

detect_uuid() {
    trim "$(cat "$SYSROOT/sys/class/dmi/id/product_uuid" 2>/dev/null)"
}

detect_memory_bytes() {
    # MemTotal is in kB.
    awk '/^MemTotal:/ { printf "%.0f", $2 * 1024; exit }' "$SYSROOT/proc/meminfo"
}

list_macs() {
    # All network interfaces except loopback, lowercase-with-colons --
    # the canonical format. Matters for machines with more than one NIC.
    for _d in "$SYSROOT"/sys/class/net/*; do
        [ -e "$_d" ] || continue
        _n=$(basename "$_d")
        [ "$_n" = "lo" ] && continue
        _a=$(cat "$_d/address" 2>/dev/null)
        [ -n "$_a" ] && [ "$_a" != "00:00:00:00:00:00" ] && echo "$_a"
    done
}

logical_block_size() {
    # $1 = block device path. גודל הסקטור הלוגי כפי שהקרנל מדווח אותו,
    # או **כלום** כשאי אפשר לקרוא אותו. הקוראים מחזיקים נתיב
    # (‏`/dev/sda`) ו-sysfs מפתחו לפי שם (`sda`) — הגישור נעשה כאן.
    #
    # ‏`/sys/block` מכיל כוננים שלמים בלבד: נתיב של **מחיצה**
    # (‏`/dev/sda1`) לא יימצא, ויחזור ריק. זה נכון — סכימה היא תכונה
    # של כונן, ושני הקוראים (`build_disk_entry`, ‏`capture_disk`)
    # מעבירים לכאן שם מ-`list_disks` בלבד.
    _bs=$(cat "$SYSROOT/sys/block/${1##*/}/queue/logical_block_size" 2>/dev/null)
    case "$_bs" in
        ''|*[!0-9]*) return ;;              # חסר, ריק, או לא מספר
    esac
    # ‏512 הוא המינימום שהקרנל מדווח; החלוקה ב-8 היא ה-`bs` של ה-`dd`
    # שקורא את החתימה. ערך שאינו עומד בשניים אינו גודל סקטור — ולא
    # מתקנים אותו בשקט.
    [ "$_bs" -ge 512 ] && [ $((_bs % 8)) -eq 0 ] || return
    printf '%s' "$_bs"
}

disk_scheme() {
    # $1 = block device path. "EFI PART" at LBA1 -> gpt; 55aa at 510 -> mbr.
    #
    # ‏LBA1 הוא **הסקטור הלוגי השני**, לא "בייט 512". על כונן 4Kn
    # (‏`logical_block_size=4096` — נפוץ ב-NVMe ארגוני ובדיסקים גדולים)
    # כותרת ה-GPT יושבת בבייט 4096, וקריאה מקובעת ב-512 נוחתת בתוך
    # ה-MBR המגונן. היא לא מוצאת "EFI PART", והשורה הבאה מוצאת `55aa`
    # — שיש **לכל** דיסק GPT — ומכריזה על כונן GPT תקין כ-`mbr` (#126).
    # לכן ה-offset נגזר מ-sysfs ולא מונח.
    #
    # ה-`55aa` דווקא כן נשאר בבייט 510 המוחלט, בכל גודל סקטור: ה-MBR
    # הוא מבנה קשיח של 512 בייט בראש הכונן, לא "הסקטור הראשון".
    _lbs=$(logical_block_size "$1")
    # בלי גודל סקטור אין לדעת איפה LBA1. ‏512 כברירת מחדל היא בדיוק
    # ההנחה שיצרה את הבאג, ו-`none` הוא טענה חיובית ("אין טבלה")
    # שתסמן כונן מלא נתונים כריק — `has_data:false`. עיקרון 5: "לא
    # הצלחנו לבדוק" הוא מצב משלו.
    [ -n "$_lbs" ] || { echo "unknown"; return; }
    _sig=$(dd if="$1" bs=8 skip=$((_lbs / 8)) count=1 2>/dev/null)
    if [ "$_sig" = "EFI PART" ]; then echo "gpt"; return; fi
    _mbr=$(dd if="$1" bs=2 skip=255 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    [ "$_mbr" = "55aa" ] && echo "mbr" || echo "none"
}

disk_serial() {
    # $1 = disk name. sysfs has it for NVMe; SATA needs hdparm.
    _s=$(trim "$(cat "$SYSROOT/sys/block/$1/device/serial" 2>/dev/null)")
    if [ -z "$_s" ]; then
        # ‏wwid לפני hdparm: קיים כמעט לכל דיסק — וירטואלי (נגזר ממזהה
        # ה-VHD, מתחלף עם הקובץ כמו serial פיזי, #16) וגם SATA אמיתי
        # (t10.ATA + הדגם וה-serial). ‏hdparm על דיסק וירטואלי מייצר
        # שגיאת קרנל לכל קריאה — וה-hello הקבוע הציף את מסך הבנייה
        # בשורת hv_storvsc כל שתי שניות וקבר את הממשק (#30).
        _s=$(trim "$(cat "$SYSROOT/sys/block/$1/device/wwid" 2>/dev/null)")
    fi
    if [ -z "$_s" ] && command -v hdparm >/dev/null 2>&1 && [ -z "$SYSROOT" ]; then
        _s=$(hdparm -I "$DEVROOT/$1" 2>/dev/null |
             awk -F: '/Serial Number/ { gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2; exit }')
    fi
    printf '%s' "$_s"
}

port_from_path() {
    # $1 = the sysfs path the disk's `device` link resolves to. Prints the
    # physical SATA slot as the kernel numbers it, or nothing at all.
    #
    # ‏‎/sys/block/sdX/device עובר דרך ataN — והוא, ולא סדר הגילוי, החריץ
    # שאליו הכונן מחובר: ata1 הוא הראשון בבקר, במחשבי השיכפול המגירה
    # העליונה (‏#27). ‏NVMe, דיסק וירטואלי או בקר לא-ATA לא מדווחים חריץ,
    # והקונסולה נופלת חזרה לשם ההתקן — עדיף בלי מספר מאשר מספר שגוי.
    case "$1" in
        */ata[0-9]*)
            _p=${1##*/ata}
            _p=${_p%%/*}
            case "$_p" in
                ''|*[!0-9]*) ;;
                *) printf '%s' "$_p"; return ;;
            esac
            ;;
    esac
    # נפילה אחורה ל-hostN: ה-scsi host הראשון של בקר ATA הוא ata1, ולכן
    # hostN הוא חריץ N+1. רק כשהבקר באמת ATA — על VM עם SCSI
    # (‏storvsc/mptspi/virtio) המספור אינו חריצי מגירות.
    case "$1" in
        */host[0-9]*)
            _h=${1##*/host}
            _h=${_h%%/*}
            case "$_h" in ''|*[!0-9]*) return ;; esac
            case "$(cat "$SYSROOT/sys/class/scsi_host/host$_h/proc_name" 2>/dev/null)" in
                ahci|ata_*|sata_*|pata_*) printf '%s' "$((_h + 1))" ;;
            esac
            ;;
    esac
}

disk_port() {
    # $1 = disk name. Empty when the slot cannot be derived -- never a failure.
    port_from_path "$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null)"
}

build_disk_entry() {
    # $1 = disk name (sda, nvme0n1). Emits one JSON object.
    _name="$1"
    _sectors=$(cat "$SYSROOT/sys/block/$_name/size" 2>/dev/null || echo 0)
    _size=$((_sectors * 512))
    _model=$(trim "$(cat "$SYSROOT/sys/block/$_name/device/model" 2>/dev/null)")
    _serial=$(disk_serial "$_name")
    _removable=$(cat "$SYSROOT/sys/block/$_name/removable" 2>/dev/null || echo 0)
    [ "$_removable" = "1" ] && _removable="true" || _removable="false"
    _scheme=$(disk_scheme "$DEVROOT/$_name")
    _has="false"
    [ "$_scheme" != "none" ] && _has="true"

    _serial_json="null"
    [ -n "$_serial" ] && _serial_json="\"$(json_escape "$_serial")\""

    _port=$(disk_port "$_name")
    _port_json="null"
    [ -n "$_port" ] && _port_json="$_port"

    printf '{"dev":"%s","size_bytes":%s,"model":"%s","serial":%s,"removable":%s,"scheme":"%s","has_data":%s,"port":%s}' \
        "$_name" "$_size" "$(json_escape "$_model")" "$_serial_json" \
        "$_removable" "$_scheme" "$_has" "$_port_json"
}

list_disks() {
    # Real disks only, in /dev order. Loop devices, CD, RAM -- out.
    for _d in "$SYSROOT"/sys/block/*; do
        [ -e "$_d" ] || continue
        _n=$(basename "$_d")
        case "$_n" in
            loop*|ram*|sr*|zram*|dm-*|md*|fd*) continue ;;
        esac
        echo "$_n"
    done
}

build_hello() {
    # Emits the full hello body. Needs IFACE and IP in the environment
    # (init writes them after DHCP).
    #
    # $1 = "false" for a heartbeat hello: the machine says it is still alive
    # but does NOT ask to join an open session. A machine holding an error
    # screen has to stay visible without being counted into the next wave
    # (#64). Any other value -- including none -- is a normal hello.
    _mac=$(cat "$SYSROOT/sys/class/net/$IFACE/address" 2>/dev/null)
    [ -n "$_mac" ] || return 1

    _all=""
    for _m in $(list_macs); do
        [ -n "$_all" ] && _all="$_all,"
        _all="$_all\"$_m\""
    done

    _disks=""
    for _n in $(list_disks); do
        [ -n "$_disks" ] && _disks="$_disks,"
        _disks="$_disks$(build_disk_entry "$_n")"
    done

    _joining=true
    [ "$1" = "false" ] && _joining=false

    printf '{"schema":1,"mac":"%s","all_macs":[%s],"ip":"%s","hostname_current":null,"uuid":"%s","firmware":"%s","secure_boot":%s,"agent_version":"%s","memory_bytes":%s,"joining":%s,"disks":[%s]}' \
        "$_mac" "$_all" "$IP" "$(json_escape "$(detect_uuid)")" \
        "$(detect_firmware)" "$(detect_secure_boot)" "$AGENT_VERSION" \
        "$(detect_memory_bytes)" "$_joining" "$_disks"
}
