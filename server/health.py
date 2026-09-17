"""מסך בריאות המערכת — רמזור לכל בדיקה, בקונסולה ולא בטרמינל.

העיקרון (נדב): בטרמינל נוגעים פעם אחת, בהתקנה. כדי לדעת אם פורט 67
תפוס ועל ידי מי לא מריצים `ss -ulnp` ביד — פותחים את הקונסולה.

כל בדיקה מחזירה: ok (ירוק) / warn (צהוב) / bad (אדום) / off (אפור —
לא רלוונטי או שאי אפשר לבדוק כאן). הרצת הפקודות מוזרקת, כך שהבדיקות
של הקוד עצמו רצות בלי systemd ובלי רשת.
"""

from __future__ import annotations

import socket
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends

import hashlib

from . import (agent_loops, auth, console_ssh, dhcp, foreign_vlan, hello,
               identity, monitor, ssh_switch)

BOOT_FILES = ("bootx64.efi", "grubx64.efi", "grub/grub.cfg")

#: ‏#976: הסימון של הבדיקה העצמית על בקשות `/boot/*` — הצד השולח של
#: `hello.PROBE_HEADER`, מאותו מקור אמת (שמות כותרות אינם תלויי-רישיות).
PROBE_HEADER = hello.PROBE_HEADER.decode("ascii")

#: מה שהתחנה מושכת ב-HTTP אחרי התפריט, מתוך תיקיית האתחול (#333).
#: ה-initramfs הגרפי (#32) אינו כאן: היעדרו נופל לטקסטואלי, ואינו חוסם.
BOOT_ASSETS = ("vmlinuz", "initrd.img")

#: רצפה גסה. קרנל ו-initramfs אמיתיים גדולים בהרבה, וגוף ה-404 של ‎/boot
#: הוא **תשעה** בייטים — ולכן "יש תשובה ויש גודל" אינו "יש קובץ" (#332).
MIN_ASSET_BYTES = 1 << 20


def _sha256(path) -> str:
    """‏"" כשאי-אפשר לקרוא — נבדל מ-hash שנקרא, ואינו "תואם"."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def default_hooks() -> dict:
    return {
        "ss": lambda: _run(["ss", "-ulnp"]),
        "shim_src": lambda: "/usr/lib/shim/shimx64.efi.signed",
        "unit_active": lambda name: _run(["systemctl", "is-active", name]).strip(),
        "http_get": _http_probe,
        "http_size": _http_size,
        "http_text": _http_body,
        "interfaces": dhcp.list_interfaces,
        "tftp_root": lambda: Path("/srv/tftp"),
        "udp_sender_pids": live_udp_sender_pids,
    }


def _probe_request(url: str) -> urllib.request.Request:
    """הבקשה של הבדיקה העצמית — מסומנת (‏#976).

    בלי הסימון השרת רשם את הבדיקה של עצמו כמכונה: ‏`net_seen` עם
    `127.0.0.1`, פירור `menu` שאיפס שביל אמיתי, והתקן "לא רשום"
    `00:00:00:00:00:00`. הסימון מכובד רק כשהפונה הוא השרת עצמו
    (‏`hello.self_probe`), ולכן הוא אינו דרך לאתחל בלי להירשם.
    """
    return urllib.request.Request(url, headers={PROBE_HEADER: "1"})


def _http_probe(url: str) -> int | None:
    try:
        with urllib.request.urlopen(_probe_request(url), timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def _http_body(url: str) -> tuple[int | None, str]:
    """קוד *וגם* גוף. חיווי ה-SSH של התחנות נשען על מה שבאמת נכתב
    בתפריט שהשרת מגיש, ולכן קוד תשובה לבדו אינו מספיק לו."""
    try:
        with urllib.request.urlopen(_probe_request(url), timeout=3) as response:
            return response.status, response.read(65536).decode("ascii", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:
        return None, ""


def _http_size(url: str) -> tuple[int | None, int | None]:
    """קוד תשובה **וגודל מוצהר**, בלי לקרוא את הגוף — initrd הוא עשרות
    מגהבייט, ומסך בריאות אינו מושך אותם. גודל שלא הוצהר חוזר `None`,
    שאינו 0 ואינו "בסדר"."""
    try:
        with urllib.request.urlopen(_probe_request(url), timeout=5) as response:
            length = response.headers.get("Content-Length")
            return response.status, int(length) if str(length).isdigit() else None
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return None, None


def boot_asset_problems(probe, server_base: str) -> list[str]:
    """‏vmlinuz ו-initrd.img — כפי שהתחנה מושכת אותם, לא כפי שהם בדיסק.

    בהתקנה נקייה (#332) שניהם החזירו 404 בזמן שהשורה הייתה ירוקה.
    הראיה כאן חיובית ומורכבת משניים — **200 וגם** גודל שאינו יכול
    להיות גוף שגיאה — ומי מהם נכשל נאמר בשמו.
    """
    problems = []
    for name in BOOT_ASSETS:
        status, size = probe(server_base.rstrip("/") + "/boot/" + name)
        if status is None:
            problems.append(f"{name}: הבדיקה עצמה לא רצה (השרת לא ענה)")
        elif status != 200:
            problems.append(f"{name}: {status} מ-/boot")
        elif size is None:
            problems.append(f"{name}: 200 בלי גודל מוצהר — הבדיקה לא רצה")
        elif size < MIN_ASSET_BYTES:
            problems.append(f"{name}: {size} בייטים בלבד — אינו קובץ אתחול")
    return problems


def port_owner(ss_output: str, port: int) -> str | None:
    """מי מאזין על פורט UDP — שם התהליך מתוך ss -ulnp, או None."""
    for line in ss_output.splitlines():
        if f":{port} " not in line and not line.rstrip().endswith(f":{port}"):
            continue
        if '"' in line:
            return line.split('"')[1]
        return "?"
    return None


def check(check_id: str, label: str, state: str, detail: str) -> dict:
    return {"id": check_id, "label": label, "state": state, "detail": detail}


def live_udp_sender_pids() -> list[int] | None:
    """PIDs של udp-sender חיים. ‏None = לא הצלחנו לסרוק (#439)."""
    root = Path("/proc")
    if not root.is_dir():
        return None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    found: list[int] = []
    saw = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            continue
        saw = True
        if comm == "udp-sender":
            found.append(int(entry.name))
    return sorted(found) if saw else None


def _hook_pids(hooks: dict) -> list[int] | None:
    getter = hooks.get("udp_sender_pids")
    if getter is None:
        return None
    try:
        return getter()
    except Exception:  # noqa: BLE001 — בדיקה שנפלה אינה "לא רץ"
        return None


def _send_in_progress(ctx) -> bool:
    sender = getattr(ctx, "sender", None)
    try:
        status = sender.status() if sender is not None else None
    except Exception:  # noqa: BLE001 — status שנפל אינו "אין סבב"
        status = None
    if status and status.get("state") in ("starting", "sending"):
        return True
    try:
        return ctx.conn.execute(
            "SELECT 1 FROM sessions WHERE state = 'running' LIMIT 1"
        ).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def sender_check(pids: list[int] | None, live_round: bool) -> dict:
    """רץ עם סבב → warn; רץ בלי סבב → bad; לא רץ → ok. לא הורג (#439).

    ‏None (הבדיקה לא רצה) אינו ok: "לא רץ" הוא הירוק, ואי-בדיקה שנראית
    כמו ירוק היא בדיוק עיקרון 5.
    """
    label = "משדר udp-sender"
    if pids is None:
        return check("udp_sender", label, "bad",
                     "לא הצלחנו לבדוק אם udp-sender רץ — אין לדעת אם נשאר יתום")
    if not pids:
        return check("udp_sender", label, "ok", "לא רץ")
    shown = ", ".join(f"PID {p}" for p in pids)
    if live_round:
        return check("udp_sender", label, "warn",
                     f"רץ ({shown}) — יש סבב פתוח")
    return check("udp_sender", label, "bad",
                 f"רץ בלי סבב ({shown}) — יתום אחרי סגירה, יש לעצור אותו")


def collect(ctx, hooks: dict, server_base: str) -> list[dict]:
    results = []
    ss_out = hooks["ss"]()

    # פורט 67 — DHCP. פנוי זה מצב לגיטימי (עוד לא הוגדר מהקונסולה).
    owner = port_owner(ss_out, 67) if ss_out else None
    if not ss_out:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "off",
                             "אי אפשר לבדוק כאן (ss לא זמין)"))
    elif owner is None:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "warn",
                             "אף אחד לא מאזין — DHCP עוד לא הודלק מלשונית הרשת"))
    elif owner == "dnsmasq":
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "ok", "dnsmasq מאזין"))
    else:
        results.append(check("dhcp_port", "פורט 67 (DHCP)", "bad",
                             f"תפוס על ידי {owner} — יתנגש עם dnsmasq"))

    # פורט 69 — TFTP. בלעדיו אין שרשרת אתחול.
    owner = port_owner(ss_out, 69) if ss_out else None
    if not ss_out:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "off",
                             "אי אפשר לבדוק כאן (ss לא זמין)"))
    elif owner is None:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "bad",
                             "אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE"))
    elif owner == "dnsmasq":
        results.append(check("tftp_port", "פורט 69 (TFTP)", "ok", "dnsmasq מגיש"))
    else:
        results.append(check("tftp_port", "פורט 69 (TFTP)", "warn",
                             f"מוגש על ידי {owner}, לא על ידי dnsmasq"))

    # dnsmasq עצמו.
    active = hooks["unit_active"]("dnsmasq")
    results.append(
        check("dnsmasq", "שירות dnsmasq", "ok", "רץ") if active == "active"
        else check("dnsmasq", "שירות dnsmasq", "off",
                   "אי אפשר לבדוק כאן (systemctl לא זמין)") if not active
        else check("dnsmasq", "שירות dnsmasq", "bad", f"מצב: {active}"))

    # שרשרת האתחול, משני קצותיה: הקבצים על שורש ה-TFTP, ומיד אחריהם
    # הקרנל וה-initramfs כפי שהתחנה מושכת אותם ב-HTTP (#333).
    root = hooks["tftp_root"]()
    missing = [name for name in BOOT_FILES if not (root / name).is_file()]
    problems = [f"חסרים ב-{root}: {', '.join(missing)}"] if missing else []
    problems += boot_asset_problems(hooks["http_size"], server_base)
    if not problems:
        results.append(check("boot_files", "קבצי האתחול", "ok",
                             f"shim, GRUB והתפריט הקבוע נמצאים ב-{root}, "
                             "ו-vmlinuz ו-initrd.img נמשכו מ-/boot בגודל מלא"))
    else:
        results.append(check("boot_files", "קבצי האתחול", "bad",
                             " · ".join(problems) + " — הריצו את המתקין"))

    # ‏#329: המתקין מעתיק את ה-shim פעם אחת ולא מעדכן. מיקרוסופט
    # דוחפת עדכוני SBAT שמבטלים shim ישן, ואז **כל הצי** מפסיק לעלות
    # ב-PXE בבת אחת — בלי שנגענו בכלום. ‏`apt upgrade` אינו מספיק:
    # הוא מעדכן את /usr/lib/shim, לא את העותק כאן.
    src, tftp = hooks["shim_src"](), root / "bootx64.efi"
    a, b = _sha256(src), _sha256(tftp)
    if a and b and a == b:
        results.append(check("shim_fresh", "עדכניות ה-shim", "ok",
                             "העותק ב-TFTP תואם למה שהמערכת מספקת"))
    elif a and b:
        results.append(check("shim_fresh", "עדכניות ה-shim", "bad",
                             f"{tftp} ישן מ-{src} — הריצו את המתקין מחדש"))
    else:
        results.append(check("shim_fresh", "עדכניות ה-shim", "unknown",
                             f"לא ניתן לקרוא {src if not a else tftp} — לא נבדק"))

    # השרת עצמו, בכתובת שהלקוחות רואים. הקונסולה שקוראת את זה כבר מדברת
    # איתנו — הבדיקה היא שהכתובת הציבורית (זו שב-GRUB) אכן עונה.
    status = hooks["http_get"](server_base.rstrip("/") + "/boot/menu?mac=00:00:00:00:00:00")
    if status == 200:
        results.append(check("server", "השרת בכתובת ההפצה", "ok",
                             f"{server_base} עונה על תפריט האתחול"))
    elif status is None:
        results.append(check("server", "השרת בכתובת ההפצה", "bad",
                             f"{server_base} לא עונה — מחשבים לא יגיעו לתפריט"))
    else:
        results.append(check("server", "השרת בכתובת ההפצה", "warn",
                             f"{server_base} החזיר {status}"))

    # udp-sender חי בלי סבב הוא יתום שתופס את פורטי ההפצה (#439).
    # הבדיקה מזהה ומראה — היא אינה הורגת: ההריגה שייכת לנתיב הסגירה.
    results.append(sender_check(_hook_pids(hooks), _send_in_progress(ctx)))

    # כרטיסי הרשת — כמה מחוברים, וכמה מגישים DHCP (לפי ההגדרות השמורות).
    nics = hooks["interfaces"]()
    up = [n["name"] for n in nics if n.get("state") == "up"]
    serving = [
        row["key"].removeprefix(dhcp.SETTING_PREFIX)
        for row in ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (dhcp.SETTING_PREFIX + "%",),
        )
        if '"enabled": true' in row["value"] or '"proxy": true' in row["value"]
    ]
    if not nics:
        results.append(check("nics", "כרטיסי רשת", "bad", "לא נמצאו כרטיסים"))
    elif not up:
        results.append(check("nics", "כרטיסי רשת", "warn",
                             "אף כרטיס לא מחובר (כבל?)"))
    else:
        detail = f"מחוברים: {', '.join(up)}"
        detail += f" · DHCP פעיל על: {', '.join(serving)}" if serving \
            else " · DHCP לא הודלק על אף כרטיס"
        results.append(check("nics", "כרטיסי רשת", "ok", detail))

    # שתי דלתות ה-SSH (#83) — לפי מה שנקרא בחזרה, לא לפי ההגדרה.
    results.extend(console_ssh.ssh_checks(console_ssh.snapshot(ctx, hooks, server_base)))

    # ‏#855: שומר הזהות — מתג כבוי הוא מצב מוצהר שנאמר כאן, וקובץ חכירות
    # שאינו נקרא הוא "כל hello מסורב", לא שקט.
    results.append(check("identity", "זהות מכונה",
                         *identity.health_status(ctx.conn, getattr(ctx, "leases", None))))

    # ואחרונות, כי אורכן משתנה: מי נופל לסוכן בלולאה עכשיו (#112), ומי
    # מדבר עם השרת מרשת שאינה וילן ההפצה (#137). שתי רשימות נפרדות —
    # פנייה מרשת אחרת אינה לולאה, ואינה נספרת כאחת.
    try:
        loops = agent_loops.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אין לולאות"
        loops = None
    results.extend(agent_loops.loop_checks(loops))

    try:
        strangers = foreign_vlan.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אף אחד לא פנה"
        strangers = None
    results.extend(foreign_vlan.vlan_checks(
        strangers, urlsplit(server_base).hostname or server_base))

    return results


def _last_seen(seconds: int) -> str:
    """שימוש: `agent_loops.loop_checks` ו-`foreign_vlan.vlan_checks`, שתיהן
    מייבאות מכאן בגוף הפונקציה (לא בראש הקובץ) כדי לא ליצור מעגל ייבוא —
    שני המודולים האלה כבר מיובאים כאן, בראש הקובץ."""
    if seconds < 60:
        return "נראה לאחרונה לפני פחות מדקה"
    return f"נראה לאחרונה לפני {seconds // 60} דק'"


#: פורט הקיוסק (`--kiosk-port` ב-main.py). אין hook שמזריק אותו לכאן
#: (בניגוד ל-server_base) — כמו 8081/4011 למטה, השורה נשארת "לא אומת"
#: עד שיתווסף hook אמיתי, ולא נצבעת ירוק בלי בדיקה (עיקרון 5).
KIOSK_PORT = 8082


def _port(port_id: str, name: str, port: str, proto: str, desc: str,
         target: str, state: str, detail: str, note: str) -> dict:
    return {"id": port_id, "name": name, "port": port, "proto": proto,
           "desc": desc, "target": target, "state": state, "detail": detail,
           "note": note}


def ports_snapshot(ctx, hooks: dict, server_base: str) -> list[dict]:
    """‏#822: מסך "פורטים" בקונסולה. כל שורה נקראת מהשרת — לא רשימה
    קבועה ב-JS — ומצב "לא אומת" נאמר בפירוש כשאין עדיין hook שבודק
    (עיקרון 5: לא לצייר ירוק על מה שלא נבדק)."""
    ss_out = hooks["ss"]()
    entries = []

    owner = port_owner(ss_out, 69) if ss_out else None
    if not ss_out:
        entries.append(_port("tftp", "TFTP", "69", "udp",
            "bootloader — shim/GRUB והתפריט", "תחנות (PXE)", "off",
            "אי אפשר לבדוק כאן (ss לא זמין)",
            "לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת"))
    elif owner is None:
        entries.append(_port("tftp", "TFTP", "69", "udp",
            "bootloader — shim/GRUB והתפריט", "תחנות (PXE)", "bad",
            "אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE",
            "לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת"))
    elif owner == "dnsmasq":
        entries.append(_port("tftp", "TFTP", "69", "udp",
            "bootloader — shim/GRUB והתפריט", "תחנות (PXE)", "ok",
            "dnsmasq מגיש", "לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת"))
    else:
        entries.append(_port("tftp", "TFTP", "69", "udp",
            "bootloader — shim/GRUB והתפריט", "תחנות (PXE)", "warn",
            f"מוגש על ידי {owner}, לא על ידי dnsmasq",
            "לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת"))

    status = hooks["http_get"](
        server_base.rstrip("/") + "/boot/menu?mac=00:00:00:00:00:00")
    if status == 200:
        http_state, http_detail = "ok", f"{server_base} עונה על תפריט האתחול"
    elif status is None:
        http_state, http_detail = "bad", f"{server_base} לא עונה"
    else:
        http_state, http_detail = "warn", f"{server_base} החזיר {status}"
    entries.append(_port("http_boot", "HTTP", "8080", "tcp",
        "אתחול (boot/vmlinuz/initrd) ו-API הסוכן", "תחנות, סוכן",
        http_state, http_detail,
        "לפתוח ב-FW: TCP 8080 מוילן ההפצה לשרת"))

    # ‏#703 (tracer 5): הקונסולה מוגשת ב-HTTPS בלבד (תעודה חתומה-עצמית).
    entries.append(_port("http_console", "HTTPS", "8081", "tcp",
        "קונסולת הניהול (TLS, תעודה עצמית)", "דפדפן (מנהל)", "off",
        "אין hook שקורא את ההאזנה על הפורט הזה — לא אומת",
        "לפתוח ב-FW: TCP 8081 (HTTPS) מתחנת הניהול בלבד — לא לוילן הכיתות"))

    entries.append(_port("pxe_proxy", "PXE", "4011", "udp", "PXE proxy",
        "תחנות", "off", "אין hook שקורא את ההאזנה על הפורט הזה — לא אומת",
        "לפתוח ב-FW: UDP 4011 מוילן ההפצה לשרת"))

    sender = sender_check(_hook_pids(hooks), _send_in_progress(ctx))
    entries.append(_port("multicast", "Multicast", "9000–9001", "udp",
        "שידור אימג׳ (fanout)", "תחנות", sender["state"], sender["detail"],
        "לפתוח ב-FW: UDP 9000–9001 בתוך וילן ההפצה בלבד"))

    monitor_on = monitor.stations_enabled(ctx.conn)
    entries.append(_port("monitor", "Monitor (RFB)", str(monitor.MONITOR_PORT),
        "tcp", "צפייה מרחוק במחשבי בנייה/שיכפול (#690)", "תחנות (build/cloner)",
        "warn" if monitor_on else "off",
        ("המתג monitor:stations דלוק — כל תחנה שעולה מפעילה שירות RFB"
         if monitor_on else
         "המתג monitor:stations כבוי — אף תחנה לא מפעילה שירות צפייה"),
        f"לפתוח ב-FW: TCP {monitor.MONITOR_PORT} מהשרת לוילן ההפצה — "
        "רק כשהמתג דלוק"))

    entries.append(_port("kiosk", "HTTP", str(KIOSK_PORT), "tcp",
        "אפליקציית הקיוסק (מחשבי שיכפול)", "דפדפן (קלונרים)", "off",
        "אין hook שקורא את ההאזנה על הפורט הזה — לא אומת",
        f"לפתוח ב-FW: TCP {KIOSK_PORT} מהקלונרים לשרת"))

    ssh_state = console_ssh.snapshot(ctx, hooks, server_base)
    stations_check = next(
        c for c in console_ssh.ssh_checks(ssh_state) if c["id"] == "ssh_stations")
    entries.append(_port("ssh_stations", "SSH", str(ssh_switch.SSH_PORT), "tcp",
        "מעטפת טכנאי + dropbear בתחנות שעולות עם imagectl.debug",
        "תחנות", stations_check["state"], stations_check["detail"],
        f"לפתוח ב-FW: TCP {ssh_switch.SSH_PORT} מתחנת הטכנאי לתחנות — רק "
        "בזמן איתור תקלה, לא כברירת מחדל"))

    return entries


def create_health_router(ctx, server_base: str, hooks: dict | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    # מתגי ה-SSH חולקים את אותו מנגנון הזרקה: בבדיקות אף פעולה אינה
    # נוגעת ב-sshd אמיתי, בדיוק כמו ב-dhcp_hooks.
    hooks = {**default_hooks(), **ssh_switch.default_hooks(), **(hooks or {})}

    @router.get("/health")
    def health(user=Depends(admin_only)):
        return collect(ctx, hooks, server_base)

    @router.get("/ports")
    def ports(user=Depends(current_user)):
        """‏#822: מסך הפורטים. מחובר בלבד — בניגוד ל-/health, admin
        אינו נדרש: אלה פורטי תשתית ולא רשימת דלתות פתוחות למנהל בלבד."""
        del user
        return ports_snapshot(ctx, hooks, server_base)

    router.include_router(console_ssh.create_ssh_router(ctx, hooks, server_base))
    return router
