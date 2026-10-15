# disklink.sh -- the SATA link and the CRC counter of each disk, in the hello
# (#432; interfaces.md section 2). POSIX sh (busybox ash). Reads sysfs only --
# SYSROOT prefixes it in tests.
#
# Why: on 05/09 every drawer that failed had said so before the round began
# -- "ata1: SATA link up 1.5 Gbps" -- and the one drive that ever linked at
# 6.0 was the one that succeeded. A SATA III SSD that negotiates below the
# port's speed is a cable, a contact or a backplane; the console warns on it
# BEFORE start, it does not block (cloning is always attended).
#
# Principle 5, three ways: a speed that cannot be read is null -- never 6.0
# and never 0; the port's maximum that cannot be read is null too (the server
# then says "unknown", not "ok"); and the CRC count of a disk smartctl did not
# actually read is null, not the 0 the SMART cache stores for it.

_dl_gbps() {
    # $1 = a libata speed file. "6.0 Gbps" -> 6.0; "<unknown>" (link down, or
    # no limit set), a missing file or anything else -> nothing, exit 1.
    _dl_v=$(cat "$1" 2>/dev/null) || return 1
    case "$_dl_v" in
        [0-9]*.[0-9]*' Gbps') printf '%s' "${_dl_v%% *}" ;;
        *) return 1 ;;
    esac
}

_dl_ahci_max() {
    # $1 = scsi host number. CAP.ISS (bits 23:20) of the AHCI controller,
    # which libahci prints as bare hex in ahci_host_caps: 1/2/3 = 1.5/3.0/6.0.
    # This is the port's ceiling; libata's hw_sata_spd_limit stays <unknown>
    # unless the firmware wrote SControl.SPD ("SControl 300" = it did not).
    _dl_c=$(tr -d '\n\r ' < "$SYSROOT/sys/class/scsi_host/host$1/ahci_host_caps" 2>/dev/null)
    case "$_dl_c" in ''|*[!0-9a-fA-F]*) return 1 ;; esac
    case $(( (0x$_dl_c >> 20) & 15 )) in
        1) printf '1.5' ;; 2) printf '3.0' ;; 3) printf '6.0' ;; *) return 1 ;;
    esac
}

disk_link() {
    # $1 = disk name. Prints "<speed> <max>", each 1.5/3.0/6.0 or null. The
    # ataN/hostN pair comes from the same `device` link disk_port reads; a
    # disk that is not behind libata (NVMe, VM SCSI, USB) has neither.
    _dl_p=$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null)
    _dl_spd=null _dl_max=null
    case "$_dl_p" in
        */ata[0-9]*/host[0-9]*)
            _dl_a=${_dl_p##*/ata}; _dl_a=${_dl_a%%/*}
            _dl_h=${_dl_p##*/host}; _dl_h=${_dl_h%%/*}
            case "$_dl_a$_dl_h" in *[!0-9]*) _dl_a='' ;; esac
            if [ -n "$_dl_a" ]; then
                _dl_l="$SYSROOT/sys/class/ata_link/link$_dl_a"
                _dl_spd=$(_dl_gbps "$_dl_l/sata_spd") || _dl_spd=null
                _dl_max=$(_dl_ahci_max "$_dl_h") || _dl_max=null
                # a firmware limit below the controller's is the port's real ceiling
                if _dl_w=$(_dl_gbps "$_dl_l/hw_sata_spd_limit"); then
                    { [ "$_dl_max" = null ] || [ "${_dl_w%.*}${_dl_w#*.}" -lt "${_dl_max%.*}${_dl_max#*.}" ]; } &&
                        _dl_max=$_dl_w
                fi
            fi
            ;;
    esac
    printf '%s %s' "$_dl_spd" "$_dl_max"
}

disk_link_json() {
    # $1 = disk name. Prints ,"link_gbps":X,"link_max_gbps":Y,"crc":Z -- the
    # fragment build_disk_entry (sysinfo.sh) appends; each a number or null.
    _dl_sm=$(disk_link "$1")
    # attribute 199 only when smartctl really read the disk -- the same rule
    # as crc_delta_measure (crcdelta.sh); the cache holds 0 for the rest.
    _dl_crc=null
    if command -v smart_field >/dev/null 2>&1; then
        case "$(smart_field "$1" 2)" in
            ok|health_failed|uncorrectable|pending_sectors|no_health)   # #1366: the F1 reasons read it too
                _dl_crc=$(smart_field "$1" 6)
                case "$_dl_crc" in ''|*[!0-9]*) _dl_crc=null ;; esac ;;
        esac
    fi
    printf ',"link_gbps":%s,"link_max_gbps":%s,"crc":%s' "${_dl_sm% *}" "${_dl_sm#* }" "$_dl_crc"
}
