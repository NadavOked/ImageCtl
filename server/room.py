"""סבב חדר השיכפולים — הפצה מצטברת על פני גלים (אפיון סעיף 29).

הסבב חי בשרת ומצהיר יעד סך-כוננים. כל גל הוא session רגיל על קבוצת
השיכפול — אותו מנוע שידור, אותם דיווחי התקדמות. מה שמייחד את החדר:

- המוכנות נמדדת בכוננים (מגירות), לא במחשבים. גל יוצא כשהמגירות
  הטריות מכסות את היתרה, או בלחיצת "התחל עכשיו". בלי טיימר —
  בחדר עומד אדם ליד המכונות.
- מגירה מזוהה ב-serial של הכונן: כונן שכבר נכתב בסבב לא נספר שוב,
  ומכונה שהמגירות שלה לא הוחלפו לא מצטרפת לגל הבא.
- כשגל מסתיים והיעד לא הושג, הגל הבא נפתח אוטומטית — "כיבוי,
  החלפת מגירות, הדלקה — סבב שני מתחיל אוטומטית".

הערה: אין כאן `from .api import ServerContext` — זה היה סוגר מעגל
(api → hello → room). ctx מגיע כפרמטר ומשמש כפי שהוא.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, registry, reports
from .db import journal, now_iso, update_one
from .images import restore_refusal
from .imagefit import validate_expand_choice
from .sessions import SessionError, SessionStore, SessionSuperseded

log = logging.getLogger("imagectl.room")

CLONERS_GROUP = "grp_CLONERS"
#: מי רשאי להפעיל את חדר השיכפולים. רשימת-היתר מפורשת, ולא "מחובר".
#:
#: אותה הכרעה כמו ``station.ROUND_OPENER_ROLES`` (#94), ובאותו נימוק:
#: היום זה כל התפקידים שקיימים ולכן זה לא משנה התנהגות, אבל הקוד שלף
#: את ``admin_only`` ומעולם לא בדק דבר — ותפקיד שלישי (צופה, מבקר,
#: חשבון ניטור) היה מקבל ביום היוולדו את ``/wake`` ו-``/start``, שהם
#: הפעולה שדורסת כל מגירה מחוברת בחדר. "לא ידענו מה התפקיד" הוא סירוב.
#:
#: ``GET`` נשאר פתוח לכל מחובר — קריאת מצב אינה הרסנית.
ROOM_OPERATOR_ROLES = ("admin", "deploy")
#: מכונה שדיברה עם השרת בטווח הזה נחשבת ערה — הסוכן דוגם כל ~2 שניות.
AWAKE_SECONDS = 30
#: מעל כמה שניות בלי **דיווח התקדמות** חבר גל מוכרז אבוד (#450). הדופק
#: הנמדד הוא progress ולא hello: מכונה שכותבת מפסיקה לשלוח hello אך
#: מדווחת התקדמות כל ~2 שניות (#449), ולכן הפקעה לפי hello הייתה קוטלת
#: מכונה בריאה עסוקה בכתיבה. ‏`updated_at` זז על כל דיווח (וגם על מגירה
#: קפואה — הוא זמן הדיווח, לא זמן התנועה), ומכונה שנעלמה מפסיקה להזיז
#: אותו. הסף נגזר מקצב הדיווח (~2 ש') עם שוליים גסים; אינו מדידת שדה.
LOST_SECONDS = 180


# --- שאילתות טהורות ----------------------------------------------------------


def active_round(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM room_rounds WHERE state = 'active' LIMIT 1"
    ).fetchone()


def _written(round_row: sqlite3.Row) -> set[str]:
    return set(json.loads(round_row["written_serials"]))


def _selected_ports(round_row: sqlite3.Row | None, mac: str) -> set[int] | None:
    """הפורטים שנבחרו לכתיבה עבור המכונה הזאת בסבב. ‏None = סבב ישן (כל
    הדיסקים). קבוצה ריקה = המכונה לא נבחרה כלל."""
    if round_row is None or round_row["target_slots_json"] is None:
        return None
    for item in json.loads(round_row["target_slots_json"]):
        if item["mac"] == mac:
            return set(item["ports"])
    return set()


def _disks(conn: sqlite3.Connection, mac: str) -> list[dict]:
    row = conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or not row["disks_json"]:
        return []
    disks = json.loads(row["disks_json"])
    return disks if isinstance(disks, list) else []


def fresh_serials(conn: sqlite3.Connection, mac: str, written: set[str],
                  selected_ports: set[int] | None = None) -> list[str]:
    """המגירות של המכונה שעוד לא נכתבו בסבב הנוכחי. ‏selected_ports מסנן
    לפורטים שנבחרו בלבד (‏None = כל הדיסקים, סבב ישן)."""
    return [
        d["serial"] for d in _disks(conn, mac)
        if isinstance(d, dict) and d.get("serial") and d["serial"] not in written
        and (selected_ports is None or d.get("port") in selected_ports)
    ]


def has_fresh_drawers(conn: sqlite3.Connection, mac: str) -> bool:
    """שער ההצטרפות לגל: מכונה שהמגירות שלה כבר נכתבו נשארת בהמתנה.

    בלי סבב חדר פעיל אין מה לסנן — מצטרפים כרגיל.
    """
    round_row = active_round(conn)
    if round_row is None:
        return True
    return bool(fresh_serials(conn, mac, _written(round_row),
                              _selected_ports(round_row, mac)))


def drawer_list(conn: sqlite3.Connection, mac: str, written: set[str],
                member: sqlite3.Row | None = None,
                selected_ports: set[int] | None = None) -> list[dict]:
    """המגירות של המכונה כפי שהטכנאי רואה אותן: חריץ, התקן ומצב (#27).

    ‏`port` הוא ה-ataN שהסוכן דיווח בממשק 2 — החריץ הפיזי, לא סדר הגילוי
    של הקרנל. סוכן ישן, VM או בקר לא-ATA לא מדווחים אותו, והוא יוצא `null`:
    הקונסולה נופלת חזרה לתצוגה לפי שם ההתקן, בלי להיכשל (עיקרון 1).
    """
    targets = {}
    if member is not None:
        targets = {t.get("dev"): t
                   for t in json.loads(member["targets_json"] or "[]")
                   if isinstance(t, dict)}
    drawers = []
    for disk in _disks(conn, mac):
        if not isinstance(disk, dict):
            continue
        port = disk.get("port")
        target = targets.get(disk.get("dev")) or {}
        drawers.append({
            "dev": disk.get("dev"),
            "port": port if isinstance(port, int) and not isinstance(port, bool)
            else None,
            # ‏#553: הסריאל והדגם הם מה שמזהה כונן **ביד**. ‏`port` אומר
            # לאיזו מגירה ללכת; הסריאל אומר איזה כונן זה כשמחזיקים
            # אותו — וכשאין `port` (‏NVMe, ‏VM, בקר לא-ATA) הוא כל מה
            # שנשאר. שורת כשל בלי אחד מהם שולחת אדם לחפש.
            "serial": disk.get("serial") or None,
            "model": disk.get("model") or None,
            "fresh": bool(disk.get("serial")) and disk["serial"] not in written,
            # ‏#695: האם הפורט הזה נבחר כיעד בסבב (לצביעת הגריד/בחירה).
            "selected": (selected_ports is not None
                         and isinstance(port, int) and not isinstance(port, bool)
                         and port in selected_ports),
            "state": target.get("state"),
            "error": target.get("error"),
            # ‏#552: המפעיל ראה ‏"0 of 5 written" במשך 75 דקות. המונה
            # הזה סופר מגירות ש**סיימו**, ולכן הוא 0 גם כשהכול רץ מצוין
            # וגם כשהכול תקוע — ואי-אפשר להבחין. הבייטים הם ההבחנה.
            "bytes_written": _int(target.get("bytes_written")),
            "bytes_total": _int(target.get("bytes_total")),
            "stalled_s": _stalled_s(target.get("moved_at")),
            # ‏#872: בכמה עלה מונה ה-CRC (199) בסבב הזה, מדוח הסוכן. ‏None =
            # לא נמדד — לא 0 (עיקרון 5). >0 מוצג "CRC +N · לבדוק כבל", ואינו
            # צובע את הדיסק.
            "crc_delta": target["crc_delta"]
            if isinstance(target.get("crc_delta"), int)
            and not isinstance(target.get("crc_delta"), bool) else None,
            # ‏#652: בריאות SMART כפי שדווחה ב-hello (ממטמון הסוכן), כדי
            # שהמפעיל יראה "מגירה N: SMART אזהרה" **לפני** שהוא משגר סבב.
            # ‏`unchecked` = לא-נבדק, לא כשל (עיקרון 5).
            "smart": disk.get("smart") or "unchecked",
            # ‏#710: הגודל שהסוכן דיווח (sysinfo.sh), כדי שהגריד יראה קיבולת
            # אמיתית לכל דיסק ולא 0. חסר => 0, לא נחסם (עיקרון 5).
            "size_bytes": _int(disk.get("size_bytes")),
        })
    return drawers


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _stalled_s(moved_at: str | None) -> int | None:
    """כמה שניות עברו מאז שהבייטים של המגירה הזאת גדלו — ‏`None` כשלא ידוע.

    ⚠️ **‏`None` ו-`0` הם שני מצבים שונים ואסור לקפל אותם.** ‏`0` הוא
    "נמדד, והמגירה זזה עכשיו"; ‏`None` הוא "אין חותמת" — מגירה שטרם
    דיווחה, או שרת שהתחיל לפני שהשדה הזה קיים. מסך שמצייר `None`
    כאפס מציג "הכול זז" על מגירה שלא נמדדה כלל (עיקרון 5).
    """
    if not moved_at:
        return None
    try:
        moved = datetime.fromisoformat(moved_at)
    except (TypeError, ValueError):
        return None
    return max(0, int((datetime.now(timezone.utc) - moved).total_seconds()))


def ready_drives(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row) -> int:
    """כמה כוננים טריים מחוברים למכונות שכבר הצטרפו לגל הפתוח."""
    written = _written(round_row)
    return sum(
        len(fresh_serials(conn, member["mac"], written,
                          _selected_ports(round_row, member["mac"])))
        for member in store.members(round_row["wave_session_id"])
    )


# --- מחזור החיים -------------------------------------------------------------


def _validate_target_slots(conn: sqlite3.Connection, raw) -> list[dict]:
    """‏#695: מאמת את בחירת דיסקי היעד ומחזיר צורה קנונית. דוחה בגלוי כל
    קלט פגום — MAC זר, פורט מחוץ ל-drawer_count, פורט כפול, או פורט שאינו
    מחובר כרגע — כדי שלעולם לא ייכתב דיסק לא-נכון (עיקרון 4/5/7). אין
    fallback ל"כל הדיסקים": בחירה ריקה נדחית."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("יש לבחור לפחות דיסק יעד אחד")
    selected = []
    seen_macs = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("בחירת דיסקי היעד אינה תקינה")
        mac = registry.normalize_mac(item.get("mac", ""))
        ports = item.get("ports")
        if mac is None or mac in seen_macs or not isinstance(ports, list):
            raise ValueError("בחירת דיסקי היעד אינה תקינה")
        row = conn.execute(
            "SELECT drawer_count FROM machines WHERE mac = ? AND group_id = ?",
            (mac, CLONERS_GROUP),
        ).fetchone()
        if row is None:
            raise ValueError("נבחר מחשב שאינו שייך לחדר השכפול")
        if (not ports or len(set(ports)) != len(ports) or any(
                isinstance(p, bool) or not isinstance(p, int)
                or p < 1 or p > row["drawer_count"] for p in ports)):
            raise ValueError(f"בחירת המגירות של {mac} אינה תקינה")
        live = {
            d.get("port") for d in _disks(conn, mac)
            if isinstance(d, dict) and d.get("serial")
        }
        if not set(ports) <= live:
            raise ValueError(
                f"אי אפשר לפתוח סבב: אחת המגירות שנבחרו ב־{mac} אינה מחוברת")
        selected.append({"mac": mac, "ports": sorted(ports)})
        seen_macs.add(mac)
    return sorted(selected, key=lambda item: item["mac"])


def open_round(ctx, image_id: str, target_drives: int, user: str,
               target_slots=None, expand_partition=None) -> dict:
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise ValueError("אימג' לא קיים בספרייה")
    # ‏#381: אימג' הקשור למכונה אחת אינו נשפך על מגירות. אין כאן רשימת
    # יעדים בכלל — הכוננים יותקנו במכונות שאיש עוד אינו יודע מי הן —
    # ולכן `restore_refusal` מסרב אותו, וזה הכיוון הנכון (עיקרון 5).
    refusal = restore_refusal(manifest, None)
    if refusal is not None:
        raise ValueError(refusal)
    if target_drives < 1:
        raise ValueError("יעד הכוננים חייב להיות חיובי")
    # ‏#59: בחירת ההרחבה חלה על **כל** גלי הסבב, ולכן היא נשמרת ברמת
    # ה-round ולא ב-session של גל בודד — כל גל חדש קורא אותה מחדש.
    expand_choice = validate_expand_choice(manifest, expand_partition)
    machines = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
    ).fetchone()["n"]
    if machines == 0:
        raise ValueError("אין מחשבי שיכפול רשומים — רשמו אותם בקונסולה קודם")
    if active_round(ctx.conn) is not None:
        raise SessionError("כבר יש סבב חדר פעיל")

    # ‏#695: בחירת דיסקי יעד (אופציונלית — סבב ישן בלי בחירה כותב לכל
    # הדיסקים). כשנבחרו — מאמתים לפני שנוגעים בשום דבר, ומוודאים שיש מספיק
    # יעדים בסבב לגל הראשון.
    selected = None
    if target_slots is not None:
        selected = _validate_target_slots(ctx.conn, target_slots)
        selected_count = sum(len(item["ports"]) for item in selected)
        if target_drives < selected_count:
            raise ValueError(
                "מספר היעדים בסבב קטן ממספר הדיסקים שנבחרו לגל הראשון")

    # פתיחת הגל תופסת את חריץ הסבב היחיד במערכת ומעירה את החדר (WoL).
    wave_id = ctx.store.open(
        CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=machines, opened_by=user, expand_partition=expand_choice,
    )
    round_id = "room_" + secrets.token_hex(4)
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, target_slots_json,"
        " expand_partition, state, wave_session_id, opened_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)",
        (round_id, image_id, target_drives,
         json.dumps(selected, separators=(",", ":")) if selected is not None else None,
         expand_choice, wave_id, user, now_iso()),
    )
    ctx.conn.commit()
    journal(ctx.conn, "room_open",
            f"{round_id} {image_id} target={target_drives}"
            # ‏#59: אותו כלל כמו session_open — "auto" אינו נרשם, כדי
            # שסבב שלא נגע בהרחבה לא ייראה כמו החלטה שמישהו קיבל.
            + (f" expand={expand_choice}" if expand_choice != "auto" else ""),
            user)
    return {"id": round_id, "wave_session_id": wave_id}


def round_label(ctx, round_row: sqlite3.Row) -> str:
    """שם הסבב כפי שהמפעיל רואה אותו — שם האימג' שהוא משדר.

    לסבב אין שם משלו: יש `id` אקראי שאינו מופיע על שום מסך, ומספר גל
    שהוא ספרה. מה שהמפעיל **קורא** בכותרת הוא שם האימג', וזה מה שהוא
    מקליד כדי לעצור (עיקרון 7). הנפילה חזרה ל-`image_id` היא אותה
    נפילה בדיוק כמו ב-`status_view` — **מקום אחד**, אחרת סבב שהאימג'
    שלו נמחק באמצע היה בלתי-ניתן לעצירה.
    """
    manifest = ctx.library.get(round_row["image_id"])
    return manifest["name"] if manifest else round_row["image_id"]


def close_round(ctx, user: str, confirm_name: str) -> None:
    round_row = active_round(ctx.conn)
    if round_row is None:
        raise SessionError("אין סבב חדר פעיל")
    # פעולה הרסנית מאחורי הקלדת שם — אותו דפוס כמו מחיקת אימג'
    # (`console_library.delete_image`), ובאותה שורה באפיון. לפני #533
    # ‏POST ריק עם עוגייה תקפה הרג משדר חי, וההקלדה נאכפה במסך בלבד.
    if confirm_name != round_label(ctx, round_row):
        raise ValueError("השם שהוקלד אינו זהה לשם האימג' שהסבב משדר")
    wave = ctx.conn.execute(
        "SELECT id, state FROM sessions WHERE id = ?",
        (round_row["wave_session_id"],),
    ).fetchone()
    if wave is not None and wave["state"] in ("open", "running"):
        ctx.store.close(wave["id"], user)
    # store.close קורא on_closed → sender.stop רק כשהגל עוד open/running.
    # גל שכבר סגור (או חסר) היה מחזיר ok והמשדר נשאר חי (#439).
    left = ctx.sender.stop()
    if left is not None:
        who = f"PID {left}" if left > 0 else "לא הצלחנו לוודא שהוא מת"
        raise SessionError(
            f"udp-sender עדיין רץ ({who}) אחרי ניסיון העצירה — הסבב לא נסגר"
        )
    ctx.conn.execute(
        "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
        (now_iso(), round_row["id"]),
    )
    ctx.conn.commit()
    journal(ctx.conn, "room_close",
            f'{round_row["id"]} written={round_row["written_drives"]}'
            f'/{round_row["target_drives"]}', user)


def _iso_or_none(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def _mark_lost(conn: sqlite3.Connection, store: SessionStore, wave: sqlite3.Row,
               members: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """מסמן `lost` חבר גל שלא דיווח התקדמות מעל `LOST_SECONDS` (#450).

    הגרייס נמדד מ-`max(updated_at, started_at)`: גל שרק התחיל לרוץ נותן
    לכל חבר `LOST_SECONDS` להשמיע קול ראשון לפני שהוא מוכרז אבוד — אחרת
    חבר שהצטרף בשלב הפתיחה (‏`updated_at` בן דקות, כי בפתיחה אין
    progress) היה מופקע ברגע שהגל מתחיל. שני מקורות `None` (אין חותמת)
    אינם "אבוד" אלא "לא נמדד", והכיוון הבטוח הוא לא להכריז (עיקרון 5).

    ה-UPDATE מותנה במצב המדויק שנקרא: דיווח שהגיע בין הקריאה לכתיבה כבר
    שינה אותו, והתביעה הזו נותנת לו לנצח (‏`update_one` גם משחרר את
    הנעילה כשלא תאם — #54). מחזיר את החברים המעודכנים כשמשהו השתנה.
    """
    now = datetime.now(timezone.utc)
    floor = _iso_or_none(wave["started_at"])
    lost = []
    for m in members:
        if m["state"] in reports.FINAL:
            continue
        refs = [t for t in (_iso_or_none(m["updated_at"]), floor) if t is not None]
        if refs and (now - max(refs)).total_seconds() > LOST_SECONDS:
            lost.append(m)
    if not lost:
        return members
    marked = [
        m for m in lost
        if update_one(
            conn,
            "UPDATE session_members SET state = ?, updated_at = ?"
            " WHERE session_id = ? AND mac = ? AND state = ?",
            (reports.LOST, now_iso(), wave["id"], m["mac"], m["state"]),
        )
    ]
    if not marked:
        return members
    # ‏`update_one` כבר עשה commit לכל שינוי מצב, ו-`journal` נועל וכותב
    # בעצמו (#379) — ולכן אין כאן `conn.commit()` חשוף (#450, #54).
    for m in marked:
        journal(conn, "client_lost", f'{m["mac"]} in {wave["id"]}')
    return store.members(wave["id"])


def tick(conn: sqlite3.Connection, store: SessionStore) -> None:
    """Advance on cloner hellos, never on screen reads.

    With no hello, an idle room waits. No timer or additional SQLite writer.
    """
    round_row = active_round(conn)
    if round_row is None:
        return
    wave = None
    if round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (round_row["wave_session_id"],)
        ).fetchone()

    if wave is None or wave["state"] == "closed":
        # הסבב פעיל והגל שלו כבר אינו. בלי הענף הזה זה מצב **יציב**:
        # כל דופק עתידי נוסג כאן, החדר נראה פתוח ואף גל אינו נפתח (#217).
        _resume(conn, store, round_row, wave)
    elif wave["state"] == "open":
        remaining = round_row["target_drives"] - round_row["written_drives"]
        ready = ready_drives(conn, store, round_row)
        if 0 < remaining <= ready:
            store.start_auto(wave["id"])
    elif wave["state"] == "running":
        members = store.members(wave["id"])
        # ‏#450: מכונה שנעלמה (כובתה, איבדה רשת, קרסה) לעולם אינה מדווחת
        # מצב סופי, ובלעדי הפקעה הגל ממתין לה לנצח וגל נוסף אינו נפתח.
        # מפקיעים אותה **לפני** בדיקת הסיום, ובודקים מול `reports.FINAL`
        # (הטרמינליים + `lost`).
        members = _mark_lost(conn, store, wave, members)
        # ‏`partial` הוא סיום לכל דבר (#67): מכונה שכתבה שתי מגירות מתוך
        # שלוש אמרה את דברה, והגל אינו ממתין לה עוד. הספירה שאחריו היא
        # ממילא לפי מגירה — המגירה שנכשלה פשוט לא נספרת ונשארת טרייה.
        if members and all(m["state"] in reports.FINAL for m in members):
            _finish_wave(conn, store, round_row, members)


#: תקרה לאורך פרט ביומן. הודעת חריגה יכולה לגרור traceback שלם, ושורת
#: יומן שאי אפשר לקרוא על המסך אינה מוסיפה למפעיל דבר.
_DETAIL_MAX = 200


def _one_line(value: object) -> str:
    """ערך כפי שמותר לכתוב אותו לשורת יומן או לוג: **שורה אחת**.

    **לא** מפני שהוא מזייף רשומה בטבלת היומן — הוא אינו יכול:
    ‏`journal()` מעביר את `detail` כפרמטר קשור לעמודת TEXT, ושורה חדשה
    בתוכו נשארת שדה אחד. בקרה שלילית שהייתה נשענת על "רשומה שנייה
    בטבלה" **הייתה נכשלת להיכשל**, וזה בדיוק סוג השומר שנראה עובד.

    שתי הסיבות האמיתיות:

    1. ‏`log.exception` הולך ליומן ה-systemd, שהוא **טקסטואלי**. שורה
       חדשה בהודעת חריגה מייצרת שם שורה שנראית עצמאית — כלומר רשומה
       מזויפת ביומן שהמפעיל קורא. זה אותו יומן שכבר נכווינו בו ב-#179.
    2. תקרת אורך: הודעת חריגה יכולה לגרור traceback שלם, ושדה שאי-אפשר
       לקרוא על המסך אינו מוסיף למפעיל דבר.

    ‏`normalize_mac` מחזיר hex קנוני או `None`, ולכן מסלול ה-MAC אינו
    ניתן לניצול ממילא. **הווקטור החי היחיד הוא טקסט החריגה** — והשמירה
    יושבת בנקודת הכתיבה, לא בהנחה על מי שקורא.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:_DETAIL_MAX]


def pulse(conn: sqlite3.Connection, store: SessionStore, mac: str) -> bool:
    """הדופק של הסבב מתוך מסלול ה-hello — לעולם אינו מפיל אותו.

    אותה מוסכמה כמו `agent_loops.note`: הפונקציה לעולם אינה זורקת
    ולעולם אינה נוגעת בתשובה שנשלחת למכונה, ועיקרון 1 נשאר בדיוק כפי
    שהוא. ‏hello שמחזיר 500 אומר למחשב השיכפול שהשרת מת, וזה שקר —
    השרת חי, רק קידום הסבב נכשל (#177).

    **אבל לא בשקט (עיקרון 5).** גל שלא נפתח הוא בדיוק מה שהמפעיל חייב
    לראות: הסבב נראה תקוע והחדר ממתין לאדם שיבחין. לכן הכישלון נרשם
    ביומן — שם הוא מגיע למסך — וגם ב-`log.exception` עם ה-traceback.
    ‏`except: pass` היה הופך את הבאג לשקוף, וזה גרוע מ-500.

    מוחזר האם הדופק עבר, למי שכן רוצה לדעת.
    """
    who = _one_line(mac)
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל hello
        log.exception("room tick from %s failed", who)
        try:
            journal(conn, "room_tick_failed", f"{who} — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל hello
            log.exception("room tick failure from %s was not journaled", who)
        return False


def sweep(conn: sqlite3.Connection, store: SessionStore) -> bool:
    """הדופק של הסבב מהרקע — **גל שאיש אינו צופה בו חייב להתקדם** (#456).

    ‏`tick` מקודם היום משני מקורות בלבד: ‏hello של משכפל (`pulse`) ו-GET
    ‏`/overview`. שניהם תלויים במשהו חיצוני — מכונה ששולחת hello, או אדם
    שמסתכל על מבט-העל. כשכל המכונות נעלמו (כובו, נותקו, קרסו) **אין יותר
    hello**, ומסך החדר מושך `/api/console/room` שהוא read-only (#446,
    ‏#453) — ולכן `_mark_lost` לא נקרא, אף חבר לא מוכרז אבוד, והגל תקוע
    לנצח (#456, וזה גם המקרה שנשאר פתוח ב-#659 כשהמכונה **האחרונה**
    נעלמת). לולאת הרקע (`app._room_clock`) קוראת לכאן בקצב קבוע, ולכן
    ההפקעה אינה תלויה עוד בצופה או ב-hello של שורד.

    אותה מוסכמה כמו `pulse`: לעולם אינה זורקת. הרקע אינו יכול להחזיר 500
    לאיש, אבל כישלון בקידום הסבב עדיין חייב להיראות — לכן `log.exception`
    עם ה-traceback ושורת יומן, ולא `except: pass` שמעלים את הבאג (עיקרון
    5). מוחזר האם הדופק עבר, למי שכן רוצה לדעת (הבדיקה).
    """
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל את לולאת הרקע
        log.exception("room background tick failed")
        try:
            journal(conn, "room_tick_failed", f"background — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל את הלולאה
            log.exception("room background tick failure was not journaled")
        return False


def _tally(conn: sqlite3.Connection, round_row: sqlite3.Row,
           members: list[sqlite3.Row]) -> tuple[int, set[str]]:
    """כמה מגירות נכתבו בסבב עד כה, ואילו — לפי serial.

    **הספירה חוזרת על עצמה בבטחה:** מגירה שכבר בקבוצה אינה נספרת שוב,
    ולכן ספירה חוזרת של אותו גל (במסלול השחזור של `_resume`) מחזירה
    בדיוק את אותו מספר. זה מה שמאפשר לשחזר בלי לדעת אם הספירה הקודמת
    הספיקה להיכתב.
    """
    written = _written(round_row)
    new_drives = 0
    for member in members:
        serial_of = {
            d.get("dev"): d.get("serial")
            for d in _disks(conn, member["mac"]) if isinstance(d, dict)
        }
        for target in json.loads(member["targets_json"] or "[]"):
            serial = serial_of.get(target.get("dev"))
            if target.get("state") == "done" and serial and serial not in written:
                written.add(serial)
                new_drives += 1
    return round_row["written_drives"] + new_drives, written


def _resume(conn: sqlite3.Connection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row | None) -> None:
    """הסבב פעיל, והגל שהוא מצביע עליו סגור (או שאין כזה) — ‏#217.

    שלוש דרכים נמדדות להגיע לכאן: סגירת הגל מ-endpoint הסבבים הכללי של
    הקונסולה (בלי שום תקלה — ‏`close_session` אינו יודע שזה גל של חדר),
    וכשל בכתיבת `room_rounds` אחרי ש-`store.close` או `store.open` כבר
    בוצע להם commit — שני מסלולי הנסיגה של `_finish_wave`.

    **התביעה כאן היא איפוס המצביע**, בדיוק כמו שהסגירה המותנית היא
    התביעה ב-#177: ‏`UPDATE` מותנה שרק תהליכון אחד מנצח בו, ומי שהפסיד
    רואה `NULL` וכבר אינו במצב הזה. איפוס לפני הפתיחה ולא אחריה, כדי
    שכשל **בפתיחה** ישאיר מצב שהדופק הבא יודע לתקן — ולא סבב שמצביע על
    גל שאינו קיים.
    """
    total, written = round_row["written_drives"], _written(round_row)
    if wave is not None:
        total, written = _tally(conn, round_row, store.members(wave["id"]))
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " wave_session_id = NULL WHERE id = ? AND state = 'active'"
            " AND wave_session_id = ?",
            (total, json.dumps(sorted(written)), round_row["id"], wave["id"]),
        ):
            return
        conn.commit()

    target = round_row["target_drives"]
    if total >= target:
        conn.execute(
            "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
            (now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done", f'{round_row["id"]} written={total}/{target}')
        return
    if wave is not None:
        # לא בשקט (עיקרון 5): גל שנעלם מתחת לסבב הוא בדיוק מה שהמפעיל
        # צריך לראות, גם כשהחדר מתאושש לבדו. שורה אחת, כי התביעה למעלה
        # מצליחה פעם אחת.
        journal(conn, "room_wave_lost",
                f'{round_row["id"]} {wave["id"]} written={total}/{target}')
    _attach_wave(conn, store, round_row, total)


def _attach_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, total: int) -> None:
    """מצמיד לסבב גל — קיים או חדש.

    **קודם מאמצים.** גל שנפתח ואיש לא הספיק להצביע עליו כבר מחזיק את
    חריץ המולטיקאסט היחיד, ופתיחת גל נוסף הייתה נכשלת ב-`TAKEN` בכל
    דופק מכאן והלאה. הזיהוי הוא קבוצה + קידומת + אימג' — חתימה שסבב
    כיתה אינו נושא.
    """
    existing = store.active_for_group(CLONERS_GROUP)
    opened_here = False
    if (existing is not None and existing["prefix"] == "ROOM"
            and existing["image_id"] == round_row["image_id"]):
        wave_id = existing["id"]
    else:
        machines = conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
        ).fetchone()["n"]
        try:
            wave_id = store.open(
                CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
                expected_clients=max(1, machines), opened_by="",
                expand_partition=round_row["expand_partition"],
            )
        except SessionError:
            # החריץ תפוס בידי סבב אחר. **לא נבלע**: המצב כבר ביומן
            # (`room_wave_lost`) וגם על המסך (`wave_state: closed`),
            # והדופק הבא ינסה שוב. חריגה כאן הייתה מייצרת
            # `room_tick_failed` בכל hello — רעש שמכסה על השורה שכן
            # אומרת משהו.
            return
        opened_here = True
    if not update_one(
        conn,
        "UPDATE room_rounds SET wave_session_id = ?, wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id IS NULL",
        (wave_id, round_row["id"]),
    ):
        # תהליכון אחר הקדים אותנו. גל שאיש אינו מצביע עליו יחזיק את החריץ
        # לנצח, ולכן סוגרים את מה ש**אנחנו** פתחנו — אבל רק אחרי שנבדק
        # שהסבב אינו מצביע עליו: המנצח יכול היה לאמץ בדיוק את הגל הזה
        # (זה הענף הראשון כאן), וסגירתו הייתה מחזירה את החדר למצב שממנו
        # באנו. שאילתה, ולא הנחה — הראיה החיובית היא שהמצביע אינו שלנו.
        if opened_here and conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


def _finish_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, members: list[sqlite3.Row]) -> None:
    """הגל הסתיים: סופרים לפי serial אילו מגירות נכתבו, וממשיכים."""
    total, written = _tally(conn, round_row, members)

    if total >= round_row["target_drives"]:
        # הסגירה היא התביעה: שני תהליכונים שהגיעו לכאן עם אותו גל —
        # רק זה שסגר אותו בפועל כותב את השורה התחתונה של הסבב (#177).
        if not store.close(round_row["wave_session_id"], ""):
            return
        conn.execute(
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " state = 'closed', closed_at = ? WHERE id = ?",
            (total, json.dumps(sorted(written)), now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done",
                f'{round_row["id"]} written={total}/{round_row["target_drives"]}')
        return

    # היעד לא הושג — הגל הבא נפתח מעצמו וממתין למגירות מוחלפות.
    machines = conn.execute(
        "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
    ).fetchone()["n"]
    # סגירת הגל הגמור ופתיחת הבא הן טרנזאקציה אחת: בין השתיים החריץ
    # היה פנוי, ושני דופקים בו-זמנית הפכו את השני ל-`TAKEN` — ה-hello
    # שהריץ אותו החזיר 500 (#177), ופותח שלישי (סבב כיתה מהקונסולה)
    # שהיה נכנס לאותו חלון היה משאיר את החדר בלי גל בכלל.
    try:
        wave_id = store.open(
            CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
            expected_clients=max(1, machines), opened_by="",
            replaces=round_row["wave_session_id"],
            expand_partition=round_row["expand_partition"],
        )
    except SessionSuperseded:
        # תהליכון אחר סגר את הגל הזה ופתח את הבא. זה אינו כישלון אלא
        # בדיוק מה שהאטומיות נועדה לייצר: פותח אחד, לא שניים.
        return
    conn.execute(
        "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
        " wave_session_id = ?, wave_number = wave_number + 1 WHERE id = ?",
        (total, json.dumps(sorted(written)), wave_id, round_row["id"]),
    )
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


# --- התצוגה ------------------------------------------------------------------


def _fresh(stamp: str | None) -> bool:
    if not stamp:
        return False
    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    # ‏#774: חסם תחתון. חותמת בעתיד (סטיית שעון, DB מיובא) נותנת גיל
    # שלילי שמקיים `<= AWAKE_SECONDS` — והמכונה נראתה "ערה" לכל משך
    # הסטייה. חותמת עתידית אינה ראיה שהמכונה חיה עכשיו.
    age = datetime.now(timezone.utc) - seen
    return timedelta(0) <= age <= timedelta(seconds=AWAKE_SECONDS)


def _is_awake(*stamps: str | None) -> bool:
    """ערה אם דיברה איתנו בחלון האחרון — ב-hello **או** בדיווח progress.

    ‏#449: ``last_seen`` מתעדכן מ-hello בלבד, אבל בזמן שחזור הכתיבה
    סינכרונית והסוכן חדל לפעום — לולאת הרקע מדווחת **progress** ולא
    hello. מכונה שכותבת בפועל דיווחה התקדמות לפני שניות, וזו ראיה
    חיובית שהיא חיה; סימונה כ"ישנה" קיפל "עסוקה בכתיבה" ו"נפלה/איבדה
    רשת" לאותו סימן (עיקרון 5). ``updated_at`` של חבר הסבב הוא הראיה
    השנייה — מספיקה אחת מהשתיים בחלון."""
    return any(_fresh(stamp) for stamp in stamps)


#: מעל כמה שניות בלי תנועה מגירה נחשבת עצורה. נגזר מ-1,896 דגימות
#: של סבב 08/09: מגירה בריאה עצרה עד 120 שניות. אותו מספר כמו
#: ‏`ROOM_STALL_S` בסוכן, וכאן הוא משמש רק להכרעה שלמטה.
STALL_SECONDS = 180


def _stream_stalled(machines: list[dict]) -> bool:
    """האם **כל** המגירות הכותבות עצרו יחד — כלומר הזרם, לא הכונן.

    ⚠️ **זו ההבחנה ש-`fanout` אינו יכול לעשות, והשרת כן.** ‏`fanout`
    רואה מגירה אחת, ולכן כשהיא נעצרת הוא מסיק `drive too slow`
    ומאשים אותה. בסבב 08/09 זה היה שגוי: נמדד ש**ארבע המגירות
    הבריאות קפאו באותן דגימות בדיוק** — ‏81%–100% חפיפה — ‏**כולל
    שתי מגירות במכונה פיזית אחרת.** ‏26 דגימות שבהן כל החמש עצרו.

    ‏Garbage collection של כונן אינו מסתנכרן עם כונן במארז אחר.
    מקור משותף פירושו סיבה משותפת, ותווית "כונן תקוע" על עצירת זרם
    שולחת אדם להחליף חומרה תקינה — בדיוק הבאג שהמסך הזה בא לתקן.

    פחות משתי מגירות כותבות = אין רוב, ואין מה להסיק (עיקרון 5):
    מגירה בודדת שעצרה היא **המגירה**, ואין ראיה לזרם.
    """
    writing = [d for m in machines for d in m.get("drawer_list", [])
               if d.get("state") == "writing"]
    if len(writing) < 2:
        return False
    # ‏`None` אינו "לא עצורה" — הוא "לא נמדד". מגירה שלא נמדדה אינה
    # יכולה להשתתף בהכרעה שכולן עצרו.
    if any(d.get("stalled_s") is None for d in writing):
        return False
    return all(d["stalled_s"] >= STALL_SECONDS for d in writing)


def status_view(ctx) -> dict:
    """מה שמסך החדר מציג: המכונות, המגירות, והסבב אם יש."""
    round_row = active_round(ctx.conn)
    written = _written(round_row) if round_row else set()
    member_of = {}
    if round_row is not None:
        member_of = {
            m["mac"]: m for m in ctx.store.members(round_row["wave_session_id"])
        }

    machines = []
    for row in ctx.conn.execute(
        "SELECT m.mac, m.suffix, m.drawer_count, d.last_seen FROM machines m"
        " LEFT JOIN net_devices d ON d.mac = m.mac"
        " WHERE m.group_id = ? ORDER BY m.suffix", (CLONERS_GROUP,)
    ):
        member = member_of.get(row["mac"])
        drawers = drawer_list(ctx.conn, row["mac"], written, member,
                              _selected_ports(round_row, row["mac"]))
        # ‏#710: כל עוד #704 (הגדרת מגירות במסוף) לא נבנה, העמודה NULL —
        # ואז ברירת המחדל היא מספר הדיסקים שהמכונה מדווחת בפועל (הפורט
        # הגבוה ביותר, או הספירה כשאין פורטים), כדי שהגריד לא יסתיר דיסק.
        _ports = [d["port"] for d in drawers if isinstance(d["port"], int)]
        _count = row["drawer_count"] or (max(_ports + [len(drawers)]) if drawers else 1)
        machines.append({
            "mac": row["mac"],
            "name": row["suffix"],
            "drawer_count": _count,   # #695 הוגדר במסוף / #710 ברירת מחדל לפי הדיסקים
            "awake": _is_awake(row["last_seen"],
                               member["updated_at"] if member else None),
            "drawers": len(drawers),
            "fresh_drawers": sum(1 for d in drawers if d["fresh"]),
            "drawer_list": drawers,
            "joined": member is not None,
            "state": member["state"] if member else None,
            "bytes_written": member["bytes_written"] if member else 0,
            "bytes_total": member["bytes_total"] if member else 0,
            "error": member["error"] if member else None,
        })

    view = {"round": None, "machines": machines,
            "stream_stalled": _stream_stalled(machines)}
    if round_row is not None:
        wave = ctx.conn.execute(
            "SELECT state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
        view["round"] = {
            "id": round_row["id"],
            "image_id": round_row["image_id"],
            "image_name": round_label(ctx, round_row),
            "target_drives": round_row["target_drives"],
            "written_drives": round_row["written_drives"],
            "remaining_drives": round_row["target_drives"] - round_row["written_drives"],
            "wave_number": round_row["wave_number"],
            "wave_state": wave["state"] if wave else "closed",
            "ready_drives": ready_drives(ctx.conn, ctx.store, round_row),
            "opened_by": round_row["opened_by"],
        }
    return view


# --- ה-API -------------------------------------------------------------------


def create_room_router(ctx, wake=None) -> APIRouter:
    """`wake` מוזרק מ-app.py כדי ששליחת ה-WoL תהיה אותה פונקציה בכל
    המערכת (וניתנת לזיוף בבדיקות)."""
    router = APIRouter(prefix="/api/console/room")
    current_user, _admin_only = auth.dependencies(ctx.conn)

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שמותר לו להפעיל את החדר."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.get("")
    def status(user=Depends(current_user)):
        # Observation is never a room state-machine event (#446).
        return status_view(ctx)

    @router.post("")
    async def open_(request: Request, user=Depends(room_operator)):
        body = await request.json()
        try:
            return open_round(
                ctx, body.get("image_id", ""),
                int(body.get("target_drives", 0)), user[0],
                body.get("target_slots"),          # #695: בחירת דיסקי יעד (אופציונלי)
                body.get("expand_partition"),      # #59: בחירת ההרחבה (אופציונלי)
            )
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc) or "בקשה לא תקינה")

    @router.post("/start")
    def start_now(user=Depends(room_operator)):
        round_row = active_round(ctx.conn)
        if round_row is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        try:
            ctx.store.start_now(round_row["wave_session_id"], user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @router.post("/wake")
    def wake_room(user=Depends(room_operator)):
        if wake is None:
            raise HTTPException(503, "WoL אינו מחובר בשרת הזה")
        sent = wake()
        journal(ctx.conn, "wol_sent", f"{CLONERS_GROUP} count={sent}", user[0])
        # ‏`sent` ולא `woken`: חבילת WoL היא UDP broadcast בלי ACK —
        # ‏`sendto` שהצליח פירושו שהקרנל קיבל 102 בייט, ותו לא. אין
        # ראיה שהמכונה קמה, והשם `woken` טען טענה שלא נמדדה (#528;
        # המודול עצמו מונה `sent`). הראיה החיובית להתעוררות היא hello
        # שמגיע בחלון — וזו הכרעה נפרדת.
        # הסיבה עולה למסך ולא רק ליומן: "0 מחשבים" בלי הסבר שולח את
        # הטכנאי לחפש WoL ב-BIOS של 12 מכונות, כשהסיבה היא כבל אחד
        # בשרת (#74). ‏getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל.
        return {"sent": int(sent),
                "failed": len(getattr(sent, "failed", ())),
                "reasons": list(getattr(sent, "reasons", ()))}

    @router.post("/close")
    async def close_(request: Request, user=Depends(room_operator)):
        # גוף ריק או לא-JSON הוא בדיוק המקרה שההקלדה נועדה לתפוס, ולכן
        # הוא נופל לאישור ריק — 400 עם ההסבר, ולא 500 שנראה כתקלת שרת.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        try:
            close_round(ctx, user[0], typed)
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    return router
