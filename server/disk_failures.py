"""זיכרון כשלי הכתיבה — הצד של השרת ב-#874.

הכרעת נדב 16/09: הסימון על הדיסק (#845) **נפסל** — הסוכן אינו משנה דיסק
מחוץ לשיכפול. במקומו השרת זוכר, מדיווח ההתקדמות (ממשק 4) של יעד שנכשל
בכתיבה, **מי** נכשל בשתי צורות: הסידורי של הדיסק, והחריץ (מכונה+פורט).
שתיהן נדרשות: בראיה השנייה (07:03) דיסק אחר על אותו פורט נכשל באותה
חתימה — הפורט אשם והדיסקים חפים; ואותיות ‏sd* מתחלפות בין אתחולים,
ולכן אף דבר כאן אינו מזוהה לפי אות.

באתחול, hello (ממשק 3) עונה לסוכן `disk_failures` — הרשומות הפתוחות
שתואמות לסידוריים שהמכונה שלחה **או** לחריץ באותה מכונה — והסוכן מציב
את הדיסק אדום (`failed_last`) לפני הסבב, בלי לכתוב עליו דבר.

הזיכרון אינו גובר על המפעיל: "נקה" (`cleared_at`) תמיד זמין — הדיסק
הוחלף, הכבל תוקן — ורשומה מנוקה אינה צובעת עוד.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from . import ata_cause
from .db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagectl.disk_failures")


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def record(conn: sqlite3.Connection, session_id: str, mac: str, target: dict) -> dict | None:
    """רושם כשל כתיבה של יעד אחד, פעם אחת לכל (סבב, מכונה, יעד).

    נקרא מ-`reports.ingest` על יעד `failed` שנושא `ata_log` (סוכן חדש;
    סוכן ישן שאינו שולח אותו אינו נרשם — אין לו סידורי ולא פורט, ואין
    למה להצמיד את הזיכרון). מחזיר את הרשומה שנכתבה, או `None` כשכבר
    הייתה. ‏`mac` כבר קנוני (הקורא נרמל).
    """
    dev = target.get("dev")
    if not isinstance(dev, str) or not dev or "ata_log" not in target:
        return None
    lines = ata_cause.clean_log(target.get("ata_log"))
    cause = ata_cause.classify(lines)
    serial = target.get("serial") if isinstance(target.get("serial"), str) else None
    error = target.get("error") if isinstance(target.get("error"), str) else None
    row = {
        "session_id": session_id, "mac": mac, "dev": dev, "serial": serial or None,
        "port": _int(target.get("port")), "ata_port": _int(target.get("ata_port")),
        "at": now_iso(), "cause": cause, "error": error,
    }
    with _write_lock, writing(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO disk_failures (session_id, mac, dev, serial, port,"
            " ata_port, at, cause, error, ata_log) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (session_id, mac, dev, row["serial"], row["port"], row["ata_port"],
             row["at"], cause, error, json.dumps(lines)),
        )
        inserted = cur.rowcount == 1
        row["id"] = cur.lastrowid if inserted else None
    if not inserted:
        return None
    journal(conn, "disk_failure",
            f"{mac} {dev} serial={serial or '?'} port={row['port'] if row['port'] is not None else '?'}"
            f" cause={cause}")
    return row


def open_for(conn: sqlite3.Connection, mac: str, disks: list | None) -> list[dict]:
    """מה ה-hello עונה: הרשומות הפתוחות שתואמות למכונה הזאת (ממשק 3).

    ‏**סידורי** — כל רשומה פתוחה על אחד הסידוריים שהמכונה דיווחה, מכל
    מכונה שהיא (הדיסק נדד). ‏**חריץ** — כל רשומה פתוחה של אותה מכונה
    שיש לה פורט. ‏`port` בתשובה הוא **החריץ במכונה הזאת**: ברשומה של
    מכונה אחרת שתאמה לפי סידורי הוא `null` — פורט 2 של מכונה אחרת אינו
    פורט 2 כאן, והסוכן משווה פורט רק מול המכונה שלו.
    """
    serials = []
    for d in disks or []:
        s = d.get("serial") if isinstance(d, dict) else None
        if isinstance(s, str) and s and s not in serials:
            serials.append(s)
    marks = ",".join("?" * len(serials))
    sql = ("SELECT serial, mac, port, cause, at FROM disk_failures"
           " WHERE cleared_at IS NULL AND ((mac = ? AND port IS NOT NULL)")
    params: list = [mac]
    if serials:
        sql += f" OR serial IN ({marks})"
        params += serials
    sql += ") ORDER BY at"
    out = []
    for r in conn.execute(sql, params).fetchall():
        out.append({
            "serial": r["serial"],
            "port": r["port"] if r["mac"] == mac else None,
            "cause": r["cause"],
            "at": r["at"],
        })
    return out


def list_open(conn: sqlite3.Connection) -> list[dict]:
    """הרשימה לקונסולה: כל הרשומות הפתוחות, החדשה ראשונה."""
    rows = conn.execute(
        "SELECT id, session_id, mac, dev, serial, port, ata_port, at, cause, error,"
        " ata_log FROM disk_failures WHERE cleared_at IS NULL ORDER BY at DESC, id DESC"
    ).fetchall()
    out = []
    for r in rows:
        try:
            lines = json.loads(r["ata_log"] or "[]")
        except ValueError:
            lines = []
        out.append({
            "id": r["id"], "session_id": r["session_id"], "mac": r["mac"],
            "dev": r["dev"], "serial": r["serial"], "port": r["port"],
            "disk_number": r["port"], "ata_port": r["ata_port"], "at": r["at"],
            "cause": r["cause"], "error": r["error"], "ata_log": lines,
        })
    return out


def clear(conn: sqlite3.Connection, failure_id: int, by: str = "") -> bool:
    """"נקה": הרשומה מקבלת `cleared_at`. אמת רק אם רשומה פתוחה נסגרה
    עכשיו — רשומה שאינה קיימת או שכבר נוקתה אינה "נוקתה" (עיקרון 5)."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE disk_failures SET cleared_at = ? WHERE id = ? AND cleared_at IS NULL",
            (now_iso(), failure_id),
        )
        done = cur.rowcount == 1
    if done:
        journal(conn, "disk_failure_cleared", f"#{failure_id}", user=by)
    return done
