"""טבלת ה-MAC והקבוצות — הזיכרון המוסדי של המערכת.

הסיומת קבועה לנצח למכונה פיזית (סעיף 10 באפיון). לכן שני חוקים נאכפים
כאן ולא נדחים לקונסולה: סיומת חייבת להיות דו-ספרתית או INS, ו-MAC שכבר
רשום עם סיומת אחרת הוא שגיאה — לא הערה.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .db import journal, now_iso

_MAC_CLEAN = re.compile(r"[^0-9a-f]")
_SUFFIX_NUM = re.compile(r"^\d{1,2}$")


def normalize_mac(text: str) -> str | None:
    """שלוש הווריאציות מסעיף 10 → התבנית הקנונית, או None.

    בכוונה מחמירה יותר מה-normalize של המחולל: לשם מגיע קלט מ-GRUB
    וכל חריגה מסתיימת בדיסק מקומי; לכאן מגיעה הקלדה של אדם, וטעות
    צריכה להיתפס, לא להיבלע.
    """
    if not isinstance(text, str):
        return None
    hexonly = _MAC_CLEAN.sub("", text.strip().lower())
    if len(hexonly) != 12:
        return None
    # מוודאים שלא נזרקו תווים "אמיתיים" — רק מפרידים מוכרים.
    if re.sub(r"[0-9a-fA-F:\-. ]", "", text.strip()):
        return None
    return ":".join(hexonly[i : i + 2] for i in range(0, 12, 2))


def normalize_suffix(text: str) -> str | None:
    """"5" → "05", "ins" → "INS", כל דבר אחר → None."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    if s.upper() == "INS":
        return "INS"
    if _SUFFIX_NUM.match(s):
        return s.zfill(2)
    return None


def normalize_name(role: str, text: str) -> str | None:
    """השם שניתן למכונה, לפי תפקיד הקבוצה.

    בכיתה השם הוא הסיומת שתיכנס לשם המחשב — ולכן החוקים נוקשים
    (01-99 או INS). מחשב בנייה ומחשבי שיכפול לא מקבלים שם מחשב,
    אז שם חופשי קצר ("עמדה 3") מותר.
    """
    if role == "classroom":
        return normalize_suffix(text)
    if not isinstance(text, str):
        return None
    s = text.strip()
    return s if 0 < len(s) <= 32 else None


@dataclass
class ImportLine:
    line_no: int
    raw: str
    mac: str | None = None
    suffix: str | None = None
    error: str | None = None


def parse_paste(text: str, role: str = "classroom") -> list[ImportLine]:
    """מפרק הדבקה מרובה: שורה = MAC ואז השם, מופרדים ברווח/פסיק/טאב.

    לא נוגע ב-DB — פענוח בלבד, כדי שהקונסולה תוכל להציג תצוגה מקדימה
    לפני שמירה. כפילות בתוך ההדבקה עצמה מסומנת כשגיאה כאן.
    """
    lines: list[ImportLine] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = ImportLine(line_no=i, raw=stripped)
        parts = re.split(r"[,\t ]+", stripped, maxsplit=1)
        if len(parts) != 2:
            item.error = "צריך MAC ואחריו שם"
        else:
            item.mac = normalize_mac(parts[0])
            item.suffix = normalize_name(role, parts[1].strip(" ,\t"))
            if item.mac is None:
                item.error = "MAC לא תקין"
            elif item.suffix is None:
                item.error = (
                    "סיומת חייבת להיות 01-99 או INS"
                    if role == "classroom"
                    else "שם באורך 1-32 תווים"
                )
            elif item.mac in seen:
                item.error = f"כפילות בהדבקה — כבר הופיע בשורה {seen[item.mac]}"
            else:
                seen[item.mac] = i
        lines.append(item)
    return lines


def import_lines(
    conn: sqlite3.Connection, group_id: str, lines: list[ImportLine], user: str
) -> tuple[int, list[ImportLine]]:
    """שומר את השורות התקינות. מחזיר (כמה נשמרו, השורות שנדחו).

    MAC שכבר רשום עם אותה סיומת — עדכון קבוצה שקט. עם סיומת אחרת —
    דחייה: הסיומת קבועה לנצח, ושינוי שלה הוא פעולה מפורשת, לא ייבוא.
    """
    saved = 0
    rejected: list[ImportLine] = []
    for item in lines:
        if item.error or item.mac is None or item.suffix is None:
            rejected.append(item)
            continue
        row = conn.execute(
            "SELECT suffix FROM machines WHERE mac = ?", (item.mac,)
        ).fetchone()
        if row and row["suffix"] != item.suffix:
            item.error = (
                f"רשום כבר עם הסיומת {row['suffix']} — "
                "הסיומת קבועה למכונה; מחיקה מפורשת לפני שינוי"
            )
            rejected.append(item)
            continue
        conn.execute(
            "INSERT INTO machines (mac, suffix, group_id, added_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (mac) DO UPDATE SET group_id = excluded.group_id",
            (item.mac, item.suffix, group_id, now_iso()),
        )
        saved += 1
    conn.commit()
    journal(conn, "mac_import", f"group={group_id} saved={saved} rejected={len(rejected)}", user)
    return saved, rejected


def group_role(conn: sqlite3.Connection, group_id: str) -> str | None:
    row = conn.execute("SELECT role FROM groups WHERE id = ?", (group_id,)).fetchone()
    return row["role"] if row else None


def add_machine(
    conn: sqlite3.Connection, mac_raw: str, name_raw: str, group_id: str, user: str
) -> str:
    """הוספה ידנית של מכונה אחת. מחזיר את ה-MAC הקנוני; זורק ValueError."""
    role = group_role(conn, group_id)
    if role is None:
        raise ValueError("קבוצה לא קיימת")
    mac = normalize_mac(mac_raw)
    if mac is None:
        raise ValueError("MAC לא תקין")
    name = normalize_name(role, name_raw)
    if name is None:
        raise ValueError(
            "סיומת חייבת להיות 01-99 או INS" if role == "classroom" else "שם באורך 1-32 תווים"
        )
    if conn.execute("SELECT 1 FROM machines WHERE mac = ?", (mac,)).fetchone():
        raise ValueError("ה-MAC כבר רשום — ערכו אותו במקום להוסיף שוב")
    conn.execute(
        "INSERT INTO machines (mac, suffix, group_id, added_at) VALUES (?, ?, ?, ?)",
        (mac, name, group_id, now_iso()),
    )
    conn.commit()
    journal(conn, "machine_add", f"{mac} name={name} group={group_id}", user)
    return mac


def update_machine(
    conn: sqlite3.Connection, mac_raw: str, name_raw: str | None,
    group_id: str | None, user: str,
) -> None:
    """עריכה מפורשת — הדרך היחידה לשנות שם קיים (הייבוא דוחה שינוי שקט)."""
    mac = normalize_mac(mac_raw)
    row = conn.execute(
        "SELECT m.suffix, m.group_id, g.role FROM machines m"
        " JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (mac,)
    ).fetchone() if mac else None
    if row is None:
        raise ValueError("מכונה לא רשומה")
    target_group = group_id or row["group_id"]
    role = group_role(conn, target_group)
    if role is None:
        raise ValueError("קבוצה לא קיימת")
    name = row["suffix"] if name_raw is None else normalize_name(role, name_raw)
    if name is None:
        raise ValueError(
            "סיומת חייבת להיות 01-99 או INS" if role == "classroom" else "שם באורך 1-32 תווים"
        )
    conn.execute(
        "UPDATE machines SET suffix = ?, group_id = ? WHERE mac = ?",
        (name, target_group, mac),
    )
    conn.commit()
    journal(conn, "machine_edit", f"{mac} name={name} group={target_group}", user)


def lookup(conn: sqlite3.Connection, mac: str) -> sqlite3.Row | None:
    """המכונה + הקבוצה + התפקיד, בשורה אחת. התפקיד נגזר מהחברות בקבוצה."""
    return conn.execute(
        "SELECT m.mac, m.suffix, m.note, g.id AS group_id, g.label, g.role "
        "FROM machines m JOIN groups g ON g.id = m.group_id WHERE m.mac = ?",
        (mac,),
    ).fetchone()


def export_csv(conn: sqlite3.Connection) -> str:
    """mac,suffix,group — לגיבוי ולעריכה חיצונית."""
    rows = conn.execute(
        "SELECT mac, suffix, group_id FROM machines ORDER BY group_id, suffix"
    ).fetchall()
    out = ["mac,suffix,group"]
    out.extend(f"{r['mac']},{r['suffix']},{r['group_id']}" for r in rows)
    return "\n".join(out) + "\n"
