#!/bin/sh
# ImageCtl — מחולל /etc/nftables.conf לפי כרטיסי וילן (R20-F1 / #1074).
#
# POSIX sh. מדפיס קובץ שלם ל-stdout. המתקין בודק תחביר (nft -c -f)
# ואז מניח ב-/etc/nftables.conf ומפעיל את השירות.
#
# מודל האתר: שלושה וילנים. הכיתות (v2) אינן נפתחות כאן כלל.
#   הפצה  — DHCP/TFTP/סוכן/קיוסק/מולטיקאסט. WoL יוצא ב-output (לא נחסם).
#   שרתים — קונסולה HTTPS ו-SSH ניהול. 8443 רק במשני, ורק מכתובת הראשי.
#   כיתות — כלום.
#
# שימוש:
#   nftables-rules.sh --deploy-if <if> --servers-if <if> \
#     [--primary-ip <ip>] [--mcast-ports a-b] \
#     [--console-port 8081] [--agent-port 8080] \
#     [--kiosk-port 8082] [--interserver-port 8443] [--ssh-if IF]

set -eu

DEPLOY_IF=""
SERVERS_IF=""
PRIMARY_IP=""
MCAST_PORTS="9000-9001"
CONSOLE_PORT="8081"
AGENT_PORT="8080"
KIOSK_PORT="8082"
INTERSERVER_PORT="8443"
SSH_IFS=""

die() { printf '%s\n' "nftables-rules: $*" >&2; exit 2; }

usage() {
    cat <<'EOF'
Usage: nftables-rules.sh --servers-if IF [--deploy-if IF] [options]

  --deploy-if IF          כרטיס וילן ההפצה; בלעדיו (#1088: טרם הוגדר מהקונסולה)
                          נכתבים כללי וילן השרתים בלבד, וההפצה סגורה
  --servers-if IF         כרטיס וילן השרתים (חובה)
  --primary-ip IP         כתובת הראשי — רק במשני; פותח 8443 ממנה בלבד
  --mcast-ports A-B       טווח UDP של udpcast (ברירת מחדל 9000-9001)
  --console-port N        קונסולה (ברירת מחדל 8081)
  --agent-port N          סוכן (ברירת מחדל 8080)
  --kiosk-port N          קיוסק (ברירת מחדל 8082)
  --interserver-port N    בין-שרתים (ברירת מחדל 8443)
  --ssh-if IF             פתח TCP 22 על ממשק שמתג ssh:iface:<IF> שלו דלוק
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --deploy-if)        DEPLOY_IF="${2:?}"; shift 2 ;;
        --servers-if)       SERVERS_IF="${2:?}"; shift 2 ;;
        --primary-ip)       PRIMARY_IP="${2:?}"; shift 2 ;;
        --mcast-ports)      MCAST_PORTS="${2:?}"; shift 2 ;;
        --console-port)     CONSOLE_PORT="${2:?}"; shift 2 ;;
        --agent-port)       AGENT_PORT="${2:?}"; shift 2 ;;
        --kiosk-port)       KIOSK_PORT="${2:?}"; shift 2 ;;
        --interserver-port) INTERSERVER_PORT="${2:?}"; shift 2 ;;
        --ssh-if)
            SSH_IF="${2:?}"
            case "$SSH_IF" in
                *[!A-Za-z0-9_.:-]*|*"'"*|*"\""*) die "SSH interface name is not safe for nftables" ;;
            esac
            SSH_IFS="$SSH_IFS $SSH_IF"
            shift 2
            ;;
        -h|--help)          usage; exit 0 ;;
        *)                  die "unknown option: $1" ;;
    esac
done

[ -n "$SERVERS_IF" ] || die "--servers-if is required"

# שמות כרטיסים נכנסים ל-nft כמחרוזת מצוטטת. תו שיכול לשבור את הציטוט
# או את הדקדוק — סירוב, לא "ניקוי" בשקט.
case "$DEPLOY_IF$SERVERS_IF" in
    *[!A-Za-z0-9_.:-]*|*"'"*|*"\""*) die "interface name is not safe for nftables" ;;
esac
case "$MCAST_PORTS" in
    [0-9]*-[0-9]*) ;;
    *) die "--mcast-ports must be A-B (got: $MCAST_PORTS)" ;;
esac

for p in "$CONSOLE_PORT" "$AGENT_PORT" "$KIOSK_PORT" "$INTERSERVER_PORT"; do
    case "$p" in
        *[!0-9]*|"") die "port must be a number (got: $p)" ;;
    esac
done

if [ -n "$PRIMARY_IP" ]; then
    case "$PRIMARY_IP" in
        [0-9]*.[0-9]*.[0-9]*.[0-9]*) ;;
        *) die "--primary-ip must be an IPv4 address (got: $PRIMARY_IP)" ;;
    esac
fi

SSH_RULES="		# SSH לשרת כבוי — אין כלל TCP 22 על אף ממשק."
if [ -n "$SSH_IFS" ]; then
    SSH_RULES="		# SSH לשרת — רק ממשקים שהמתג שלהם דלוק."
    for SSH_IF in $SSH_IFS; do
        SSH_RULES="${SSH_RULES}
		iifname \"${SSH_IF}\" tcp dport 22 accept"
    done
fi

# ‏#1088: כללי וילן ההפצה רק כשיש כרטיס הפצה. בלעדיו — הערה במקומם, וההפצה
# סגורה עד שהקונסולה תריץ את המחולל שוב עם --deploy-if (server/deploy_net.py).
if [ -n "$DEPLOY_IF" ]; then
    DEPLOY_RULES="		# --- וילן ההפצה (${DEPLOY_IF}) ---
		# DHCP: מקור ראשוני עשוי להיות 0.0.0.0 — לא דורשים כתובת קיימת.
		iifname \"${DEPLOY_IF}\" udp dport 67 accept
		# TFTP: קריאת shim/GRUB. העברת הנתונים היא related אם nf_conntrack_tftp טעון.
		iifname \"${DEPLOY_IF}\" udp dport 69 accept
		# סוכן (hello/boot/images) וקיוסק — HTTP על כרטיס ההפצה בלבד.
		iifname \"${DEPLOY_IF}\" tcp dport ${AGENT_PORT} accept
		iifname \"${DEPLOY_IF}\" tcp dport ${KIOSK_PORT} accept
		# מולטיקאסט udpcast: portbase ו-portbase+1 (ברירת מחדל 9000-9001, server/sender.py).
		iifname \"${DEPLOY_IF}\" udp dport ${MCAST_PORTS} accept
		# WoL (UDP 9) הוא יוצא — chain output ב-accept, אין כלל כניסה."
else
    DEPLOY_RULES="		# --- וילן ההפצה: טרם הוגדר (#1088) ---
		# אין כרטיס הפצה — DHCP/TFTP/סוכן/קיוסק/מולטיקאסט סגורים. הקונסולה
		# כותבת את הקובץ הזה מחדש עם הכרטיס כשמודלק עליו DHCP."
fi

# כלל 8443 רק כשיש כתובת ראשי — אחרת הראשי יוזם החוצה, ואין מה לפתוח.
if [ -n "$PRIMARY_IP" ]; then
    INTERSERVER_RULE="		# משני בלבד: 8443 נכנס רק מוילן השרתים ורק מכתובת הראשי.
		# הראשי יוזם; תשובות הן established. mTLS עדיין חובה מעל זה.
		iifname \"${SERVERS_IF}\" ip saddr ${PRIMARY_IP} tcp dport ${INTERSERVER_PORT} accept"
else
    INTERSERVER_RULE="		# ראשי: 8443 אינו נפתח. הראשי יוזם חיבור למשני, לא להפך."
fi

cat <<EOF
#!/usr/sbin/nft -f
# ImageCtl — נוצר על ידי install/nftables-rules.sh. אל תערוך ביד.
# מדיניות לפי וילן (R20 §4 / #1074): input drop, output accept, forward drop.
# השרת אינו נתב — forward סגור כדי שכרטיס הפצה לא יהפוך לגשר לכיתות.

flush ruleset

table inet imagectl {
	chain input {
		type filter hook input priority filter; policy drop;

		# loopback — pairing מקומי, קונסולה על 127.0.0.1, health עצמי.
		iifname "lo" accept

		# תשובות לחיבורים שאנחנו יזמנו, וחיבורים related (TFTP helper אם נטען).
		ct state established,related accept
		ct state invalid drop

		# ICMP/ICMPv6: echo לאבחון, ND ל-IPv6, ו-PMTU כדי ש-TCP לא ייחסם.
		icmp type { echo-request, echo-reply, destination-unreachable, time-exceeded } accept
		icmpv6 type { echo-request, echo-reply, destination-unreachable, packet-too-big, time-exceeded, nd-router-solicit, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept

${DEPLOY_RULES}

		# --- וילן השרתים (${SERVERS_IF}) ---
		# קונסולה HTTPS — לא על כרטיס ההפצה.
		iifname "${SERVERS_IF}" tcp dport ${CONSOLE_PORT} accept
${SSH_RULES}
${INTERSERVER_RULE}

		# כל השאר: יומן מצומצם ואז drop (ה-policy). כיתות = v2, לא נפתח כאן.
		# ‏level info: לא warning — אחרת השורה מודפסת על הקונסולה מעל ה-DCUI (#1195)
		log prefix "imagectl-drop " level info limit rate 5/minute
	}

	chain output {
		type filter hook output priority filter; policy accept;
	}

	chain forward {
		type filter hook forward priority filter; policy drop;
	}
}
EOF
