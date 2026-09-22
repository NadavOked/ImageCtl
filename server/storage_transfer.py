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

import hashlib
import json
import logging
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

from . import interserver_auth, storage_client, storage_nodes
from .db import _write_lock, journal, now_iso, writing

log = logging.getLogger("imagectl.storage_transfer")

STATES = ("queued", "sending", "verifying", "done", "failed")
ACTIVE_STATES = ("queued", "sending", "verifying")
#: כמה בייטים בין עדכוני התקדמות ל-DB — לא כל מנה של 1MB היא כתיבה.
PROGRESS_STEP = 64 * 1024 * 1024
#: כמה העברות מוחזרות לתצוגה.
LIST_LIMIT = 20
#: תיקיית הביניים של pull — אזור עבודה (מתחיל בנקודה) שהספרייה מדלגת עליו.
INCOMING = ".incoming"

_cancel_flags: dict[str, threading.Event] = {}
_cancel_lock = threading.Lock()


class Cancelled(Exception):
    """המפעיל ביטל את ה-pull."""


class TransferError(ValueError):
    """בקשת העברה שנדחתה לפני שהתחילה — עם קוד HTTP שה-route מתרגם."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _note_contact_failure(conn, node_id: str, exc: BaseException, detail: str) -> None:
    """‏#1017: כשל **בערוץ** (חיבור/TLS/תשובה שאינה 200) נרשם על השורה של
    המשני — "לא ענה — HH:MM" בטבלת הסניפים. כשל לוגי של ההעברה ("האימג'
    כבר קיים", sha256) אינו כשל חיבור, והמשני שענה עליו כבר נרשם כ"ענה"."""
    if isinstance(exc, (storage_client.InterserverClientError, OSError)):
        storage_nodes.record_contact(conn, node_id, error=detail)


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
        " t.created_at, t.updated_at, t.direction"
        f" FROM storage_transfers t JOIN storage_nodes n ON n.id = t.node_id {where}"
        " ORDER BY t.created_at DESC, t.id LIMIT ?", params).fetchall()
    return [dict(r) for r in rows]


def _set_state(conn, transfer_id: str, state: str, *, bytes_sent: int | None = None,
               bytes_total: int | None = None, error: str | None = None) -> None:
    assert state in STATES
    sets = ["state = ?", "updated_at = ?"]
    params: list = [state, now_iso()]
    if bytes_sent is not None:
        sets.append("bytes_sent = ?")
        params.append(bytes_sent)
    if bytes_total is not None:
        sets.append("bytes_total = ?")
        params.append(bytes_total)
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
        _note_contact_failure(conn, row["node_id"], exc, detail)
        log.warning("transfer %s failed: %s", transfer_id, detail)
        # היומן לפני המצב הסופי, בכל אתר: המצב הוא מה שהקונסולה (והטסטים)
        # ממתינים לו, ומי שרואה ``failed``/``done`` חייב לראות גם את
        # האירוע — בסדר ההפוך יש חלון שבו המצב סופי והיומן עוד ריק.
        journal(conn, "storage_transfer_failed",
                f"{transfer_id} {row['image_id']}: {detail}", "")
        _set_state(conn, transfer_id, "failed", error=detail)


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
        storage_nodes.record_contact(conn, node["id"], error=None)   # #1017: ענה
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
    journal(conn, "storage_transfer_done",
            f'{transfer_id} {image_id} "{row["image_name"]}" -> {node["label"]}', "")
    _set_state(conn, transfer_id, "done", bytes_sent=row["bytes_total"])


def _cancel_flag(transfer_id: str) -> threading.Event:
    with _cancel_lock:
        return _cancel_flags.setdefault(transfer_id, threading.Event())


def _is_cancelled(transfer_id: str) -> bool:
    flag = _cancel_flags.get(transfer_id)
    return flag is not None and flag.is_set()


def _clear_incoming(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        log.warning("pull incoming was left behind at %s", path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cancel_transfer(conn, transfer_id: str, user: tuple[str, str]) -> dict:
    """מסמן pull פעיל לביטול. ה-worker מוחק את ``.incoming`` ומסיים failed."""
    storage_nodes.assert_can_manage_nodes(conn, user)
    row = conn.execute(
        "SELECT id, state, direction FROM storage_transfers WHERE id = ?",
        (transfer_id,)).fetchone()
    if row is None:
        raise TransferError(404, "העברה לא קיימת")
    if row["direction"] != "pull":
        raise TransferError(409, "ביטול נתמך רק בהעברה מהמשני")
    if row["state"] not in ACTIVE_STATES:
        raise TransferError(409, "ההעברה אינה פעילה")
    _cancel_flag(transfer_id).set()
    return {"ok": True}


def start_pull(ctx, data_dir, node_id: str, image_id: str,
               user: tuple[str, str], *, run_in_thread: bool = True) -> str:
    """מאמת (משני קיים ופעיל, האימג' אינו בראשי, אין העברה פעילה לאותו זוג),
    רושם שורה ``queued`` עם ``direction=pull`` ומפעיל את המשיכה ברקע."""
    from .images import valid_image_id
    conn = ctx.conn
    storage_nodes.assert_can_manage_nodes(conn, user)
    node = _node(conn, node_id)
    if node is None:
        raise TransferError(404, "שרת משני לא קיים")
    if node["disabled_at"]:
        raise TransferError(409, "השרת המשני מושבת")
    if not valid_image_id(image_id):
        raise TransferError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    if ctx.library.get(image_id) is not None:
        raise TransferError(409, f"האימג' {image_id} כבר קיים בספריית הראשי")
    if data_dir is None:
        raise TransferError(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
    transfer_id = uuid.uuid4().hex
    with _write_lock, writing(conn):
        active = conn.execute(
            "SELECT 1 FROM storage_transfers WHERE node_id = ? AND image_id = ?"
            " AND state IN ('queued', 'sending', 'verifying')",
            (node_id, image_id)).fetchone()
        if active is not None:
            raise TransferError(409, "כבר רצה העברה של האימג' הזה עם המשני הזה")
        stamp = now_iso()
        conn.execute(
            "INSERT INTO storage_transfers (id, node_id, image_id, image_name,"
            " state, bytes_sent, bytes_total, error, started_by, created_at,"
            " updated_at, direction) VALUES (?, ?, ?, ?, 'queued', 0, 0, NULL,"
            " ?, ?, ?, 'pull')",
            (transfer_id, node_id, image_id, image_id, user[0], stamp, stamp))
    journal(conn, "storage_transfer_start",
            f'{transfer_id} {image_id} <- {node["label"]}', user[0])
    if run_in_thread:
        threading.Thread(target=run_pull, args=(ctx, data_dir, transfer_id),
                         name=f"pull-{transfer_id[:8]}", daemon=True).start()
    return transfer_id


def run_pull(ctx, data_dir, transfer_id: str) -> None:
    """גוף ה-pull — תהליכון עם חיבור DB משלו. כשל/ביטול = ``failed`` גלוי."""
    from .images import inside, valid_image_id
    conn = ctx.conn
    row = conn.execute(
        "SELECT node_id, image_id, image_name, bytes_total FROM storage_transfers"
        " WHERE id = ?", (transfer_id,)).fetchone()
    if row is None:
        return
    incoming = None
    image_id = row["image_id"]
    if valid_image_id(image_id):
        incoming = inside(Path(ctx.library.root) / INCOMING / image_id,
                          Path(ctx.library.root))
    try:
        _pull(ctx, data_dir, transfer_id, row, incoming)
    except Cancelled:
        if incoming is not None:
            _clear_incoming(incoming)
        log.info("pull %s cancelled", transfer_id)
        journal(conn, "storage_transfer_failed",
                f"{transfer_id} {image_id}: בוטל", "")
        _set_state(conn, transfer_id, "failed", error="בוטל")
    except Exception as exc:                                     # noqa: BLE001
        if _is_cancelled(transfer_id):
            if incoming is not None:
                _clear_incoming(incoming)
            journal(conn, "storage_transfer_failed",
                    f"{transfer_id} {image_id}: בוטל", "")
            _set_state(conn, transfer_id, "failed", error="בוטל")
        else:
            detail = interserver_auth.redact_secrets(str(exc))
            _note_contact_failure(conn, row["node_id"], exc, detail)
            log.warning("pull %s failed: %s", transfer_id, detail)
            journal(conn, "storage_transfer_failed",
                    f"{transfer_id} {image_id}: {detail}", "")
            _set_state(conn, transfer_id, "failed", error=detail)
    finally:
        with _cancel_lock:
            _cancel_flags.pop(transfer_id, None)


def _pull(ctx, data_dir, transfer_id: str, row, incoming: Path | None) -> None:
    from .images import (inside, streamed_partitions, validate_display_name,
                         valid_image_id)
    conn = ctx.conn
    node = _node(conn, row["node_id"])
    if node is None:
        raise RuntimeError("השרת המשני נמחק לפני שההעברה התחילה")
    image_id = row["image_id"]
    if not valid_image_id(image_id):
        raise RuntimeError(f"מזהה אימג' לא תקין: {image_id!r}")
    if ctx.library.get(image_id) is not None:
        raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית הראשי")
    root = Path(ctx.library.root)
    if incoming is None:
        raise RuntimeError("תיקיית הביניים יוצאת משורש הספרייה")
    last = {"mark": 0}

    def on_progress(sent: int) -> None:
        if _is_cancelled(transfer_id):
            raise Cancelled()
        if sent - last["mark"] >= PROGRESS_STEP:
            last["mark"] = sent
            _set_state(conn, transfer_id, "sending", bytes_sent=sent)

    client, token = storage_nodes.node_client(conn, data_dir, node)
    with client:
        listing = client.get_json("/images", token)
        storage_nodes.record_contact(conn, node["id"], error=None)   # #1017: ענה
        if not isinstance(listing, list):
            raise RuntimeError("תשובה לא צפויה מהמשני לרשימת אימג'ים")
        item = next((x for x in listing if isinstance(x, dict)
                     and x.get("id") == image_id), None)
        if item is None:
            raise RuntimeError(f"האימג' {image_id} אינו בספריית המשני")
        total = int(item.get("size_bytes") or 0)
        name = str(item.get("name") or image_id)
        with _write_lock, writing(conn):
            conn.execute(
                "UPDATE storage_transfers SET image_name = ?, bytes_total = ?,"
                " updated_at = ? WHERE id = ?",
                (name, total, now_iso(), transfer_id))
        if _is_cancelled(transfer_id):
            raise Cancelled()
        _set_state(conn, transfer_id, "sending", bytes_sent=0, bytes_total=total)
        manifest = client.get_json(f"/images/{image_id}/manifest", token)
        if not isinstance(manifest, dict) or manifest.get("id") != image_id:
            raise RuntimeError("מניפסט לא צפוי מהמשני")
        validate_display_name(str(manifest.get("name") or ""), "שם האימג'")
        if manifest.get("folder"):
            validate_display_name(str(manifest["folder"]), "שם התיקייה")
        files = [p["file"] for p in streamed_partitions(manifest)]
        incoming.mkdir(parents=True, exist_ok=True)
        completed = 0
        for filename in files:
            if _is_cancelled(transfer_id):
                raise Cancelled()
            dest = incoming / filename
            def _file_progress(n, _c=completed):
                on_progress(_c + n)
            client.get_stream(
                f"/images/{image_id}/files/{quote(filename, safe='')}",
                token, dest, on_progress=_file_progress)
            completed += dest.stat().st_size
        _set_state(conn, transfer_id, "verifying", bytes_sent=completed)
        try:
            for part in streamed_partitions(manifest):
                filename = part["file"]
                path = incoming / filename
                if not path.is_file():
                    raise RuntimeError(f"חסר קובץ מחיצה אחרי ההורדה: {filename}")
                digest = _sha256_file(path)
                if digest != part["sha256"]:
                    raise RuntimeError(
                        f"אימות נכשל: {filename} אינו תואם ל-sha256")
        except RuntimeError:
            _clear_incoming(incoming)
            raise
        public = {k: v for k, v in manifest.items() if not str(k).startswith("_")}
        with (incoming / "manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(public, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if ctx.library.get(image_id) is not None:
            _clear_incoming(incoming)
            raise RuntimeError(f"האימג' {image_id} כבר קיים בספריית הראשי")
        target = inside(root / image_id, root)
        if target is None:
            _clear_incoming(incoming)
            raise RuntimeError("יעד האימג' יוצא משורש הספרייה")
        if target.exists():
            _clear_incoming(incoming)
            raise RuntimeError(f"התיקייה {image_id} כבר קיימת")
        try:
            incoming.rename(target)
        except OSError as extra:
            _clear_incoming(incoming)
            raise RuntimeError(f"לא ניתן להכניס את האימג' לספרייה: {extra}") from extra
    journal(conn, "storage_transfer_done",
            f'{transfer_id} {image_id} "{name}" <- {node["label"]}', "")
    _set_state(conn, transfer_id, "done", bytes_sent=completed)
