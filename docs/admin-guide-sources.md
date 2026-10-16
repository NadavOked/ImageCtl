<div dir="rtl">

# מקורות למדריך מנהל ה-IT (`docs/admin-guide-he.md`, ‏#1474)

כל כלל בחומת האש ובטבלאות הרשת שבמדריך — ומאיפה בקוד הוא נלקח. שורות נכונות
לענף `integration/v0743-speed` (‏`cbad065c`, ‏16/10/2026). כשמשנים פורט או זרימה
בקוד — מעדכנים כאן ובמדריך, ומריצים מחדש:
`python tools/docs/md2pdf_he.py docs/admin-guide-he.md --pdf`.

## כללים שנפתחים (פרק 3.1)

| כלל | מה | מקור בקוד |
|---|---|---|
| F1 | קונסולה `8081/tcp` HTTPS, על כרטיס הניהול; השרת אינו בודק מקור | `server/main.py:153` (‏`--console-port` 8081), `server/main.py:167` (‏`--console-host`); `install/nftables-rules.sh:195` (פתוח על כרטיס השרתים, בלי `saddr`); `install/firewall-rules.md:3-7` ("אין בדיקת כתובת מקור בקוד"); `server/health.py:628-631` |
| F2 | ראשי ← משני `8443/tcp` mTLS | `server/main.py:213` (‏`--interserver-port` 8443); `install/nftables-rules.sh:180-183` (במשני: רק `ip saddr` של הראשי); `server/storage_transfer.py:3-7` (דחיפה על הערוץ המאומת, ‏TLS 1.3); `server/storage_client.py:486` (‏TLSv1_3 מינימום); `server/interserver_api.py:1396` (המשני מתחבר ל-5900 של מכונה בשביל הראשי) |
| F3 | משני ← ראשי `8443/tcp` "אתה שם?" | `server/primary_alive.py:30` (‏`/api/interserver/v1/alive`); `install/nftables-rules.sh:184-188`; `docs/server-install.md:239-248` (DNAT באתר הראשי, 8081 לא בין אתרים) |
| F4 | ‏NTP ‏`123/udp` | `installer/lib/finish.sh:86-90` (‏`systemd-timesyncd` מופעל, בלי שרת מוגדר → ברירת המחדל של Debian); `server/health.py:190-197` (בדיקת "סנכרון שעון"); `install/firewall-rules.md` כלל L4 |
| F5 | ‏DNS ‏`53` | `installer/lib/finish.sh:101` (‏`servers_dns` באשף); `server/update.py:55-58` ("חומת אש או DNS") |
| F6 | ‏`github.com:443` לעדכון | `server/update.py:54` (‏`PUBLIC_UPDATE_URL = https://github.com/NadavOked/ImageCtl.git`), `server/update.py:191` (‏`git ls-remote --tags`); `tools/server-upgrade.sh:152` (‏`git fetch --tags`), `tools/server-upgrade.sh:162-164` (אימות חתימה מול `install/release-signers`); `install/firewall-rules.md` כלל L5 |
| F7 | ‏VM ← אינטרנט `80/443` | היום: `server/vmnet.py:12-16,87-101` (יציאה דרך כרטיס הניהול בלבד, לכתובות לא-פרטיות; DNS לשרתי השמות בלבד). ‏v1: הכרעת נדב 16/10, ענף `feat/vm-own-mac` — **לא בריפו עדיין** |
| F8 | ‏VM ← שיתוף רשת `445/tcp` | ‏v1 בלבד (בריף נדב 16/10). היום חסום: `server/vmnet.py:44-46` (‏`DENIED` כולל 10/8, ‏172.16/12, ‏192.168/16). הפורט 445 — הנחה של המדריך (SMB), לא מהקוד |

## כללים שנסגרים (פרק 3.2)

| כלל | מקור |
|---|---|
| D1 כיתות ← שרת | `install/firewall-rules.md` כלל L6; `install/nftables-rules.sh:7,10-11` ("הכיתות (v2) אינן נפתחות כאן כלל"); `server/health.py:632-634` (‏4011 רק כשיש כיתות) |
| D2 ‏SSH | `install/nftables-rules.sh:10` ("אין כלל TCP 22 בשום מצב (#1266)"); `install/firewall-rules.md` כלל L8 |
| D3 ההפצה אינה מנותבת | `install/firewall-rules.md` §3 (L1 ופסקת ה-DHCP); `README.md:279` |
| D4 מולטיקאסט/WoL לא לכיתות | `install/firewall-rules.md` כלל L9; `README.md:261-266` |
| D5 קונסולה רק מ-IT | ראו F1 |
| D6 ‏VM לא מגיע לרשתות פנימיות | היום: `server/vmnet.py:17-19,44-46,97-100`. ‏v1: בריף נדב 16/10 |

## בתוך וילן ההפצה (פרק 3.3)

| מה | מקור |
|---|---|
| DHCP ‏`67/68` | `install/nftables-rules.sh:162-163`; `server/health.py:592-612`; כבוי בהתקנה: `docs/server-install.md:313-320` |
| TFTP ‏`69` + conntrack helper | `install/nftables-rules.sh:164-165`; `install/firewall-rules.md` §3 (אזהרת `nf_conntrack_tftp`); `server/health.py:574` |
| HTTP ‏`8080` לתחנות, לא מוצפן | `server/main.py:149`; `install/nftables-rules.sh:166-167`; `README.md:290-296` (סיסמת משתמש ההפצה גלויה) |
| מולטיקאסט `9000-9001/udp`, ‏TTL 1 | `server/sender.py:37,41` (‏`PRODUCTION_PORTBASE = 9000`, ‏portbase ו-portbase+1); `server/sender.py:659-685` (‏`udp-sender` בלי `--ttl`); `install/nftables-rules.sh:35,168-169`; `README.md:248-249`; מחשב הבנייה כמקור: `agent/lib/directsend.sh:36` |
| ‏WoL ‏`9/udp` ‏`255.255.255.255` | `server/wol.py:28-29`, `server/wol.py:140-150` (על הממשק בלבד); `install/nftables-rules.sh:170` |
| מוניטור `5900/tcp` שרת ← תחנה, כבוי כברירת מחדל | `server/monitor.py:21` (‏`MONITOR_PORT`), `server/monitor.py:656` (השרת פותח את החיבור); `install/nftables-rules.sh:135-136` (הדפדפן דרך פורט הקונסולה); `server/health.py:653-666` |
| ‏`4011` לא בגרסה 1 | `server/health.py:632-645` |
| ‏`8082` הוסר | `server/app.py:660`, `server/kiosk.py:3` (‏#1247) |
| חומת האש על השרת: input drop, ‏output accept, ‏forward drop | `install/nftables-rules.sh:209-237` |

## מתגים, DHCP relay ורשת (פרקים 2, 4, 5)

| טענה | מקור |
|---|---|
| הגדרות מתג ההפצה (וילן, port security, ‏IGMP, ‏DHCP snooping, בלי 802.1X, תיוג) | `README.md:268-284` |
| ‏IGMP: פקיעה אחרי 125–260 ש'; ‏1G ≈ 110MB/s; נמדד 106MB/s; ‏Dynamic Memory | `docs/imagectl-spec.md:109-115` |
| ‏10GbE לשרת | `docs/imagectl-spec.md:352` |
| בלי `ip helper-address` לשרת | `install/firewall-rules.md` §3 (פסקת DHCP) |
| אישור נוסף ל-DHCP על כרטיס trunk | `server/console_dhcp.py:415-418` |
| "מי עוד עונה" / מכונה מרשת זרה — אזהרה, לא חסימה | `README.md:286-288`; `server/foreign_vlan.py:1-11` |
| השרת אינו מתייג וילנים | אין יצירת ממשק VLAN ב-`server/netcfg.py`, ‏`server/console_netcfg.py`, ‏`server/deploy_net.py` (חיפוש `vlan` — 0 תוצאות) |
| הניהול יכול להיות רשת הכיתות | הכרעת נדב 16/10 (בריף) |
| ‏PortFast/edge | **לא מהקוד** — המלצה כללית, מסומנת במדריך |

## ‏VM לעדכון Windows (פרק 6)

| טענה | מקור |
|---|---|
| ‏MAC משלו, כרטיס לבחירה, DHCP reservation, ‏ESXi/Hyper-V | בריף נדב 16/10, ענף `feat/vm-own-mac` — **לא בריפו** |
| היום: NAT דרך כרטיס הניהול, בלי כתובות פרטיות | `server/vmnet.py:1-30`; `docs/vm-update.md` §3 |
| ‏`/dev/kvm` חובה, סירוב `no_kvm` | `server/vmupdate.py:104-110,662` |
| השרת על ברזל (13/10) | `docs/vm-update.md:9-10` |
| דומיינים של Windows Update, ‏KMS ‏1688 | **לא מהקוד** — ידע כללי על Microsoft; מסומן "להשלים מול הרשימה הרשמית" |
| וירטואליזציה מקוננת, Promiscuous | **לא מהקוד** — נגזר מדרישת `/dev/kvm`; Promiscuous הוא שאלה פתוחה |

## העברות בין אתרים ועדכונים (פרקים 7, 8)

| טענה | מקור |
|---|---|
| הראשי יוזם, דחיפה ומשיכה | `server/storage_transfer.py:3-7,18-19`; ‏#1070 |
| ניתוק = כישלון גלוי ומתחילים מחדש | `server/storage_transfer.py:12-16` |
| מקום פנוי: היום ‏2× + 256MiB | `server/interserver_api.py:304-306,514` |
| אחרי #1457: ‏1× + 256MiB | `origin/fix/1457-intersite-push` (‏`4e9d3c9f`): ‏`FREE_SPACE_FACTOR = 1` |
| ללא תקרה כברירת מחדל; תקרה בקונסולה | בריף נדב 16/10, ענף `feat/wave-rate-by-machine` — **לא בריפו** |
| עדכון רק בתג חתום, חזרה אוטומטית | `tools/server-upgrade.sh:162-170` (‏`verify-release-tag.py`), ‏`rollback` לאורך הסקריפט |
| בלי מקור apt חי | הכרעת נדב 15/10 ב-#1411 (תגובה ב-Issue) |
| ‏`deb.debian.org` ב-vmupdate-host | `install/vmupdate-host.sh:17-24,42,57-71` (‏#1371) |
| אין SSH בייצור | `install/firewall-rules.md` §1 (‏#1148, ‏#1266) |

</div>
