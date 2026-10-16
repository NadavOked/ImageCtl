# guistate.sh -- one writer, atomic GUI snapshots. POSIX sh.
gui_get() {
    _sc=$(console_get "$1" "$2") || return 1
    [ "$_sc" = 200 ] || { printf 'GUI GET %s: HTTP %s\n' "$1" "$_sc" >&2; return 1; }
}

gui_state() {
    http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$GUI_DIR/station.json" || return 1
    # Delimiters cannot be represented by the GUI's record format.
    # Replace them for display only; action identifiers are validated separately.
    jq -er '
      def clean: tostring | gsub("[\\r\\n|]"; " ");
      if (.known | type) != "boolean" or (.disks | type) != "array" then error("bad station state") else . end |
      if .known == false then "message=המחשב אינו רשום במערכת|פנו לצוות ה-IT"
      else
        (.disks[] | select(.removable == false) |
          "disk=" + ([.dev,.model,.size_bytes,(if .has_data then 1 else 0 end),0] | map(clean) | join("|"))),
        (if .task == null then "task=none" else .task | .state as $ts |
          (if .state == "cancelled" then "task=failed"
           elif (["pending","running","done","failed"]|index($ts)) != null then "task=\(.state)"
           else error("unknown task state") end), "task_name=\(.name|clean)", "task_disk=\(.disk|clean)",
          "task_direct=\(if .type == "direct_send" then 1 else 0 end)",
          "task_error=\((.error // "")|clean)", "bytes=\(.bytes_written // 0)",
          (if .source_progress != null then
             "pct=\((100 * .source_progress.blocks_read / .source_progress.blocks_total)|floor)",
             "partition=\(.source_progress.partition)"
           elif (.bytes_total // 0) > 0 then "pct=\((100 * .bytes_written / .bytes_total)|floor)"
           else "pct=-1" end) end)
      end' "$GUI_DIR/station.json" > "$GUI_DIR/state.next" || return 1
    # #1495: each local disk's drawer by the mapping -- the capture list's "דיסק N"
    disk_slot_records >> "$GUI_DIR/state.next" || return 1
    # #880: menu_class=0|1 -- the class card only when the last hello said
    # so (class_deploy_on, buildmenu.sh). Always written: an absent record
    # reads as 0 in state.c, so the card never shows by omission.
    if class_deploy_on; then _mc=1; else _mc=0; fi
    printf 'menu_class=%s\n' "$_mc" >> "$GUI_DIR/state.next" || return 1
    # v1 without the toolbox (Nadav, 19/09): menu_tools=0|1 from tools_on
    # (buildmenuitems.sh), always written for the same reason as menu_class.
    if tools_on; then _mt=1; else _mt=0; fi
    printf 'menu_tools=%s\n' "$_mt" >> "$GUI_DIR/state.next" || return 1
    gui_header_state "$GUI_DIR/station.json" >> "$GUI_DIR/state.next" || return 1
    gui_shrink_state >> "$GUI_DIR/state.next" || return 1   # #928: the capture's shrink question
    gui_ask_state >> "$GUI_DIR/state.next" || return 1      # 11/10: grow-back / end of capture (askgui.sh)
    gui_hello_state >> "$GUI_DIR/state.next" || return 1
    # #434: the power-off countdown -- the epoch idle.sh wrote; absent = none.
    [ -s "$RUN_DIR/idle.warn" ] && { printf 'idle_poweroff_at=%s\n' "$(cat "$RUN_DIR/idle.warn")" >> "$GUI_DIR/state.next" || return 1; }
    _sm=$(cat "$GUI_DIR/mode") || return 1
    case "$_sm" in
        restore)
            # #706: the images allowed for THIS machine (server-filtered, in
            # station.json from /state) become the picker's image= records. The
            # non-removable disk= records are already emitted above (target panel).
            jq -r 'if (.allowed_images | type) != "array" then empty else
                .allowed_images[] |
                "image=" + ([.id, .name, .folder]
                  | map((. // "") | tostring | gsub("[\\r\\n|]"; " ")) | join("|"))
              end' "$GUI_DIR/station.json" >> "$GUI_DIR/state.next" || return 1 ;;
        capture)
            gui_get folders "$GUI_DIR/folders.json" || return 1
            jq -r 'if type != "array" then error("bad folders") else .[] |
                if (.name|test("[\\r\\n|]")) then error("unrepresentable folder") else "folder=\(.name)" end end' \
                "$GUI_DIR/folders.json" >> "$GUI_DIR/state.next" || return 1 ;;
        room|direct)
            # #715: `direct` is the room screen without an image picker --
            # the same room records, no image= records at all.
            gui_get room "$GUI_DIR/room.json" || return 1
            if [ "$_sm" = room ]; then
                gui_get images "$GUI_DIR/images.json" || return 1
                jq -r 'if type != "array" then error("bad images") else .[] |
                    "image=" + ([.id,.name,.folder] | map(tostring | gsub("[\\r\\n|]"; " ")) | join("|")) end' \
                    "$GUI_DIR/images.json" >> "$GUI_DIR/state.next" || return 1
            fi
            # #1269: the models the driver library has -- the "add drivers"
            # switch. driver_models=-1 = not read: the switch says so, and an
            # unread list is never an empty one (principle 5) nor the end of
            # the whole room screen.
            if gui_get room/driver-models "$GUI_DIR/driver-models.json" &&
                jq -r 'if type != "array" then error("bad driver models") else "driver_models=\(length)",
                    (.[] | "driver_model=" + ([.vendor, .model] | map((. // "") | tostring | gsub("[\\r\\n|]"; " ")) | join("|"))) end' \
                    "$GUI_DIR/driver-models.json" > "$GUI_DIR/driver-models.next"; then
                cat "$GUI_DIR/driver-models.next" >> "$GUI_DIR/state.next" || return 1
            else
                printf 'driver_models=-1\n' >> "$GUI_DIR/state.next" || return 1
            fi
            jq -r '
              def clean: ((. // "") | tostring | gsub("[\\r\\n|]"; " "));
              # #1495: a connection as the console writes it (console.js connText)
              def conn: if test("^sata:[0-9]+$") then "SATA \((ltrimstr("sata:") | tonumber) - 1)"
                elif startswith("nvme:") then "NVMe \(ltrimstr("nvme:") | ltrimstr("0000:"))" else "" end;
              def row: "machine=" + ([.name,.mac,(if .awake then 1 else 0 end),
                (if .joined then 1 else 0 end),.fresh_drawers,.state,
                (if (.bytes_total // 0)>0 then (100*.bytes_written/.bytes_total|floor) else -1 end),0,
                (.error // ""),(if .local_copy == true then 1 else 0 end)] | map(clean) | join("|"));
              if (.machines|type) != "array" then error("bad room") else
              "room_label=" + (.group_label | clean),
              (if .round == null then empty else .round |
                "round=" + ([.image_name,.wave_number,(if .wave_state == "open" then 1 else 0 end),
                  .written_drives,.target_drives,.ready_drives,.remaining_drives,
                  (.elapsed_s // -1),(.rate_bps // -1),(.eta_s // -1)] |
                  map(tostring|gsub("[\\r\\n|]";" "))|join("|")) end),
              # #432: the orange lines before "start"; #65: who the wave waits for.
              (if .round == null then empty else (.round.precheck // [])[] | "room_warn=" + (.text_he | clean) end),
              (if .round == null then empty else (.round.missing // [])[] | "room_missing=" +
                ([.mac, .name, .reason, (if .can_poweroff then 1 else 0 end), .text_he,
                  (if .local_copy == true then 1 else 0 end)] | map(clean) | join("|")) end),
              (.machines[] | row),
              (.machines | to_entries[] |
                .key as $mi | .value as $m |
                "machine_drawers=" +
                  ([$mi, (($m.drawer_count // 1) | if . < 1 then 1 elif . > 8 then 8 else . end)]
                   | map(clean) | join("|")),
                (($m.drawer_list // [])[] |
                  "room_drawer=" +
                    ([$mi, (.port // 0), .serial, .model, (.size_bytes // 0),
                      (.smart // "unchecked"),
                      # #1235: fresh null = no serial (#105) -> -1, "cannot identify".
                      (if .fresh == null then -1 elif .fresh then 1 else 0 end),
                      (if .selected then 1 else 0 end),
                      # #1269: the drivers outcome; "" = the step did not run.
                      (.drivers.state // ""),
                      (.drivers as $d | if $d == null then "" elif $d.state == "staged"
                        then ($d.packages // [] | join(", ")) elif $d.state == "failed"
                        then ($d.error // "") else ($d.reason // "") end),
                      # #704: the operator name of the slot of this drawer; "" = "disk N".
                      (.slot_name // ""),
                      # #432: ok / slow / unknown ("" = an older server); only slow colours.
                      (.link_state // ""),
                      # #1366: the CRC count when over crc_warn_threshold, "" = not (or unread).
                      (if .crc_suspect == true then (.crc // "") else "" end),
                      # #1495: the drawer by the mapping (null = not in it -> 0; an
                      # older server without the field -> "" = by port), and the connection.
                      (if has("drawer") then (.drawer // 0) else "" end),
                      (if (.port | type) == "number" then "sata:\(.port)"
                       elif (.pci | type) == "string" then "nvme:\(.pci)" else "" end | conn)]
                     | map(clean) | join("|"))),
                # #1495: every drawer of the mapping, empty ones too, with its slot name.
                (($m.drawer_slots // []) | to_entries[] | .value as $k |
                  "room_slot=" + ([$mi, (.key + 1), ($k | conn), (($m.slot_names // {})[$k] // "")]
                   | map(clean) | join("|"))),
                # #704: names of the SATA slots, empty ones included.
                (($m.slot_names // {}) | to_entries[] | select(.key | test("^sata:[1-9][0-9]?$")) |
                  "room_slot_name=" + ([$mi, (.key | ltrimstr("sata:")), .value] | map(clean) | join("|")))) end' \
                "$GUI_DIR/room.json" >> "$GUI_DIR/state.next" || return 1 ;;
        classes)
            http_get "$SERVER/api/v1/agent/groups" > "$GUI_DIR/classes.json" || return 1
            http_get "$SERVER/api/v1/agent/sessions/active" > "$GUI_DIR/session.json" || return 1
            jq -r 'if type != "array" then error("bad classes") else .[] |
                "class=" + ([.id,.label,.machines]|map(tostring|gsub("[\\r\\n|]";" "))|join("|")) end' \
                "$GUI_DIR/classes.json" >> "$GUI_DIR/state.next" || return 1
            jq -r 'if has("session")|not then error("bad active session") else .session |
                select(. != null and .group_role == "classroom") |
                "session=" + ([.image_name,.prefix,.group_label,(if .state == "open" then 1 else 0 end),
                .joined,.expected_clients,.starts_in_seconds] |
                map(tostring|gsub("[\\r\\n|]";" "))|join("|")),
                (.members[] | "machine=" + ([.name,.mac,1,1,0,.state,
                  (if (.bytes_total // 0)>0 then (100*.bytes_written/.bytes_total|floor) else -1 end),0,
                  (.error // "")] | map((. // "")|tostring|gsub("[\\r\\n|]";" "))|join("|"))) end' \
                "$GUI_DIR/session.json" >> "$GUI_DIR/state.next" || return 1
            cp "$GUI_DIR/session.json" "$GUI_DIR/session-shown.next" &&
                mv "$GUI_DIR/session-shown.next" "$GUI_DIR/session-shown.json" || return 1 ;;
    esac
    if [ -f "$GUI_DIR/error" ]; then cat "$GUI_DIR/error" >> "$GUI_DIR/state.next" || return 1; fi
    # #410: when this snapshot was taken. The screen prints "updated Ns ago"
    # from it, so a state file that stopped changing shows a growing age
    # instead of a picture that looks current (rule 5).
    printf 'updated=%s\n' "$_now" >> "$GUI_DIR/state.next" || return 1
    mv "$GUI_DIR/state.next" "$GUI_DIR/state"
}

gui_server_addr() {
    # #1252: server_addr=<host of $SERVER> -- imagectl.server, which init
    # normalised to a URL. Known without the server, so the failure snapshot
    # and the cloner screen carry it too. Empty $SERVER = empty = unknown.
    _ga=${SERVER:-}; _ga=${_ga#*://}; _ga=${_ga%%/*}; _ga=${_ga%:*}
    printf 'server_addr=%s\n' "$(printf '%s' "$_ga" | tr '\r\n|' '   ')"
}

gui_header_state() {
    # The build state and cached cloner hello carry the same header identity.
    # Missing/null stays empty: the screen says unknown rather than guessing.
    if [ -r "$1" ]; then
        jq -r 'def c: (. // "") | tostring | gsub("[\\r\\n|]"; " ");
            "machine_name=\(.name | c)", "server_name=\(.server_name | c)",
            "server_version=\(.server_version | c)", "tz=\(.tz | c)"' "$1" || return 1
    else
        printf 'machine_name=\nserver_name=\nserver_version=\ntz=\n'
    fi
    gui_server_addr || return 1
    printf 'agent_version=%s\n' "${AGENT_VERSION:-unknown}"
}

gui_hello_state() {
    # hello_age=/hello_rc= from $RUN_DIR/hello.state (hello.sh): the age of
    # the last hello attempt and its HTTP code, empty = no response. No file
    # = both empty = "not known yet". One printf at the end: a failure prints
    # nothing, never half a pair. #1304: the cloner's standby reads it too.
    _hello_age=""; _hello_rc=""; _now=$(date +%s) || return 1
    if [ -f "$RUN_DIR/hello.state" ]; then
        IFS='|' read -r _hello_at _hello_rc < "$RUN_DIR/hello.state" || return 1
        case "$_hello_at" in ''|*[!0-9]*) printf 'native-gui: invalid hello timestamp\n' >&2; return 1 ;; esac
        case "$_hello_rc" in ''|[0-9][0-9][0-9]) ;; *) printf 'native-gui: invalid hello HTTP code\n' >&2; return 1 ;; esac
        [ "$_now" -ge "$_hello_at" ] || { printf 'native-gui: hello timestamp is in the future\n' >&2; return 1; }
        _hello_age=$((_now - _hello_at))
    fi
    printf 'hello_age=%s\nhello_rc=%s\n' "$_hello_age" "$_hello_rc"
    # #1483: a power-off that came back (safe_poweroff) -- on every screen, said
    [ -s "$RUN_DIR/power.fail" ] && printf 'toast=%s\n' "$(tr -d '\r\n|' < "$RUN_DIR/power.fail")"
    return 0
}

gui_shrink_state() {
    # #928: the agent's pending shrink question (shrinkgui.sh), copied into
    # the state file. The question is local -- it needs no server -- so the
    # failure snapshot below carries it too. One awk opens the file once:
    # a removal after the open cannot split the header from its rows, and a
    # removal before it (the question was just answered) is no question.
    [ -s "$GUI_DIR/shrink-request" ] || return 0
    awk 'NR == 1 { print "shrink_prompt=" $0; next }
         { print "shrink_partition=" $0 }' "$GUI_DIR/shrink-request" 2>/dev/null ||
        [ ! -e "$GUI_DIR/shrink-request" ]
}

gui_state_loop() {
    while :; do
        if ! gui_state; then
            printf 'native-gui: state could not be verified\n' >&2
            { printf 'message=לא ניתן לאמת את מצב התחנה|בדקו את החיבור לשרת. הפרטים ביומן הסוכן\n' &&
                gui_server_addr && gui_shrink_state && gui_ask_state; } > "$GUI_DIR/state.next" &&
                mv "$GUI_DIR/state.next" "$GUI_DIR/state" || return 1
        fi
        sleep 2
    done
}
