# slotnames.sh -- the operator's name for each SATA slot of this machine (#704).
# POSIX sh (busybox ash).
#
# The console names a connection, not a disk: "sata:<port>" (the ataN of
# interface 2) keeps its name when the disk in that drawer is replaced. hello
# answers them as `slot_names` ({"sata:1": "..."}). The cloner screen draws
# the name instead of "disk N"; with no name it stays "disk N" (SATA 0 = disk
# 1). A label only -- selection and target_ports stay by port.
#
# Only sata: keys reach the screen: it lays out one card per SATA port, and a
# disk with no port (NVMe, keyed serial:) has no card there to name.

slot_names_refresh() {
    # The last hello answer -> $RUN_DIR/slot_names, one "port|name" per line
    # after a first line "ok" -- the evidence jq read the answer whole. An
    # answer without the field (a poll response, an older server) or one jq
    # cannot read is not "no names": the previous file stays (principle 5).
    # 0 = refreshed.
    _sn_n="$RUN_DIR/slot_names.next"
    jq -r 'if (.slot_names | type) != "object" then error("no slot_names") else
        "ok", (.slot_names | to_entries[] | select(.key | test("^sata:[1-9][0-9]?$")) |
        "\(.key | ltrimstr("sata:"))|\(.value | tostring | gsub("[\\r\\n|]"; " "))") end' \
        "$RUN_DIR/response.json" > "$_sn_n" 2>/dev/null
    [ "$(head -n 1 "$_sn_n" 2>/dev/null)" = ok ] || { rm -f "$_sn_n"; return 1; }
    mv "$_sn_n" "$RUN_DIR/slot_names"
}

drawer_slots_refresh() {
    # Preserve the previous mapping unless jq reads a complete array.
    _sn_n="$RUN_DIR/drawer_slots.next"
    jq -r 'if (.drawer_slots | type) != "array" then error("no drawer_slots") else
        "ok", (.drawer_slots | to_entries[] |
        select(.value | type == "string") |
        select((.value | test("^nvme:[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\\.[0-7]$")) or
               ((.value | test("^sata:[1-9][0-9]?$")) and
                ((.value | ltrimstr("sata:") | tonumber) <= 32))) |
        "\(.key + 1)|\(.value)") end' \
        "$RUN_DIR/response.json" > "$_sn_n" 2>/dev/null
    [ "$(head -n 1 "$_sn_n" 2>/dev/null)" = ok ] || { rm -f "$_sn_n"; return 1; }
    mv "$_sn_n" "$RUN_DIR/drawer_slots"
}

slot_names_records() {
    # cslot_name=<port>|<name> per named slot, for the cloner state file.
    [ -f "$RUN_DIR/slot_names" ] || return 0
    sed -n '2,$s/^/cslot_name=/p' "$RUN_DIR/slot_names"
}
