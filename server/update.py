"""עדכון השרת מול הריפו הציבורי (‏#748).

הכרעות נדב (16/09, תגובה אחרונה ב-#748): המתג **כבוי בברירת מחדל**,
בדיקת-עדכון היא מול הריפו **הציבורי** בלבד (לא הפרטי — שם אין CI),
המנהל בוחר אם לעדכן פר-עדכון, והחיבור היוצא נפתח **רק** בזמן הבדיקה/
העדכון עצמם — לא חיבור קבוע.

מקור הגרסה החדשה: `tag` אחרון ב-remote (‏`git ls-remote --tags`), לא
release API — הריפו הציבורי עדיין בלי releases. גרסה נוכחית: `git
describe --tags` על עץ השרת (`--repo-dir`, ברירת מחדל תיקיית הקוד עצמה).

כמו ב-`ssh_switch.py`: ההגדרה (`update_enabled`) קובעת רק מה *מותר
לנסות*; המצב בפועל (הגרסה שרצה) נקרא תמיד מחדש, לא מונח.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from .db import get_setting, journal, set_setting

Hooks = dict[str, Callable]

#: מפתח המתג ב-``WRITE_SETTINGS`` (console_api.py) — כבוי כברירת מחדל,
#: כמו כל דגל אחר שם: ``get_setting`` שמחזיר ``None`` נקרא "כבוי".
ENABLED_KEY = "update_enabled"
#: התג הקודם, נשמר לפני כל ``apply`` — כדי ש"חזור לגרסה הקודמת" לא
#: יצטרך לזכור אותו בעצמו.
PREVIOUS_KEY = "update_previous"
#: מצב הרצת העדכון האחרון — נשרד את ה-restart כי הוא ב-DB, לא בזיכרון.
STATUS_KEY = "update_status"

#: הריפו הציבורי — הוא שער ה-CI (CLAUDE.md, "ארבעת הריפואים"). הבדיקה
#: אף פעם אינה מול הפרטי: שם `tests` לא רץ ואין ראיה שהתג ירוק.
PUBLIC_UPDATE_URL = "https://github.com/NadavOked/ImageCtl.git"

_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_REMOTE_TAG_RE = re.compile(r"refs/tags/(v\d+\.\d+\.\d+)(\^\{\})?$")


def semver_key(tag: str) -> tuple[int, int, int] | None:
    m = _TAG_RE.match(tag)
    return tuple(int(g) for g in m.groups()) if m else None


def _base_version(describe_output: str | None) -> str | None:
    """‏`git describe --tags` מחזיר לפעמים `v0.24.0-3-gabc123` (עצי עבודה
    שאינם *בדיוק* על תג) — משווים לפי התג הבסיסי, לא למחרוזת כולה."""
    if not describe_output:
        return None
    m = re.match(r"^(v\d+\.\d+\.\d+)", describe_output.strip())
    return m.group(1) if m else None


def parse_remote_tags(ls_remote_output: str) -> list[str]:
    """‏`vX.Y.Z` בלבד, ממוינים מהישן לחדש. שורות `^{}` (תג מוער שהוצמד)
    ושורות שאינן תואמות תבנית semver נבדלות — לא כל שורה ב-ls-remote
    היא תג-מוצר."""
    names = set()
    for line in ls_remote_output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        m = _REMOTE_TAG_RE.search(parts[1])
        if m:
            names.add(m.group(1))
    return sorted(names, key=semver_key)


# --- הרצת פקודות בפועל — כולן מוזרקות, כדי שאף בדיקה לא תיגע ברשת/git -------


def _run(cmd: list[str], timeout: int, cwd: str | Path | None = None) -> tuple[bool, str, str]:
    """‏(הרצה עצמה הצליחה, stdout, stderr). קוד יציאה שאינו 0, וגם חריגה
    בהרצה עצמה, שניהם "לא הצלחנו" — לא "אין תוצאה" (עיקרון 5)."""
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=False, cwd=str(cwd) if cwd else None,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)
    if done.returncode != 0:
        return False, done.stdout, (done.stderr or "").strip()[:300]
    return True, done.stdout, ""


def _describe(repo_dir: str | Path) -> str | None:
    ok, out, _err = _run(["git", "-C", str(repo_dir), "describe", "--tags"], timeout=10)
    return out.strip() if ok and out.strip() else None


def _ls_remote_tags(url: str) -> tuple[bool, str, str]:
    # חד-פעמי, 20 שניות: זה החיבור היוצא היחיד שהבדיקה פותחת (סעיף 4
    # בתגובת נדב) — אין polling ואין חיבור קבוע לציבורי.
    return _run(["git", "ls-remote", "--tags", url], timeout=20)


def _fetch_tags(repo_dir: str | Path) -> tuple[bool, str]:
    ok, _out, err = _run(["git", "fetch", "--tags"], timeout=60, cwd=repo_dir)
    return ok, err


def _checkout(repo_dir: str | Path, tag: str) -> tuple[bool, str]:
    ok, _out, err = _run(["git", "checkout", "--detach", tag], timeout=30, cwd=repo_dir)
    return ok, err


def _run_upgrade_script(repo_dir: str | Path, tag: str) -> tuple[bool, str]:
    """מפעיל את `tools/server-upgrade.sh` **מנותק** מתהליך השרת, דרך
    `systemd-run` — כי השלב האחרון שלו הוא `systemctl restart
    imagectl-server`, שמנהל התהליך הזה. תהליך-ילד היה נהרג יחד איתו."""
    script = Path(repo_dir) / "tools" / "server-upgrade.sh"
    ok, _out, err = _run(
        ["systemd-run", "--unit=imagectl-upgrade", "--collect",
         "bash", str(script), tag, str(repo_dir)],
        timeout=15,
    )
    return ok, err


def default_hooks() -> Hooks:
    return {
        "describe": _describe,
        "ls_remote_tags": _ls_remote_tags,
        "fetch_tags": _fetch_tags,
        "checkout": _checkout,
        "run_upgrade": _run_upgrade_script,
    }


# --- שכבת המידע --------------------------------------------------------------


def enabled(conn) -> bool:
    try:
        return get_setting(conn, ENABLED_KEY) == "true"
    except Exception:                                  # noqa: BLE001 — כשל סוגר
        return False


def current_version(hooks: Hooks, repo_dir: str | Path) -> str | None:
    try:
        return hooks["describe"](repo_dir)
    except Exception:                                  # noqa: BLE001
        return None


def check_update(hooks: Hooks, repo_dir: str | Path, public_url: str) -> dict:
    """בדיקה חד-פעמית מול הריפו הציבורי. אינה כותבת דבר — ``apply`` בלבד
    נוגע בעץ. הראיה חיובית: ``available`` נגזר משוואת גרסאות, לא מ"אין
    שגיאה"."""
    current = current_version(hooks, repo_dir)
    try:
        ok, out, err = hooks["ls_remote_tags"](public_url)
    except Exception as exc:                            # noqa: BLE001
        ok, out, err = False, "", str(exc)
    if not ok:
        return {"current": current, "latest": None, "available": False,
                "reason": f"הבדיקה מול {public_url} נכשלה: {err}"}
    tags = parse_remote_tags(out)
    if not tags:
        return {"current": current, "latest": None, "available": False,
                "reason": "אין גרסאות (tags) בריפו הציבורי"}
    latest = tags[-1]
    base = _base_version(current)
    available = base is None or (semver_key(latest) or ()) > (semver_key(base) or ())
    return {"current": current, "latest": latest, "available": available, "reason": None}


def _set_status(conn, doc: dict) -> None:
    try:
        set_setting(conn, STATUS_KEY, json.dumps(doc))
    except Exception:                                    # noqa: BLE001 — לוג בלבד
        pass


def get_status(conn) -> dict | None:
    try:
        raw = get_setting(conn, STATUS_KEY)
    except Exception:                                    # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def status_with_verification(conn, hooks: Hooks, repo_dir: str | Path) -> dict:
    """מצב ההרצה האחרונה + הראיה החיובית: האם הגרסה שרצה בפועל, עכשיו,
    היא התג שביקשנו. "לא ידוע עדיין" (השרת עוד לא עלה מחדש) שונה מפורשות
    מ"נכשל" — עיקרון 5, אותה משפחה כמו ``ssh_switch.Listeners``."""
    doc = get_status(conn) or {"state": "idle"}
    current = current_version(hooks, repo_dir)
    result = {**doc, "current": current}
    if doc.get("state") == "applying" and doc.get("tag"):
        if current == doc["tag"]:
            result["verified"] = True
            result["state"] = "done"
        else:
            result["verified"] = False
    return result


def _apply_in_background(conn, hooks: Hooks, repo_dir: str | Path, tag: str,
                         user: str) -> None:
    _set_status(conn, {"state": "fetching", "tag": tag})
    ok, err = hooks["fetch_tags"](repo_dir)
    if not ok:
        _set_status(conn, {"state": "failed", "tag": tag,
                           "error": f"git fetch --tags נכשל: {err}"})
        journal(conn, "update_apply_failed", f"{tag}: fetch — {err}", user)
        return

    previous = current_version(hooks, repo_dir)
    if previous:
        try:
            set_setting(conn, PREVIOUS_KEY, previous)
        except Exception:                                # noqa: BLE001
            pass

    ok, err = hooks["checkout"](repo_dir, tag)
    if not ok:
        _set_status(conn, {"state": "failed", "tag": tag,
                           "error": f"git checkout --detach {tag} נכשל: {err}"})
        journal(conn, "update_apply_failed", f"{tag}: checkout — {err}", user)
        return

    ok, err = hooks["run_upgrade"](repo_dir, tag)
    if not ok:
        _set_status(conn, {"state": "failed", "tag": tag,
                           "error": f"הפעלת tools/server-upgrade.sh נכשלה: {err}"})
        journal(conn, "update_apply_failed", f"{tag}: script — {err}", user)
        return

    # ‏#748 סעיף 5: "אומת" נקבע רק ב-``status_with_verification`` אחרי
    # שהשרת עלה מחדש עם התג — לא כאן. כאן רק "הופעל".
    _set_status(conn, {"state": "applying", "tag": tag})
    journal(conn, "update_apply_started", tag, user)


def start_apply(ctx, hooks: Hooks, repo_dir: str | Path, tag: str, user: str) -> None:
    """‏fetch+checkout ולשיגור הסקריפט המנותק (שניות ספורות, לא הרסטארט
    עצמו — זה קורה בתוך `tools/server-upgrade.sh`, אחרי שהתשובה כבר
    חזרה). ‏FastAPI מריץ נתיבי `def` סינכרוניים בתוך thread pool משלו,
    כך שזה כבר לא חוסם את event loop; אין צורך ב-``threading.Thread``
    נוסף, וההרצה הסינכרונית הזו היא גם מה שהופך את הבדיקות לדטרמיניסטיות
    (בלי race על ``calls``/``status``)."""
    _apply_in_background(ctx.conn, hooks, repo_dir, tag, user)


def _active_round(ctx) -> bool:
    """סבב פתוח/רץ — לא הזמן להחליף עץ מתחת לרגליו של שידור. אותה שאילתה
    שמזהה 'יש סבב חי' בכל שאר הקונסולה (sessions.py)."""
    try:
        row = ctx.conn.execute(
            "SELECT 1 FROM sessions WHERE state IN ('open', 'running') LIMIT 1"
        ).fetchone()
    except Exception:                                    # noqa: BLE001 — לא ידוע = לא לחסום בשקט
        return False
    return row is not None


def create_update_router(ctx, repo_dir: str | Path, server_base: str,
                         hooks: Hooks | None = None,
                         public_url: str = PUBLIC_UPDATE_URL) -> APIRouter:
    from . import auth

    router = APIRouter(prefix="/api/console/update")
    current_user, admin_only = auth.dependencies(ctx.conn)
    del current_user
    hooks = {**default_hooks(), **(hooks or {})}

    def _server_name() -> str:
        from urllib.parse import urlsplit
        return urlsplit(server_base).hostname or server_base

    @router.get("")
    def info(user=Depends(admin_only)):
        del user
        return {
            "current": current_version(hooks, repo_dir),
            "enabled": enabled(ctx.conn),
            "previous": get_setting(ctx.conn, PREVIOUS_KEY),
            "server_name": _server_name(),
        }

    @router.get("/status")
    def status(user=Depends(admin_only)):
        del user
        return status_with_verification(ctx.conn, hooks, repo_dir)

    @router.post("/check")
    def check(user=Depends(admin_only)):
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        result = check_update(hooks, repo_dir, public_url)
        journal(ctx.conn, "update_check",
               f"current={result['current']} latest={result['latest']}", user[0])
        return result

    def _apply_or_revert(body: dict, user, tag: str) -> dict:
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        # פעולה הרסנית מאחורי הקלדת שם — עיקרון 7, אותו דפוס כמו מחיקת
        # אימג'/עצירת סבב (console_api.py): מקלידים את שם השרת שכבר מוצג.
        if typed != _server_name():
            raise HTTPException(403, "השם שהוקלד אינו תואם את שם השרת")
        if _active_round(ctx):
            raise HTTPException(409, "יש סבב פתוח/רץ — לא ניתן לעדכן עכשיו")
        start_apply(ctx, hooks, repo_dir, tag, user[0])
        return {"ok": True, "started": True, "tag": tag}

    # ‏def רגיל (לא async): FastAPI מריץ אותו ב-thread pool משלו — בדיוק
    # כמו ``check`` למעלה — כדי ש-``git fetch --tags`` (עד 60 שניות,
    # רשת אמיתית) לא יחסום את ה-event loop היחיד שמשרת גם /health וגם
    # כל בקשה אחרת בו-זמנית. זו גם הסיבה שהבדיקות דטרמיניסטיות בלי
    # ‏thread נוסף מהצד שלנו: תגובת ה-HTTP ממילא ממתינה לסיום.
    @router.post("/apply")
    def apply(body: dict = Body(default={}), user=Depends(admin_only)):
        tag = body.get("tag", "") if isinstance(body, dict) else ""
        if not semver_key(tag):
            raise HTTPException(400, f"תג לא תקין: {tag!r}")
        return _apply_or_revert(body, user, tag)

    @router.post("/revert")
    def revert(body: dict = Body(default={}), user=Depends(admin_only)):
        previous = get_setting(ctx.conn, PREVIOUS_KEY)
        if not previous:
            raise HTTPException(404, "אין גרסה קודמת לחזור אליה")
        return _apply_or_revert(body, user, previous)

    return router
