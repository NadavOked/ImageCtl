#!/bin/sh
# gui-bridge.sh -- the install GUI's only door to the engine (#1190 part 2).
# POSIX sh (busybox ash). The native GUI fork/execs this with ONE action in
# argv and a JSON object on stdin (the form as the person filled it), and
# reads key=value lines from stdout -- the same exchange main.c already
# uses for --auth-cmd. Exit 0 = the call itself worked (validation errors are
# data: errors.<field>=...); non-zero = the bridge could not do its job, and
# the GUI says "the installer is not available (<action>)", never "ok".
#
#   inventory      disk= lines (imagectl-install --inventory) + nic= lines
#   validate       the form (+ step) -> errors.<field>=... or nothing
#   apply          writes the answers (0600), starts the engine, job=
#   progress       the engine's state file, plus output= for the log tail
#   check-primary  https://<primary>/health/live within 5 s
#   finish         what the done screen shows after a reboot-less handoff
#   eject          eject the install medium (reported, never fatal)
#   reboot         reboot -f -- only the GUI's "restart" button reaches here
#   boot-local     eject + reboot, for a disk that already carries ImageCtl
#
# No jq-less JSON parsing: jq is in the image (agent/ uses it). Every value
# the GUI sends is read with `jq -r`, never eval'd.
set -u
LC_ALL=C; export LC_ALL

case "$0" in */*) BRIDGE_DIR=${0%/*} ;; *) BRIDGE_DIR=. ;; esac
BRIDGE_DIR=$(CDPATH="" cd "$BRIDGE_DIR" && pwd) || exit 2
ENGINE="${ENGINE:-$BRIDGE_DIR/imagectl-install}"
RUN_DIR="${RUN_DIR:-/run/imagectl}"
STATE_FILE="${STATE_FILE:-$RUN_DIR/install.state}"
ANSWERS="${ANSWERS:-$RUN_DIR/answers}"
ENGINE_LOG="${ENGINE_LOG:-$RUN_DIR/install.log}"
DHCP_DIR="${DHCP_DIR:-$RUN_DIR/dhcp}"
SYSROOT="${SYSROOT:-}"
MEDIA_FILE="${MEDIA_FILE:-$RUN_DIR/install-media}"

ACTION=${1:-}; [ -n "$ACTION" ] || { echo 'usage: gui-bridge.sh <action> < form.json' >&2; exit 2; }
INPUT=$(cat)
[ -n "$INPUT" ] || INPUT='{}'
command -v jq >/dev/null 2>&1 || { echo 'gui-bridge: jq is missing from this image' >&2; exit 3; }

field() { printf '%s' "$INPUT" | jq -r --arg k "$1" '.[$k] // "" | tostring' 2>/dev/null; }
emit() { printf '%s=%s\n' "$1" "$2"; }

# --- NIC rows: name|model|mac|link|current|source|address|netmask|gateway|dns --
nic_model() {
    _nm_dev="$SYSROOT/sys/class/net/$1/device"
    [ -r "$_nm_dev/vendor" ] && [ -r "$_nm_dev/device" ] || { printf -- '-'; return; }
    _nm_v=$(cat "$_nm_dev/vendor"); _nm_d=$(cat "$_nm_dev/device")
    case "$_nm_v" in
        0x8086) printf 'Intel %s' "$_nm_d" ;; 0x15ad) printf 'VMware %s' "$_nm_d" ;;
        0x10ec) printf 'Realtek %s' "$_nm_d" ;; 0x14e4) printf 'Broadcom %s' "$_nm_d" ;;
        0x1af4) printf 'virtio %s' "$_nm_d" ;; *) printf '%s %s' "$_nm_v" "$_nm_d" ;;
    esac
}

nic_rows() {
    for _d in "$SYSROOT"/sys/class/net/*; do
        _n=${_d##*/}; [ "$_n" = lo ] && continue
        _mac=$(cat "$_d/address" 2>/dev/null || printf '')
        _oper=$(cat "$_d/operstate" 2>/dev/null || printf 'unknown')
        _carrier=$(cat "$_d/carrier" 2>/dev/null || printf '')
        # #1183 wording: down = not brought up, no carrier = no cable, else connected
        if [ "$_oper" = down ]; then _link='לא מודלק'
        elif [ "$_carrier" = 1 ]; then _link='מחובר'
        else _link='אין קישור'; fi
        _addr=; _gw=; _dns=; _state=
        if [ -r "$DHCP_DIR/$_n" ]; then
            _state=$(sed -n 's/^state=//p' "$DHCP_DIR/$_n" | head -n1)
            _addr=$(sed -n 's/^address=//p' "$DHCP_DIR/$_n" | head -n1)
            _gw=$(sed -n 's/^gateway=//p' "$DHCP_DIR/$_n" | head -n1)
            _dns=$(sed -n 's/^dns=//p' "$DHCP_DIR/$_n" | head -n1)
        fi
        if [ -z "$_addr" ]; then
            _addr=$(ip -o -4 addr show dev "$_n" scope global 2>/dev/null | sed -n 's/.* inet \([0-9./]*\).*/\1/p' | head -n1)
        fi
        case "$_state" in
            lease) _current=$_addr; _source="DHCP · שער $_gw" ;;
            no-offer) _current='—'; _source='אין כתובת (אין DHCP ברשת הזו)' ;;
            no-carrier) _current='—'; _source='—' ;;
            *) if [ -n "$_addr" ]; then _current=$_addr; _source='כתובת קיימת'; else _current='—'; _source='—'; fi ;;
        esac
        _ip=${_addr%%/*}; _mask=
        case "$_addr" in */24) _mask=255.255.255.0 ;; */16) _mask=255.255.0.0 ;; */8) _mask=255.0.0.0 ;; */22) _mask=255.255.252.0 ;; */23) _mask=255.255.254.0 ;; */25) _mask=255.255.255.128 ;; esac
        emit nic "$_n|$(nic_model "$_n")|$_mac|$_link|$_current|$_source|$_ip|$_mask|$_gw|$_dns"
    done
}

# --- the form -> an answers file (never printed; the password stays in the file) --
write_answers() {
    _wa_out=$1; _wa_if=$(field interface); _wa_mac=
    [ -n "$_wa_if" ] && _wa_mac=$(cat "$SYSROOT/sys/class/net/$_wa_if/address" 2>/dev/null || printf '')
    _wa_user=$(field admin_user); [ -n "$_wa_user" ] || _wa_user="admin"
    umask 077
    {
        emit disk "$(field disk)"
        emit role "$(field role)"
        emit primary_url "$(field primary_url)"
        emit servers_if "$_wa_if"
        emit servers_mac "$_wa_mac"
        emit servers_mode "$(field mode)"
        if [ "$(field mode)" = static ]; then
            emit servers_addr "$(field address)"; emit servers_mask "$(field netmask)"
            emit servers_gw "$(field gateway)"; emit servers_dns "$(field dns)"
        else
            emit servers_addr ''; emit servers_mask ''; emit servers_gw ''; emit servers_dns ''
        fi
        emit hostname "$(field hostname)"
        emit admin_user "$_wa_user"
        # Before the admin screen the password is legitimately empty; the engine's
        # own check ("admin_pass must not be empty") belongs to step 4+, not to
        # a disk/role/network validation. A placeholder keeps the dry-run honest
        # for every other field; apply always sends the real one.
        if [ -n "$(field password)" ] || [ "${_wa_placeholder:-0}" = 0 ]; then emit admin_pass "$(field password)"; else emit admin_pass "not-yet-asked-1!"; fi
    } > "$_wa_out"
}

# The engine's failure code -> the GUI field that owns it (errors.<field>=).
field_for_code() {
    case "$1" in
        invalid-disk|iso-target|missing-key) printf disk ;;
        invalid-role) printf role ;; invalid-primary-url) printf primary_url ;;
        invalid-interface|invalid-mac|mac-mismatch|interface-check) printf interface ;;
        invalid-network-mode) printf mode ;; invalid-address) printf address ;;
        invalid-netmask) printf netmask ;; invalid-gateway) printf gateway ;; invalid-dns) printf dns ;;
        invalid-network) printf address ;; invalid-hostname) printf hostname ;;
        invalid-admin-user) printf admin_user ;; invalid-admin-password) printf password ;;
        *) printf disk ;;
    esac
}

password_errors() {
    _pe_p=$(field password); _pe_c=$(field password_confirm); _pe_bad=0
    [ "${#_pe_p}" -ge 8 ] || { emit errors.password 'לפחות 8 תווים'; _pe_bad=1; }
    case "$_pe_p" in *[A-Za-z]*) ;; *) emit errors.password 'נדרשת לפחות אות אחת'; _pe_bad=1 ;; esac
    case "$_pe_p" in *[0-9]*) ;; *) emit errors.password 'נדרשת לפחות ספרה אחת'; _pe_bad=1 ;; esac
    case "$_pe_p" in *[!A-Za-z0-9]*) ;; *) emit errors.password 'נדרש לפחות תו מיוחד אחד'; _pe_bad=1 ;; esac
    [ "$_pe_p" = "$_pe_c" ] || { emit errors.password_confirm 'הסיסמאות שהזנת אינן תואמות'; _pe_bad=1; }
    return $_pe_bad
}

# Run the engine's own validation (--dry-run: no command touches a disk) on
# the form as an answers file; the first failure names the field.
engine_validate() {
    _ev_tmp="$RUN_DIR/validate.$$"; write_answers "$_ev_tmp"
    _ev_err=$("$ENGINE" "$_ev_tmp" --dry-run --state "$RUN_DIR/validate.state.$$" 2>&1 >/dev/null); _ev_rc=$?
    rm -f "$_ev_tmp" "$RUN_DIR/validate.state.$$"
    [ "$_ev_rc" -eq 0 ] && return 0
    _ev_code=$(printf '%s\n' "$_ev_err" | sed -n 's/^installer: \[\([a-z-]*\)\].*/\1/p' | head -n1)
    _ev_msg=$(printf '%s\n' "$_ev_err" | sed -n 's/^installer: \[[a-z-]*\] //p' | head -n1)
    [ -n "$_ev_msg" ] || _ev_msg=$(printf '%s\n' "$_ev_err" | tail -n1)
    emit "errors.$(field_for_code "${_ev_code:-unknown}")" "$_ev_msg"
    return 1
}

do_inventory() {
    "$ENGINE" --inventory || { echo 'gui-bridge: engine inventory failed' >&2; return 4; }
    nic_rows
    _di_role=standalone; _di_if=
    for _d in "$SYSROOT"/sys/class/net/*; do _n=${_d##*/}; [ "$_n" = lo ] && continue
        [ -r "$DHCP_DIR/$_n" ] && grep -q '^state=lease$' "$DHCP_DIR/$_n" && { _di_if=$_n; break; }; done
    emit role "$_di_role"; emit primary_url ''; emit interface "$_di_if"; emit mode dhcp
    emit hostname imagectl-server; emit admin_user admin; emit rerun 0
}

do_validate() {
    _dv_step=$(field step); _dv_bad=0
    case "$_dv_step" in
        ''|5) engine_validate || _dv_bad=1; password_errors || _dv_bad=1 ;;
        4) password_errors || _dv_bad=1 ;;
        *) _wa_placeholder=1; engine_validate || _dv_bad=1; _wa_placeholder=0 ;;
    esac
    return 0
}

do_apply() {
    if [ -s "$STATE_FILE" ] && grep -qE '^state=(partitioning|bootstrap|packages|bootloader|finishing)$' "$STATE_FILE"; then
        emit job running; return 0
    fi
    engine_validate || return 0
    password_errors || return 0
    mkdir -p "$RUN_DIR" && write_answers "$ANSWERS" || { echo 'gui-bridge: cannot write the answers' >&2; return 4; }
    printf 'state=partitioning\npct=0\ntitle=starting\n' > "$STATE_FILE"
    ( "$ENGINE" "$ANSWERS" --state "$STATE_FILE" > "$ENGINE_LOG" 2>&1 ) &
    emit job "$!"
}

do_progress() {
    [ -r "$STATE_FILE" ] || { emit state ready; return 0; }
    cat "$STATE_FILE"
    [ -r "$ENGINE_LOG" ] && tail -n 20 "$ENGINE_LOG" | sed 's/\x1b\[[0-9;]*m//g; s/^/output=/'
    return 0
}

do_check_primary() {
    _cp_url=$(field primary_url)
    case "$_cp_url" in https://*:8443) ;; *) emit ok 0; emit error 'כתובת הראשי חייבת להיות https://HOST:8443'; return 0 ;; esac
    _cp_body=$(curl -k -s --max-time 5 "$_cp_url/health/live" 2>/dev/null) || {
        emit ok 0; emit error "התקשרות אל ${_cp_url#https://} בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה."; return 0; }
    _cp_ver=$(printf '%s' "$_cp_body" | jq -r '.version // ""' 2>/dev/null)
    _cp_name=$(printf '%s' "$_cp_body" | jq -r '.hostname // .name // ""' 2>/dev/null)
    _cp_fp=$(printf '%s' "$_cp_body" | jq -r '.fingerprint // ""' 2>/dev/null)
    emit ok 1; emit name "$_cp_name"; emit version "$_cp_ver"; emit fingerprint "$_cp_fp"
}

do_finish() {
    [ -r "$STATE_FILE" ] && cat "$STATE_FILE" || emit state ready
    _df_if=$(sed -n 's/^servers_if=//p' "$ANSWERS" 2>/dev/null | head -n1)
    _df_addr=$(sed -n 's/^servers_addr=//p' "$ANSWERS" 2>/dev/null | head -n1)
    [ -n "$_df_addr" ] || _df_addr=$(sed -n 's/^address=//p' "$DHCP_DIR/$_df_if" 2>/dev/null | head -n1)
    [ -n "$_df_addr" ] && emit console_url "https://${_df_addr%%/*}:8081"
    emit user "$(sed -n 's/^admin_user=//p' "$ANSWERS" 2>/dev/null | head -n1)"
}

do_eject() {
    _de_dev=$(cat "$MEDIA_FILE" 2>/dev/null || printf '')
    [ -n "$_de_dev" ] || { emit ejected 0; emit error 'no install medium recorded'; return 0; }
    umount /cdrom 2>/dev/null
    if eject "$_de_dev" 2>/dev/null; then emit ejected 1; else emit ejected 0; emit error "eject $_de_dev failed (remove the medium by hand)"; fi
}

case "$ACTION" in
    inventory|interfaces) do_inventory ;;
    validate) do_validate ;;
    apply) do_apply ;;
    progress) do_progress ;;
    check-primary) do_check_primary ;;
    finish) do_finish ;;
    eject) do_eject ;;
    reboot) sync; reboot -f ;;
    boot-local) do_eject; sync; reboot -f ;;
    *) echo "gui-bridge: unknown action $ACTION" >&2; exit 2 ;;
esac
