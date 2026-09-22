#!/usr/bin/env bash
#
# firstboot-answers.sh -- stage B from the live installer's answers (#1190).
# Sourced by firstboot.sh when /etc/imagectl/answers exists: the person
# already answered everything on the server's screen, so there is no wizard
# to wait for. This does what the wizard's apply does (server/wizard/core.py
# `_apply`), in the same order and with the same flags, then verifies the
# boot payload and writes the stamp -- and only then starts the DCUI.
#
# The admin password comes from /etc/imagectl/answers.secret through a file
# descriptor (never argv/env/log), and the secret is shredded right after.
# Every step reports by name; a failed step is a failed first boot that the
# DCUI shows, not a server that "came up" half-configured.

ANSWERS="${ANSWERS:-/etc/imagectl/answers}"
ANSWERS_SECRET="${ANSWERS_SECRET:-/etc/imagectl/answers.secret}"
APP_DIR="${APP_DIR:-/opt/imagectl}"
DATA_DIR="${DATA_DIR:-/var/lib/imagectl}"

answers_get() { sed -n "s/^$1=//p" "$ANSWERS" | head -n1; }

# Resolve the management NIC by MAC first (names can change between the live
# kernel and the installed one), then by name; both must agree with a live NIC.
answers_servers_if() {
    local mac name path
    mac=$(answers_get servers_mac); name=$(answers_get servers_if)
    if [[ -n "$mac" ]]; then
        for path in /sys/class/net/*; do
            [[ "$(cat "$path/address" 2>/dev/null)" == "$mac" ]] && { basename "$path"; return 0; }
        done
    fi
    [[ -n "$name" && -e "/sys/class/net/$name" ]] && { printf '%s\n' "$name"; return 0; }
    return 1
}

firstboot_from_answers() {
    local role primary mode host servers_if argv rc
    [[ -s "$ANSWERS_SECRET" ]] || fail answers-secret-missing "$ANSWERS exists but $ANSWERS_SECRET is missing or empty"
    role=$(answers_get role); mode=$(answers_get servers_mode); host=$(answers_get hostname); primary=$(answers_get primary_url)
    [[ "$role" == standalone || "$role" == secondary ]] || fail answers-invalid "role in $ANSWERS is '$role'"
    [[ "$mode" == dhcp || "$mode" == static ]] || fail answers-invalid "servers_mode in $ANSWERS is '$mode'"
    [[ -n "$host" ]] || fail answers-invalid "hostname missing from $ANSWERS"
    [[ -n "$(answers_get admin_user)" ]] || fail answers-invalid "admin_user missing from $ANSWERS"
    servers_if=$(answers_servers_if) || fail answers-nic-missing "no live NIC matches servers_mac/servers_if from $ANSWERS"
    log "stage B from answers: role=$role nic=$servers_if ($mode) hostname=$host"
    printf 'state=installing\ntimestamp=%s\nrole_default=%s\nsource=answers\n' "$(date -u +%FT%TZ)" "$role" > "$STATUS"

    hostnamectl set-hostname "$host" || fail hostname-failed "hostnamectl set-hostname $host failed"
    # read back, not exit code: hostnamectl without a system bus (no dbus) returns 1 with the
    # name already right in /etc/hostname from the engine -- and the reverse would be silent
    [[ "$(hostname)" == "$host" ]] || fail hostname-failed "hostname reads back '$(hostname)', not $host"
    argv=(bash "$SRC/install/setup-boot-server.sh" --servers-if "$servers_if" --servers-mode "$mode")
    if [[ "$mode" == static ]]; then
        argv+=(--servers-address "$(answers_get servers_addr)" --servers-netmask "$(answers_get servers_mask)")
        [[ -n "$(answers_get servers_gw)" ]] && argv+=(--servers-gateway "$(answers_get servers_gw)")
        [[ -n "$(answers_get servers_dns)" ]] && argv+=(--servers-dns "$(answers_get servers_dns)")
    fi
    argv+=(--console-host "$servers_if" --storage-role "$role")
    [[ "$role" == secondary ]] && argv+=(--primary-url "$primary")
    argv+=(--admin-user "$(answers_get admin_user)" --admin-pass-fd 3)
    # answers.secret is `admin_pass=<value>` (interfaces.md); setup-boot-server
    # reads fd 3 as the bare password. Feeding it the whole file made the
    # admin password literally "admin_pass=..." (ESXi, 21/09) -- the value
    # only, and an empty value is a named failure, not an empty password.
    secret=$(sed -n 's/^admin_pass=//p' "$ANSWERS_SECRET" | head -n 1)
    [[ -n "$secret" ]] || fail answers-secret-missing "$ANSWERS_SECRET has no admin_pass= line"
    set +e
    "${argv[@]}" 3< <(printf '%s\n' "$secret") >>"$BUILD_LOG" 2>&1
    rc=$?
    set -e
    unset secret
    shred -u "$ANSWERS_SECRET" 2>/dev/null || rm -f "$ANSWERS_SECRET"
    [[ $rc -eq 0 ]] || fail installer-failed "setup-boot-server.sh exited $rc; see $BUILD_LOG"

    # setup-boot-server writes interfaces.d/imagectl-<nic> and deliberately does
    # no ifup (the web wizard is connected through that NIC); here nobody is,
    # and networking.service ran before the file existed -- without this the
    # first DCUI says "link down" until the next restart (ESXi, 21/09).
    if ifup "$servers_if" >>"$BUILD_LOG" 2>&1; then
        addr=$(ip -o -4 addr show dev "$servers_if" scope global 2>/dev/null | awk 'NR == 1 {print $4}')
        log "nic $servers_if: ${addr:-up, no IPv4 address yet}"
    else
        log "[nic-up-failed] ifup $servers_if failed (see $BUILD_LOG); the console is reachable after a restart"
    fi

    bash "$APP_DIR/install/verify-boot-payload.sh" --app-dir "$APP_DIR" --http-root "$HTTP_ROOT" \
        --server-url http://127.0.0.1:8080 >>"$BUILD_LOG" 2>&1 \
        || fail verify-failed "verify-boot-payload.sh failed; the installation is not marked complete (see $BUILD_LOG)"
    printf 'state=done\ntimestamp=%s\nservers_if=%s\nrole=%s\nsource=answers\n' "$(date -u +%FT%TZ)" "$servers_if" "$role" > "$STATUS"
    touch "$STAMP"
    log "stage B complete from answers: console on $servers_if, role $role"
}
