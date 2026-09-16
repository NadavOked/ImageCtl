"""Storage Nodes — העברת אימג' מהראשי למשני (#655 v1, הכרעת נדב 16/09).

הראשי **דוחף**: משימת רקע קוראת את תיקיית האימג' מהספרייה שעל הדיסק
(``archive.tar_stream`` — אותו tar של "הורדה למחשב"), ומזרימה אותה למשני
דרך הערוץ הבין-שרתי המאומת (mTLS 1.3, SPKI-pin, טוקן כרוך לתעודה —
‏#740). המשני מכניס דרך ``import_tar`` — ‏sha256 של כל מחיצה מול המניפסט
**לפני** שהאימג' נכנס לספרייה (עיקרון 6); הראשי לא מקבל "הצלחה" אלא
מתשובת 200 שאחרי האימות.

מה נשמר ב-DB (``storage_transfers``) הוא **תיאום זמני**: מצב, בייטים
שנשלחו/סה"כ, ושגיאה. לא מלאי — "האם למשני יש X" נשאל מהדיסק של המשני.

חידוש אחרי ניתוק: v1 = **מתחיל מחדש בגלוי**. ניתוק באמצע מסתיים ב-
``failed`` עם "נותק אחרי N מתוך M בייטים", והמפעיל מפעיל שוב; המשני
מחק את מה שהתקבל (``import_tar`` לעולם לא משאיר תיקייה חלקית). לא
"נתקע" ולא "ממשיך" מאמצע.

חד-כיווני: הפונקציות כאן רצות רק על standalone (``assert_can_manage_nodes``),
ומשני אינו דוחף מעלה.
"""

from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path

from . import interserver_auth, storage_nodes
from .db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagectl.storage_transfer")

STATES = ("queued", "sending", "verifying", "done", "failed")
ACTIVE_STATES = ("queued", "sending", "verifying")
#: כמה בייטים בין עדכוני התקדמות ל-DB — לא כל מנה של 1MB היא כתיבה.
PROGRESS_STEP = 64 * 1024 * 1024
#: כמה העברות מוחזרות לתצוגה.
LIST_LIMIT = 20


class TransferError(ValueError):
    """בקשת העברה שנדחתה לפני שהתחילה — עם קוד HTTP שה-route מתרגם."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _node(conn, node_id: str):
    return conn.execute(
        "SELECT id, label, base_url, pinned_spki, credential_ref, disabled_at"
        " FROM storage_nodes WHERE id = ?", (node_id,)).fetchone()


def list_transfers(conn, user: tuple[str, str], *, node_id: str | None = None,
                   limit: int = LIST_LIMIT) -> list[dict]:
    """ההעברות האחרונות (חדשה ראשונה), לכל המשניים או לאחד."""
    storage_nodes.assert_can_manage_nodes(conn, user)
    where = "WHERE t.node_id = ?" if node_id else ""
    params: tuple = (node_id, limit) if node_id else (limit,)
    rows = conn.execute(
        "SELECT t.id, t.node_id, n.label AS node_label, t.image_id, t.image_name,"
        " t.state, t.bytes_sent, t.bytes_total, t.error, t.started_by,"
        " t.created_at, t.updated_at"
        f" FROM storage_transfers t JOIN storage_nodes n ON n.id = t.node_id {where}"
        " ORDER BY t.created_at DESC, t.id LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def _set_state(conn, transfer_id: str, state: str, *, bytes_sent: int | None = None,
               error: str | None = None) -> None:
    assert state in STATES
    sets = ["state = ?", "updated_at = ?"]
    params: list = [state, now_iso()]
    if bytes_sent is not None:
        sets.append("bytes_sent = ?")
        params.append(bytes_sent)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    params.append(transfer_id)
    with _write_lock, writing(conn):
        conn.execute(f"UPDATE storage_transfers SET {', '.join(sets)} WHERE id = ?",
                     params)


def start_transfer(ctx, data_dir, node_id: str, image_id: str,
                   user: tuple[str, str], *, run_in_thread: bool = True) -> str:
    """מאמת (משני קיים ופעיל, אימג' קיים, אין העברה פעילה לאותו זוג), רושם
    שורה ``queued`` ומפעיל את הדחיפה ברקע. מחזיר את מזהה ההעברה."""
    conn = ctx.conn
    storage_nodes.assert_can_manage_nodes(conn, user)
    node = _node(conn, node_id)
    if node is None:
        raise TransferError(404, "שרת משני לא קיים")
    if node["disabled_at"]:
        raise TransferError(409, "השרת המשני מושבת")
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise TransferError(404, "אימג' לא קיים")
    if data_dir is None:
        raise TransferError(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
    from .archive import tar_size
    total = tar_size(Path(manifest["_dir"]))
    transfer_id = uuid.uuid4().hex
    with _write_lock, writing(conn):
        active = conn.execute(
            "SELECT 1 FROM storage_transfers WHERE node_id = ? AND image_id = ?"
            " AND state IN ('queued', 'sending', 'verifying')",
            (node_id, image_id)).fetchone()
        if active is not None:
            raise TransferError(409, "כבר רצה העברה של האימג' הזה למשני הזה")
        stamp = now_iso()
        conn.execute(
            "INSERT INTO storage_transfers (id, node_id, image_id, image_name,"
            " state, bytes_sent, bytes_total, error, started_by, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, 'queued', 0, ?, NULL, ?, ?, ?)",
            (transfer_id, node_id, image_id, manifest["name"], total, user[0],
             stamp, stamp))
    journal(conn, "storage_transfer_start",
            f'{transfer_id} {image_id} "{manifest["name"]}" -> {node["label"]}',
            user[0])
    if run_in_thread:
        threading.Thread(target=run_transfer, args=(ctx, data_dir, transfer_id),
                         name=f"transfer-{transfer_id[:8]}", daemon=True).start()
    return transfer_id


def run_transfer(ctx, data_dir, transfer_id: str) -> None:
    """גוף המשימה — רץ בתהליכון משלו (חיבור DB משלו, ``db.Database``).
    כל חריגה מסתיימת ב-``failed`` עם סיבה מוסתרת-סודות; אין "נתקע"."""
    conn = ctx.conn
    row = conn.execute(
        "SELECT node_id, image_id, image_name, bytes_total FROM storage_transfers"
        " WHERE id = ?", (transfer_id,)).fetchone()
    if row is None:
        return
    try:
        _push(ctx, data_dir, transfer_id, row)
    except Exception as exc:                                     # noqa: BLE001
        detail = interserver_auth.redact_secrets(str(exc))
        log.warning("transfer %s failed: %s", transfer_id, detail)
        _set_state(conn, transfer_id, "failed", error=detail)
        journal(conn, "storage_transfer_failed",
                f"{transfer_id} {row['image_id']}: {detail}", "")


def _push(ctx, data_dir, transfer_id: str, row) -> None:
    from .archive import tar_stream
    conn = ctx.conn
    node = _node(conn, row["node_id"])
    if node is None:
        raise RuntimeError("השרת המשני נמחק לפני שההעברה התחילה")
    manifest = ctx.library.get(row["image_id"])
    if manifest is None:
        raise RuntimeError("האימג' נמחק לפני שההעברה התחילה")
    client, token = storage_nodes.node_client(conn, data_dir, node)
    image_id = row["image_id"]
    last = {"mark": 0}

    def on_progress(sent: int) -> None:
        if sent - last["mark"] >= PROGRESS_STEP:
            last["mark"] = sent
            _set_state(conn, transfer_id, "sending", bytes_sent=sent)

    _set_state(conn, transfer_id, "sending", bytes_sent=0)
    with client:
        present = client.get_json(f"/images/{image_id}", token)
        if present.get("present") is True:
            raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית המשני")

        def chunks():
            for chunk in tar_stream(Path(manifest["_dir"]), image_id):
                yield chunk
            # כל הבייטים יצאו; מכאן המשני מאמת sha256 — וזה יכול לארוך דקות.
            _set_state(conn, transfer_id, "verifying", bytes_sent=row["bytes_total"])

        result = client.put_stream(f"/images/{image_id}", token, chunks(),
                                   row["bytes_total"], on_progress=on_progress)
    if result.get("ok") is not True or result.get("id") != image_id:
        raise RuntimeError(f"תשובה לא צפויה מהמשני: {result!r}")
    _set_state(conn, transfer_id, "done", bytes_sent=row["bytes_total"])
    journal(conn, "storage_transfer_done",
            f'{transfer_id} {image_id} "{row["image_name"]}" -> {node["label"]}', "")
