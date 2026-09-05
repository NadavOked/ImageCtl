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

from . import agent_loops, auth, console_ssh, dhcp, foreign_vlan, ssh_switch

BOOT_FILES = ("bootx64.efi", "grubx64.efi", "grub/grub.cfg")

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
    }


def _http_probe(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def _http_body(url: str) -> tuple[int | None, str]:
    """קוד *וגם* גוף. חיווי ה-SSH של התחנות נשען על מה שבאמת נכתב
    בתפריט שהשרת מגיש, ולכן קוד תשובה לבדו אינו מספיק לו."""
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
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
        with urllib.request.urlopen(url, timeout=5) as response:
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
    results.extend(ssh_checks(console_ssh.snapshot(ctx, hooks, server_base)))

    # ואחרונות, כי אורכן משתנה: מי נופל לסוכן בלולאה עכשיו (#112), ומי
    # מדבר עם השרת מרשת שאינה וילן ההפצה (#137). שתי רשימות נפרדות —
    # פנייה מרשת אחרת אינה לולאה, ואינה נספרת כאחת.
    try:
        loops = agent_loops.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אין לולאות"
        loops = None
    results.extend(loop_checks(loops))

    try:
        strangers = foreign_vlan.current(ctx.conn)
    except Exception:  # noqa: BLE001 — שאילתה שנפלה אינה "אף אחד לא פנה"
        strangers = None
    results.extend(vlan_checks(strangers,
                               urlsplit(server_base).hostname or server_base))

    return results


def _last_seen(seconds: int) -> str:
    if seconds < 60:
        return "נראה לאחרונה לפני פחות מדקה"
    return f"נראה לאחרונה לפני {seconds // 60} דק'"


def loop_checks(loops: list[dict] | None) -> list[dict]:
    """מחשבים שהגיעו לסוכן אף שנשלחו לדיסק המקומי — שורה לכל מחשב.

    ‏None פירושו שהרשימה לא נקראה, וזו שורה **אדומה** ולא ריקה: מסך
    ריק מפני שהשאילתה נפלה נראה בדיוק כמו מסך ריק מפני שהכול תקין,
    וזו בדיוק ההנחה שעיקרון 5 אוסר.

    גם השורה הירוקה נזהרת בלשונה. היא אומרת מה **נמדד** — לא הגיע
    hello כזה בעשר הדקות האחרונות — ולא "אין מחשבים תקועים": מחשב
    כבוי שותק בדיוק כמו מחשב שתוקן, ואין אירוע שאומר "נרפא".
    """
    label = "מחשבים שנופלים לסוכן"
    if loops is None:
        return [check("agent_loops", label, "bad",
                      "רשימת הלולאות לא נקראה — אין לדעת אם יש מחשבים תקועים")]
    if not loops:
        return [check("agent_loops", label, "ok",
                      f"אף מחשב לא הגיע לסוכן בלי משימה ובלי סבב ב-"
                      f"{agent_loops.SILENCE_SECONDS // 60} הדקות האחרונות. "
                      "מחשב כבוי שותק גם הוא — ירידה מהרשימה אינה \"תוקן\"")]
    rows = [check("agent_loops", label, "bad",
                  f"{len(loops)} מחשבים הגיעו לסוכן אף שנשלחו לדיסק המקומי — "
                  "השרשור לדיסק נכשל אצלם")]
    rows += [
        check(f"agent_loop:{loop['mac']}", loop["name"] or loop["mac"], "bad",
              f"{loop['hits']} פעמים בלולאה הנוכחית · {_last_seen(loop['silent_seconds'])}")
        for loop in loops
    ]
    return rows


def vlan_checks(contacts: list[dict] | None, deploy_vlan: str) -> list[dict]:
    """מי מדבר עם השרת מרשת שאינה וילן ההפצה — שורה לכל מחשב (#137).

    **רק אדום.** אין כאן דירוג בין "לגיטימי" ל"תקלה", כי לשרת אין ממה
    להסיק אותו: אותה פנייה בדיוק היא נדב שעומד ליד מחשב ומושך אימג'
    ביד, ומחשב שיכפול שחובר לשקע הלא נכון וייראה תקין עד שיתברר שאינו
    עובד. ההכרעה היא שהאירוע נראה בשני המקרים.

    השורה אומרת **מאיזו רשת** נפתחה הפנייה ומה מצופה — כי בלי זה
    "משהו לא בסדר ברשת" הוא בדיוק המשפט ששולח לחפש במקום הלא נכון.

    ‏None פירושו שהרשימה לא נקראה, וזו שורה **אדומה** ולא ריקה. וגם
    הירוקה נזהרת בלשונה: היא אומרת מה נמדד — לא הגיעה פנייה כזאת
    בעשר הדקות האחרונות — ולא "כל המחשבים בשקע הנכון". מחשב כבוי
    שותק בדיוק כמו מחשב שהועבר, ואין אירוע שאומר "נרפא".
    """
    label = "מחשבים שפונים מרשת אחרת"
    if contacts is None:
        return [check("off_vlan", label, "bad",
                      "רשימת הפניות מרשת אחרת לא נקראה — אין לדעת אם מחשב "
                      "מדבר עם השרת מחוץ לווילן ההפצה")]
    if not contacts:
        return [check("off_vlan", label, "ok",
                      f"אף מחשב לא פנה לשרת מחוץ לווילן ההפצה ב-"
                      f"{foreign_vlan.SILENCE_SECONDS // 60} הדקות האחרונות. "
                      "מחשב כבוי שותק גם הוא — ירידה מהרשימה אינה \"תוקן\"")]
    rows = [check("off_vlan", label, "bad",
                  f"{len(contacts)} מחשבים פונים לשרת מרשת שאינה וילן ההפצה — "
                  "בדקו לאיזה שקע הם מחוברים")]
    rows += [
        check(f"off_vlan:{one['mac']}", one["name"] or one["mac"], "bad",
              f"פנתה מ-{one['address']} · וילן ההפצה הוא {deploy_vlan} · "
              f"{one['hits']} פניות · {_last_seen(one['silent_seconds'])}")
        for one in contacts
    ]
    return rows


def ssh_checks(state: dict) -> list[dict]:
    """שתי שורות, ובשתיהן **הראיה** היא מה שנצבע.

    ‏"אי אפשר לבדוק" אינו אפור כאן אלא אדום, בניגוד לשאר המסך: פורט 67
    שלא נבדק משאיר PXE שלא עובד ורואים את זה מיד, אבל דלת SSH שלא
    נבדקה נראית בדיוק כמו דלת סגורה — וזו ההנחה שהמשימה הזאת קיימת
    כדי לשבור. דלת פתוחה שיודעים עליה היא צהוב; דלת שאי אפשר לראות
    היא אדום.
    """
    rows = []
    st = state["stations"]
    if st["evidence"] == "unknown":
        rows.append(check("ssh_stations", "SSH בתחנות", "bad",
                          f"{st['detail']} — הבדיקה עצמה לא רצה, ואי אפשר "
                          "להסיק מכך שסגור"))
    elif st["evidence"] == "open":
        rows.append(check("ssh_stations", "SSH בתחנות", "warn",
                          "פתוח — כל תחנה שעולה מריצה dropbear ומעטפת טכנאי. "
                          + st["detail"]))
    elif st["enabled"]:
        # המתג דלוק והתפריט נקי: מישהו או משהו לא הגיע ליעד.
        rows.append(check("ssh_stations", "SSH בתחנות", "bad",
                          "המתג דלוק אבל הדגל אינו בתפריט שמוגש — המתג לא תפס"))
    else:
        rows.append(check("ssh_stations", "SSH בתחנות", "ok", st["detail"]))

    live = state["listeners"]
    open_nics = [n for n in state["interfaces"] if n["listening"]]
    unwanted = [n["name"] for n in state["interfaces"]
                if bool(n["listening"]) != n["enabled"]]
    port = live["port"]
    if not live["checked"]:
        rows.append(check("ssh_server", "SSH לשרת", "bad",
                          f"טבלת הסוקטים לא נקראה ({live['reason']}) — לא "
                          f"ידוע מי מאזין בפורט {port}"))
    elif live["wildcard"]:
        rows.append(check("ssh_server", "SSH לשרת", "bad",
                          f"‏sshd מאזין על כל הממשקים (0.0.0.0/::) בפורט "
                          f"{port} — כולל וילן הכיתות"))
    elif unwanted:
        rows.append(check("ssh_server", "SSH לשרת", "bad",
                          "מה שמאזין אינו מה שהמתג אומר: " + ", ".join(unwanted)))
    elif open_nics:
        rows.append(check("ssh_server", "SSH לשרת", "warn",
                          "פתוח על: " + ", ".join(
                              f"{n['name']} ({', '.join(n['addresses']) or 'ללא כתובת'})"
                              for n in open_nics)))
    elif state["stray"]:
        rows.append(check("ssh_server", "SSH לשרת", "warn",
                          "מאזין על כתובת שאינה של אף כרטיס מוכר: "
                          + ", ".join(state["stray"])))
    else:
        rows.append(check("ssh_server", "SSH לשרת", "ok",
                          f"אף כרטיס לא מאזין בפורט {port} (נבדק בטבלת "
                          "הסוקטים של הקרנל)"))
    return rows


def create_health_router(ctx, server_base: str, hooks: dict | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)
    # מתגי ה-SSH חולקים את אותו מנגנון הזרקה: בבדיקות אף פעולה אינה
    # נוגעת ב-sshd אמיתי, בדיוק כמו ב-dhcp_hooks.
    hooks = {**default_hooks(), **ssh_switch.default_hooks(), **(hooks or {})}

    @router.get("/health")
    def health(user=Depends(admin_only)):
        return collect(ctx, hooks, server_base)

    router.include_router(console_ssh.create_ssh_router(ctx, hooks, server_base))
    return router
