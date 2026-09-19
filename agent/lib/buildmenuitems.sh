# buildmenuitems.sh -- what the build machine's menu offers: the items, their
# labels, and the gates (role, classrooms, tools). POSIX sh (busybox ash).
# Split out of buildmenu.sh (#1073) the way roomdraw.sh was split from
# roomflow.sh (#418): buildmenu.sh runs the menu, this file says what is on
# it. Both the text menu (build_menu) and the GUI (guistate.sh, menu_class=)
# read the same gates here, so v1 and v2 differ in ONE place.

#: Which roles may use this menu at all. An allow-list, like
#: station.py:ROUND_OPENER_ROLES -- "we could not tell what this account may
#: do" is a refusal, not permission (rule 5).
BUILD_MENU_ROLES="admin deploy"

build_menu_options() {
    # The menu is built from the role. Hiding is not permission -- the
    # server enforces admin_only on the capture either way -- but a menu
    # that offers what it would refuse is a menu that lies to the operator.
    # #1073 (Nadav, 18/09): one order on the text menu and on the GUI --
    # deploy from the server, deploy this disk directly, restore onto this
    # disk, and for admin the capture last. The class card (v2) after all.
    rm -f "$RUN_DIR/build_menu.txt"
    echo "room" >> "$RUN_DIR/build_menu.txt"
    echo "direct" >> "$RUN_DIR/build_menu.txt"   # #715: always, like room
    echo "self" >> "$RUN_DIR/build_menu.txt"     # #1073 (c): this server's image onto this disk
    [ "$BUILD_ROLE" = "admin" ] && echo "capture" >> "$RUN_DIR/build_menu.txt"
    # #880: v1 is the cloning edition -- the class option is offered only
    # when the last hello said the server has it switched on. The server
    # refuses the round either way (409); a missing field reads as off.
    if class_deploy_on; then echo "class" >> "$RUN_DIR/build_menu.txt"; fi
}

class_deploy_on() {
    # The hello answer is the one place the switch is read from, on both
    # the text menu and the GUI state (guistate.sh). $RESP is the agent's
    # name for it; the kiosk has only $RUN_DIR.
    # #1081: classrooms is a server edition flag (hello /state). Missing =
    # off (principle 1). v2 turns it on. The operator switch sits on top.
    _hello="${RESP:-$RUN_DIR/response.json}"
    _flag=$(json_get "$_hello" ".classrooms")
    if [ "$_flag" != true ] && [ -n "${GUI_DIR:-}" ] && [ -f "$GUI_DIR/station.json" ]; then
        _flag=$(json_get "$GUI_DIR/station.json" ".classrooms")
    fi
    [ "$_flag" = true ] || return 1
    [ "$(json_get "$_hello" ".class_deploy_enabled")" = true ]
}

tools_on() {
    # v1 ships without the toolbox (Nadav, 19/09; v1.1 turns it on). Same
    # source and same fallback as class_deploy_on: `.tools` from the hello,
    # else from /state. Missing = off (principle 1). guistate.sh writes
    # menu_tools= from here, and the GUI hides its tools button on 0.
    _hello="${RESP:-$RUN_DIR/response.json}"
    _flag=$(json_get "$_hello" ".tools")
    if [ "$_flag" != true ] && [ -n "${GUI_DIR:-}" ] && [ -f "$GUI_DIR/station.json" ]; then
        _flag=$(json_get "$GUI_DIR/station.json" ".tools")
    fi
    [ "$_flag" = true ]
}

build_menu_label() {
    case "$1" in
        capture) echo "Upload an image to the server (capture this disk)" ;;
        room)    echo "Deploy to the cloning machines" ;;
        direct)  echo "Deploy THIS disk directly to the cloning machines" ;;
        self)    echo "Restore an image from the server onto THIS disk" ;;
        class)   echo "Deploy to a classroom" ;;
    esac
}

