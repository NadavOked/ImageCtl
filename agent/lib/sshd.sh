# sshd.sh -- SSH for the technician, behind the debug gate (#44).
# POSIX sh (busybox ash). No bashisms.
#
# Twenty stations on one serial pipe does not work: the pipe was fine for
# the two machines of the first lab night and stops being a way in at
# classroom scale. So dropbear -- but under the *same* switch that already
# opens the technician shell, `imagectl.debug=1` on the kernel command
# line, and not a switch of its own. A classroom station listens on no
# port at all; nothing here runs unless the server put the parameter in
# the GRUB entry on purpose.
#
# Public key only. There are no passwords anywhere in this image and there
# will not be: the initramfs is fetched over plain HTTP by anything that
# can reach the deployment VLAN, so a password inside it is a published
# password. The key is packed at build time (`--ssh-key` in the builder).

SSH_PORT="${SSH_PORT:-22}"
SSH_HOME="${SSH_HOME:-/root}"
SSH_KEYS="${SSH_KEYS:-/etc/imagectl/authorized_keys}"
SSH_HOSTKEY="${SSH_HOSTKEY:-$RUN_DIR/dropbear_ed25519.key}"
#: שם הבינארי, ניתן לדריסה — כמו כל שאר ההגדרות כאן. הבדיקה של
#: "אין dropbear באימג'" חייבת להיות דטרמיניסטית: כשהיא נשענה על
#: היעדרו מ-PATH היא עברה על מארח הפיתוח ועל CI, ונפלה על כל מכונה
#: ש-dropbear מותקן בה — כלומר על שרת המעבדה עצמו.
SSH_DROPBEAR="${SSH_DROPBEAR:-dropbear}"
#: מאיפה נקראת הראיה שמישהו באמת מאזין. ניתן לדריסה בבדיקות — ובלי
#: הדריסה הזאת בדיקה שרצה על מכונה ש-sshd פעיל בה הייתה "מוצאת" את
#: פורט 22 שלה ומדווחת הצלחה. אותו לקח בדיוק של SSH_DROPBEAR למעלה.
SSH_PROC_NET="${SSH_PROC_NET:-/proc/net}"
SSH_VERIFY_TRIES="${SSH_VERIFY_TRIES:-3}"

_ssh_spawn() {
    # The launch, on its own, so the tests can read the command line
    # without leaving a daemon behind.
    setsid "$@" >> "$LOG_FILE" 2>&1 &
}

ssh_listen_state() {
    # Positive evidence, read back from the kernel: listening / closed /
    # unknown. Three states, not two.
    #
    # A spawn that returned is not a daemon that bound a port. dropbear
    # exits on a host key it cannot read, on a port already taken, on an
    # account NSS cannot find -- and every one of those used to end with
    # this file logging "dropbear on port 22". A technician then spent the
    # night asking why the station refuses his key, when nothing was ever
    # listening. Principle 5: absence of a failure sign is not success.
    #
    # The header line is what proves a socket table was read at all. An
    # unreadable /proc is "unknown", never "closed".
    _hex=$(printf '%04X' "$SSH_PORT")
    _read=0
    _found=0
    for _f in "$SSH_PROC_NET/tcp" "$SSH_PROC_NET/tcp6"; do
        [ -r "$_f" ] || continue
        IFS= read -r _head < "$_f" || continue
        case "$_head" in *local_address*) ;; *) continue ;; esac
        _read=1
        # st == 0A is TCP_LISTEN; field 2 is <address>:<port>, hex.
        while read -r _sl _local _rem _st _rest; do
            [ "$_st" = "0A" ] || continue
            case "$_local" in *:"$_hex") _found=1 ;; esac
        done < "$_f"
    done
    if [ "$_read" = 0 ]; then
        echo unknown
    elif [ "$_found" = 1 ]; then
        echo listening
    else
        echo closed
    fi
    unset _hex _read _found _f _head _sl _local _rem _st _rest
}

ssh_start() {
    # Never fatal: no way in is a worse lab day, not a broken machine.
    command -v "$SSH_DROPBEAR" >/dev/null 2>&1 || {
        log "ssh: no dropbear in this image -- serial console only"
        return 1
    }
    if [ ! -s "$SSH_KEYS" ]; then
        log "ssh: no authorized key was packed -- not listening"
        return 1
    fi

    mkdir -p "$SSH_HOME/.ssh"
    chmod 0700 "$SSH_HOME/.ssh"
    cp "$SSH_KEYS" "$SSH_HOME/.ssh/authorized_keys"
    chmod 0600 "$SSH_HOME/.ssh/authorized_keys"

    # The host key is made here, on every boot, into the tmpfs -- never
    # packed. One baked at build time would be the same private key on
    # every station in the college, shipped inside a file served over
    # plain HTTP, and it would make the builder's output stop being a
    # function of its inputs. The price is a fingerprint that changes on
    # every boot, which is why the documented connect line does not check
    # it (docs/agent.md).
    if [ ! -s "$SSH_HOSTKEY" ]; then
        mkdir -p "$(dirname "$SSH_HOSTKEY")"
        dropbearkey -t ed25519 -f "$SSH_HOSTKEY" >/dev/null 2>&1 || {
            log "ssh: host key generation failed -- not listening"
            return 1
        }
        chmod 0600 "$SSH_HOSTKEY"
    fi

    # -F stay in the foreground (we background it ourselves so the log
    # lands in the agent's file), -s no password logins, -g no password
    # logins for root either, -j -k no port forwarding in either
    # direction: this is a console, not a tunnel into the VLAN.
    _ssh_spawn "$SSH_DROPBEAR" -F -s -g -j -k -p "$SSH_PORT" -r "$SSH_HOSTKEY"

    # And now read it back. What gets logged is what the kernel says, not
    # what we asked for.
    _try=0
    while :; do
        _state=$(ssh_listen_state)
        if [ "$_state" = "listening" ]; then
            log "ssh: dropbear listening on port $SSH_PORT (root, public key only)"
            unset _try _state
            return 0
        fi
        [ "$_state" = "unknown" ] && break
        _try=$((_try + 1))
        [ "$_try" -ge "$SSH_VERIFY_TRIES" ] && break
        sleep 1
    done
    if [ "$_state" = "unknown" ]; then
        log "ssh: cannot read $SSH_PROC_NET -- reporting NOT listening"
    else
        log "ssh: dropbear did not bind port $SSH_PORT -- no ssh on this station"
    fi
    unset _try _state
    return 1
}
