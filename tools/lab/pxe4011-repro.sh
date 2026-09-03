#!/bin/sh
# שחזור מבודד ל-Issue #36 — קפיאת dnsmasq על בקשת PXE לפורט 4011
# במצב proxy. רץ על מכונה **אחת** (ImageCtl-Firewall), בלי לגעת באף
# מכונה אחרת במעבדה ובלי לייצר תעבורה על אף רשת אמיתית.
#
# הבידוד: שני network namespaces מחוברים בזוג veth. אינסטנס ה-dnsmasq
# הנבדק חי כולו בתוך `pxesrv`, הלקוח בתוך `pxecli`. ה-dnsmasq של
# המכונה עצמה (‏DHCP של וילן הכיתה, ‏10.97.0.0/24) לא נוגע בזה כלל —
# ה-namespace הוא מחסום קשיח, לא כלל סינון שאפשר לפספס.
#
#   ./pxe4011-repro.sh run [--dnsmasq /path/to/dnsmasq] [--variant probe|pxe43]
#   ./pxe4011-repro.sh clean
#
# התצורה שנכתבת כאן היא בדיוק מה ש-`render_proxy` ב-`server/dhcp.py`
# מייצר — אותן שורות, אותו סדר. שחזור על תצורה אחרת אינו שחזור.
#
# ההכרעה: המבחן אינו "האם dnsmasq קרס" אלא **האם הוא עדיין עונה**.
# תהליך חי שאינו עונה הוא בדיוק התסמין של #36, וזו הסיבה שהבדיקה
# מודדת שלושה דברים אחרי הבקשה — זמן מעבד, מצב התהליך, ותשובה
# לבקשה חדשה על :67 שענתה **לפני כן**. בלי הבסיס הזה "אין תשובה"
# אינו מוכיח דבר (אותו לקח של http=000 ב-imagectl-class-verify).
set -u

NS_SRV=pxesrv
NS_CLI=pxecli
VETH_SRV=vpxs
VETH_CLI=enpxe0          # ‏en* בכוונה: כך imagectl-l4-probe מוצא MAC אמיתי
IP_SRV=10.99.0.1
IP_CLI=10.99.0.2
WORK=/tmp/pxebug
PROBE=${PROBE:-$WORK/imagectl-l4-probe}
DNSMASQ=/usr/sbin/dnsmasq
VARIANT=probe
WATCHDOG=120             # שום דבר לא שורד את זה, גם תהליך שלא מעבד אותות

say() { printf '%s\n' "$*"; }
hr()  { printf '%s\n' "------------------------------------------------------------"; }

clean() {
    [ -f "$WORK/dnsmasq.pid" ] && kill -9 "$(cat "$WORK/dnsmasq.pid")" 2>/dev/null
    [ -f "$WORK/watchdog.pid" ] && kill -9 "$(cat "$WORK/watchdog.pid")" 2>/dev/null
    # כל שריד של האינסטנס הנבדק — לפי קובץ התצורה, כדי לא לגעת
    # ב-dnsmasq של המכונה עצמה בשום מקרה.
    pkill -9 -f "conf-file=$WORK/proxy.conf" 2>/dev/null
    ip netns del $NS_SRV 2>/dev/null
    ip netns del $NS_CLI 2>/dev/null
    rm -f "$WORK/dnsmasq.pid" "$WORK/watchdog.pid"
}

setup_net() {
    ip netns add $NS_SRV || return 1
    ip netns add $NS_CLI || return 1
    ip link add $VETH_SRV type veth peer name $VETH_CLI || return 1
    ip link set $VETH_SRV netns $NS_SRV
    ip link set $VETH_CLI netns $NS_CLI
    ip netns exec $NS_SRV ip addr add $IP_SRV/24 dev $VETH_SRV
    ip netns exec $NS_SRV ip link set $VETH_SRV up
    ip netns exec $NS_SRV ip link set lo up
    ip netns exec $NS_CLI ip addr add $IP_CLI/24 dev $VETH_CLI
    ip netns exec $NS_CLI ip link set $VETH_CLI up
    ip netns exec $NS_CLI ip link set lo up
}

write_conf() {
    mkdir -p "$WORK/tftp/grub/i386-pc"
    : > "$WORK/tftp/bootx64.efi"
    : > "$WORK/tftp/grub/i386-pc/core.0"
    : > "$WORK/proxy.leases"
    cat > "$WORK/proxy.conf" <<EOF
# ImageCtl -- PXE proxy instance, generated from the console (spec 24).
# Do not edit by hand: the next change in the console rewrites it.

port=0
bind-interfaces
dhcp-leasefile=$WORK/proxy.leases

enable-tftp
tftp-root=$WORK/tftp

dhcp-match=set:bios,option:client-arch,0
dhcp-match=set:efi-x86_64,option:client-arch,7
dhcp-match=set:efi-x86_64,option:client-arch,9

# --- $VETH_SRV ---
interface=$VETH_SRV
dhcp-range=set:if-$VETH_SRV,$IP_SRV,proxy
pxe-service=tag:if-$VETH_SRV,tag:bios,x86PC,"ImageCtl",grub/i386-pc/core.0
pxe-service=tag:if-$VETH_SRV,tag:efi-x86_64,x86-64_EFI,"ImageCtl",bootx64.efi
EOF
}

start_dnsmasq() {
    # ‏nice 19: אם הוא באמת נכנס ללולאה על 100% מעבד, המעטפת חייבת
    # להישאר מגיבה כדי שנוכל בכלל למדוד ולנקות (למכונה יש vCPU אחד).
    ip netns exec $NS_SRV nice -n 19 "$DNSMASQ" \
        --conf-file="$WORK/proxy.conf" \
        --pid-file="$WORK/dnsmasq.pid" \
        --log-facility="$WORK/dnsmasq.log" --log-dhcp \
        || return 1
    sleep 1
    [ -s "$WORK/dnsmasq.pid" ] || return 1
    ( sleep $WATCHDOG; kill -9 "$(cat "$WORK/dnsmasq.pid" 2>/dev/null)" 2>/dev/null ) &
    echo $! > "$WORK/watchdog.pid"
}

cputicks() {   # utime+stime של התהליך, ב-ticks
    awk '{print $14+$15}' /proc/"$1"/stat 2>/dev/null || echo -1
}
pstate() { awk '{print $3}' /proc/"$1"/stat 2>/dev/null || echo "-"; }

probe() {      # $1=port  -> "answer|refused|no-answer  detail"
    ip netns exec $NS_CLI "$PROBE" udp $IP_SRV "$1" pxe 2>&1
}

run() {
    trap 'clean' EXIT INT TERM
    clean
    mkdir -p "$WORK"
    [ -x "$PROBE" ] || { say "חסר $PROBE (imagectl-l4-probe)"; exit 2; }
    setup_net || { say "הקמת ה-namespaces נכשלה"; exit 2; }
    write_conf
    say "dnsmasq:  $("$DNSMASQ" --version | head -1)"
    start_dnsmasq || { say "dnsmasq לא עלה:"; cat "$WORK/dnsmasq.log" 2>/dev/null; exit 2; }
    PID=$(cat "$WORK/dnsmasq.pid")
    say "pid=$PID  variant=$VARIANT"
    hr

    # ‏1. בסיס — האם הוא עונה בכלל לפני שנגענו ב-4011.
    B67=$(probe 67)
    say "בסיס   :67   -> $B67"
    case "$B67" in answer*) ;; *)
        say "אין בסיס: dnsmasq לא ענה על :67 עוד לפני הטריגר. הבדיקה חסרת ערך."
        say "--- log ---"; cat "$WORK/dnsmasq.log"; exit 3 ;;
    esac
    C0=$(cputicks "$PID")

    # ‏2. הטריגר — אותה חבילת PXE, אל :4011.
    T67=$(probe 4011)
    say "טריגר  :4011 -> $T67"

    # ‏3. שלוש ראיות, לא אחת.
    sleep 3
    C1=$(cputicks "$PID"); ST=$(pstate "$PID")
    say "מעבד   : +$((C1 - C0)) ticks ב-3 שניות (100%% מעבד ~= 300)  state=$ST"
    A67=$(probe 67)
    say "אחרי   :67   -> $A67"
    say "סוקטים :"
    ip netns exec $NS_SRV ss -lnup | sed 's/^/         /'

    # ‏4. האם SIGTERM מעובד בכלל.
    kill -TERM "$PID" 2>/dev/null; sleep 3
    if kill -0 "$PID" 2>/dev/null; then TERM_OK=no; else TERM_OK=yes; fi
    say "SIGTERM: מעובד=$TERM_OK"
    hr
    say "--- journal (dnsmasq.log) ---"; cat "$WORK/dnsmasq.log" 2>/dev/null

    hr
    case "$A67$TERM_OK" in
        answer*yes) say "פסק דין: לא שוחזר — dnsmasq המשיך לענות ומת יפה." ; RC=1 ;;
        *)          say "פסק דין: שוחזר — dnsmasq הפסיק לענות אחרי בקשת :4011." ; RC=0 ;;
    esac
    exit $RC
}

case "${1:-run}" in
    clean) clean; say "נוקה." ;;
    run)   shift
           while [ $# -gt 0 ]; do
               case $1 in
                   --dnsmasq) DNSMASQ=$2; shift 2 ;;
                   --variant) VARIANT=$2; shift 2 ;;
                   *) say "ארגומנט לא מוכר: $1"; exit 2 ;;
               esac
           done
           run ;;
    *) say "שימוש: $0 run [--dnsmasq PATH] | $0 clean"; exit 2 ;;
esac
