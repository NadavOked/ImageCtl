"""הסבב כפי שהמסך מציג אותו — בשמות, לא במזהים.

הופרד מ-`sessions.py` כדי שניהול המצב (מי תופס את חריץ השידור, מתי
מתחילים, מי סיים) לא יגדל יחד עם התצוגה. אין כאן לוגיקה — רק תרגום
של שורה בבסיס הנתונים לצורה שהקונסולה ומסך התחנה קוראים.
"""

from __future__ import annotations

import json
import sqlite3

from . import bootguard, disk_events
from .sessions import MULTICAST


def label(session: sqlite3.Row, library) -> str:
    """שם הסבב כפי שהמפעיל רואה אותו — שם האימג' שהוא משדר.

    לסבב אין שם משלו: יש `id` אקראי שאינו מופיע על שום מסך. מה שהמפעיל
    **קורא** בכותרת (`image_name` למטה, ובמסך התחנה "משדר: ...") הוא שם
    האימג', וזה מה שהוא מקליד כדי לעצור (עיקרון 7, ‏#581). הנפילה חזרה
    ל-`image_id` היא אותה נפילה שב-`round_label` של חדר השיכפולים,
    ומאותה סיבה: **מקום אחד**, אחרת סבב שהאימג' שלו נמחק באמצע היה
    בלתי-ניתן לעצירה — תיקון שגרוע מהבאג.
    """
    manifest = library.get(session["image_id"])
    return manifest["name"] if manifest else session["image_id"]


def build(store, session: sqlite3.Row, library) -> dict:
    """מחשב מזוהה בשם המחשב שייכתב לו (קידומת-סיומת), לא ב-MAC: זה מה
    שמופיע על המסך בכיתה, וזה מה שמחפשים כשמשהו נכשל. ה-MAC נשאר
    בנתונים לטכנאי, אבל הוא לא הכותרת.
    """
    conn = store.conn
    group = conn.execute(
        "SELECT label, role FROM groups WHERE id = ?", (session["group_id"],)
    ).fetchone()
    names = {
        row["mac"]: row["suffix"]
        for row in conn.execute(
            "SELECT mac, suffix FROM machines WHERE group_id = ?",
            (session["group_id"],),
        )
    }
    # שם המחשב נגזר מקידומת הסבב, ולמשיכת יוניקאסט אין קידומת אמיתית:
    # אחרי שחזור כזה השם נקבע מתוך Windows (ראו ui.sh בסוכן).
    classroom = (group is not None and group["role"] == "classroom"
                 and session["kind"] == MULTICAST)

    # בריאות SMART וההכרעות של הסבב (#652), מקובצות לפי מכונה, כדי
    # שהקונסולה תראה "דיסק N: אזהרה -> rescue" ליד כל מחשב (צפייה בלבד
    # ב-v1; ההכרעה נעשתה בסוכן).
    disks_by_mac: dict[str, list] = {}
    for ev in disk_events.for_session(conn, session["id"]):
        disks_by_mac.setdefault(ev["mac"], []).append(ev)

    members = []
    for m in store.members(session["id"]):
        suffix = names.get(m["mac"])
        hostname = f'{session["prefix"]}-{suffix}' if classroom and suffix else None
        members.append({
            "mac": m["mac"],
            "name": suffix,
            "hostname": hostname,
            "state": m["state"],
            "done": bool(m["done"]),
            "bytes_written": m["bytes_written"],
            "bytes_total": m["bytes_total"],
            "error": m["error"],
            "updated_at": m["updated_at"],
            "disks": disks_by_mac.get(m["mac"], []),
        })

    return {
        "id": session["id"],
        "state": session["state"],
        # הזרם: 'multicast' תופס את חריץ השידור היחיד, 'unicast' לא (#60).
        "kind": session["kind"],
        # בחירת המחשבים של הסבב; None = כל הקבוצה. הקונסולה מציגה
        # לפי זה מי שייך לסבב הזה ומי יחכה לסבב הבא.
        "roster": json.loads(session["roster_json"])
        if session["roster_json"] else None,
        "group_id": session["group_id"],
        "group_label": group["label"] if group else session["group_id"],
        "group_role": group["role"] if group else "unknown",
        "image_id": session["image_id"],
        "image_name": label(session, library),
        "prefix": session["prefix"],
        "expected_clients": session["expected_clients"],
        # סבב של מכונה אחת אינו הפצה לכיתה — הקונסולה מסמנת אותו אחרת.
        "single": session["expected_clients"] == 1,
        "joined": len(members),
        # מי אתחל שוב ושוב לסבב הזה ולא הגיע — לפי MAC. מכונה שהסוכן
        # שלה נכשל לפני `hello` אינה חברה בסבב, ולכן אין לה שורה
        # ברשימה למעלה: בלי זה היא נראית בדיוק כמו מחשב שלא נדלק (#64).
        # `blocked` = התקציב נגמר והשרת כבר מוריד אותה לדיסק (#75).
        "stuck": bootguard.repeats(conn, f'session:{session["id"]}'),
        "starts_in_seconds": store.starts_in_seconds(session)
        if session["state"] == "open" else 0,
        "members": members,
    }
