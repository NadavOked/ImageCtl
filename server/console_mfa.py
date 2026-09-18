"""MFA (TOTP) לקונסולה — #1085.

setup → verify → enable. קוד גיבוי חד-פעמי. disable על ידי admin.
המקומי (is_builtin) אינו יכול להפעיל MFA.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import string
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from . import auth, totp, users
from .api import ServerContext
from .db import _write_lock, get_setting, journal, now_iso, writing

TRUSTED_COOKIE = "imagectl_trusted"
TRUSTED_TTL = 7 * 3600
CHALLENGE_TTL = 5 * 60
BACKUP_COUNT = 8
BACKUP_LEN = 10
BACKUP_ALPHABET = string.ascii_uppercase + string.digits


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_backup(code: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    return f"{salt}${digest}"


def _backup_matches(code: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    guess = hashlib.sha256(f"{salt}:{code.strip().upper()}".encode()).hexdigest()
    return hmac.compare_digest(guess, digest)


def make_challenge(conn: sqlite3.Connection, username: str) -> str:
    raw = secrets.token_urlsafe(32)
    expires = time.time() + CHALLENGE_TTL
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO mfa_challenges (token_hash, username, expires_at)"
            " VALUES (?, ?, ?)",
            (_hash_token(raw), username, expires),
        )
    return raw


def _challenge_user(conn: sqlite3.Connection, raw: str) -> str | None:
    row = conn.execute(
        "SELECT username, expires_at FROM mfa_challenges WHERE token_hash = ?",
        (_hash_token(raw or ""),),
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return None
    return row["username"]


def consume_challenge(conn: sqlite3.Connection, raw: str) -> None:
    with _write_lock, writing(conn):
        conn.execute(
            "DELETE FROM mfa_challenges WHERE token_hash = ?",
            (_hash_token(raw or ""),),
        )


def trusted_ok(conn: sqlite3.Connection, username: str, token: str | None) -> bool:
    if not token:
        return False
    row = conn.execute(
        "SELECT username, expires_at FROM trusted_browsers WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if row is None or row["expires_at"] < time.time():
        return False
    return row["username"].lower() == username.lower()


def remember_browser(conn: sqlite3.Connection, response: Response, username: str,
                     request: Request, tls) -> None:
    raw = secrets.token_urlsafe(32)
    ua = (request.headers.get("user-agent") or "")[:200]
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO trusted_browsers"
            " (token_hash, username, expires_at, created_at, ua)"
            " VALUES (?, ?, ?, ?, ?)",
            (_hash_token(raw), username, time.time() + TRUSTED_TTL, now_iso(), ua),
        )
    response.set_cookie(
        TRUSTED_COOKIE, raw, httponly=True, samesite="lax",
        max_age=TRUSTED_TTL, secure=tls is not None,
    )


def _new_backup_codes(conn: sqlite3.Connection, username: str) -> list[str]:
    codes = [
        "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(BACKUP_LEN))
        for _ in range(BACKUP_COUNT)
    ]
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM mfa_backup_codes WHERE username = ?", (username,))
        conn.executemany(
            "INSERT INTO mfa_backup_codes (username, code_hash) VALUES (?, ?)",
            [(username, _hash_backup(c)) for c in codes],
        )
    return codes


def consume_backup(conn: sqlite3.Connection, username: str, code: str) -> bool:
    rows = conn.execute(
        "SELECT code_hash, used_at FROM mfa_backup_codes WHERE username = ?",
        (username,),
    ).fetchall()
    for row in rows:
        if row["used_at"]:
            continue
        if _backup_matches(code, row["code_hash"]):
            with _write_lock, writing(conn):
                cur = conn.execute(
                    "UPDATE mfa_backup_codes SET used_at = ?"
                    " WHERE username = ? AND code_hash = ? AND used_at IS NULL",
                    (now_iso(), username, row["code_hash"]),
                )
            return cur.rowcount == 1
    return False


def register(router: APIRouter, ctx: ServerContext, current_user, admin_only, tls) -> None:
    """מוסיף את נתיבי ה-MFA לראוטר הקונסולה הקיים — מיזוג קל מול #1073."""

    @router.post("/me/mfa/setup")
    def mfa_setup(user=Depends(current_user)):
        username, role = user
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if role != "admin":
            raise HTTPException(403, "MFA למנהל בלבד")
        secret = totp.new_secret()
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_secret = ?, mfa_enabled = 0,"
                " mfa_enrolled_at = NULL WHERE username = ?",
                (secret, username),
            )
        journal(ctx.conn, "mfa_setup",
                "בלי QR SVG — URI להקלדה ידנית", username)
        return {
            "secret": secret,
            "otpauth_url": totp.otpauth_url(username, secret),
        }

    @router.post("/me/mfa/verify")
    async def mfa_verify(request: Request, user=Depends(current_user)):
        body = await request.json()
        username = user[0]
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if not info["mfa_secret"]:
            raise HTTPException(400, "יש לקרוא ל-setup קודם")
        if totp.verify_totp(info["mfa_secret"], body.get("code", "")) is None:
            raise HTTPException(401, "קוד שגוי, נסה שוב")
        return {"verified": True}

    @router.post("/me/mfa/enable")
    async def mfa_enable(request: Request, response: Response,
                         user=Depends(current_user)):
        body = await request.json()
        username, role = user
        info = users.flags(ctx.conn, username)
        if info["is_builtin"]:
            raise HTTPException(403, "builtin_no_mfa")
        if not info["mfa_secret"]:
            raise HTTPException(400, "יש לקרוא ל-setup קודם")
        step = totp.verify_totp(info["mfa_secret"], body.get("code", ""))
        if step is None:
            raise HTTPException(401, "קוד שגוי, לא ניתן להפעיל MFA")
        codes = _new_backup_codes(ctx.conn, username)
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_enabled = 1, mfa_enrolled_at = ?"
                " WHERE username = ?",
                (now_iso(), username),
            )
        journal(ctx.conn, "mfa_enabled", username, username)
        auth.attach_cookie(response, ctx.conn, username, role, tls)
        return {"mfa_enabled": True, "backup_codes": codes}

    @router.post("/users/{target}/mfa/disable")
    def mfa_disable(target: str, user=Depends(admin_only)):
        actor = user[0]
        if target.strip().lower() == actor.lower():
            raise HTTPException(400, "אי אפשר לנתק MFA לעצמך")
        row = users.lookup(ctx.conn, target)
        if row is None:
            raise HTTPException(404, "משתמש לא קיים")
        if row["is_builtin"]:
            raise HTTPException(400, "לחשבון המקומי אין MFA")
        stored = row["username"]
        with _write_lock, writing(ctx.conn):
            ctx.conn.execute(
                "UPDATE users SET mfa_enabled = 0, mfa_secret = NULL,"
                " mfa_enrolled_at = NULL, mfa_last_step = NULL"
                " WHERE username = ?",
                (stored,),
            )
            ctx.conn.execute(
                "DELETE FROM mfa_backup_codes WHERE username = ?", (stored,),
            )
            ctx.conn.execute(
                "DELETE FROM trusted_browsers WHERE username = ?", (stored,),
            )
        journal(ctx.conn, "mfa_disabled", stored, actor)
        return {"ok": True, "mfa_enabled": False}

    @router.post("/login/mfa")
    async def login_mfa(request: Request, response: Response):
        body = await request.json()
        raw = body.get("challenge") or ""
        code = body.get("code") or ""
        auth.assert_secret(ctx.conn)
        stored = _challenge_user(ctx.conn, raw)
        if stored is None:
            raise HTTPException(401, "אתגר MFA פג או שגוי")
        info = users.flags(ctx.conn, stored)
        if not info["mfa_enabled"] or not info["mfa_secret"]:
            raise HTTPException(401, "MFA אינו מופעל")
        step = totp.verify_totp(info["mfa_secret"], code)
        used_backup = False
        if step is None:
            if consume_backup(ctx.conn, stored, code):
                used_backup = True
            else:
                raise HTTPException(401, "קוד שגוי, נסה שוב")
        elif info["mfa_last_step"] is not None and step == info["mfa_last_step"]:
            raise HTTPException(401, "קוד שגוי, נסה שוב")
        consume_challenge(ctx.conn, raw)
        if step is not None:
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "UPDATE users SET mfa_last_step = ? WHERE username = ?",
                    (step, stored),
                )
        row = users.lookup(ctx.conn, stored)
        auth.attach_cookie(response, ctx.conn, stored, row["role"], tls)
        if body.get("remember_browser"):
            remember_browser(ctx.conn, response, stored, request, tls)
        journal(ctx.conn, "login", "mfa_backup" if used_backup else "mfa", stored)
        idle = int(get_setting(ctx.conn, "console_idle_seconds") or 300)
        return {"username": stored, "role": row["role"], "idle_seconds": idle}

    @router.post("/sessions/revoke")
    async def revoke_sessions(request: Request, user=Depends(admin_only)):
        body = await request.json()
        target = (body.get("username") or "").strip()
        row = users.lookup(ctx.conn, target)
        if row is None:
            raise HTTPException(404, "משתמש לא קיים")
        users.bump_auth_epoch(ctx.conn, row["username"])
        journal(ctx.conn, "sessions_revoked", row["username"], user[0])
        return {"ok": True}
