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
import logging
import re
import subprocess
from pathlib import Path
from typing import Callable, NamedTuple

from fastapi import APIRouter, Body, Depends, HTTPException

from .db import get_setting, journal, now_iso, set_setting

log = logging.getLogger("imagectl.update")

Hooks = dict[str, Callable]

#: מפתח המתג ב-``WRITE_SETTINGS`` (console_api.py) — כבוי כברירת מחדל,
#: כמו כל דגל אחר שם: ``get_setting`` שמחזיר ``None`` נקרא "כבוי".
ENABLED_KEY = "update_enabled"
#: התג הקודם, נשמר לפני כל ``apply`` — כדי ש"חזור לגרסה הקודמת" לא
#: יצטרך לזכור אותו בעצמו.
PREVIOUS_KEY = "update_previous"
#: מצב הרצת העדכון האחרון — נשרד את ה-restart כי הוא ב-DB, לא בזיכרון.
STATUS_KEY = "update_status"
#: ‏#1000: תוצאת בדיקת-העדכון האחרונה ``{at, current, latest, available,
#: reason}`` — נכתבת ב-``POST /update/check`` ומוחזרת ב-``GET /update`` כ-
#: ``last_check``, כדי שהדף יציג "בדיקה אחרונה: <זמן השרת> — <תוצאה>" גם
#: אחרי רענון, ולא "לא נבדק בסשן הזה" משעון הדפדפן.
LAST_CHECK_KEY = "update_last_check"

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
    except FileNotFoundError:
        # ‏#1185: "[Errno 2] No such file or directory: 'git'" הוא הניחוש של
        # המכונה על עצמה — למנהל זה אומר כלום. הכלי חסר, בשמו.
        return False, "", f"{cmd[0]} {_NOT_INSTALLED} — הותקן מ-ISO ישן (#1185)? התקנה מחדש מ-ISO עדכני"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", str(exc)
    if done.returncode != 0:
        return False, done.stdout, (done.stderr or "").strip()[:300]
    return True, done.stdout, ""


_NOT_INSTALLED = "אינו מותקן בשרת הזה"

#: ‏#1159: הקטגוריות של ``version_error`` ב-``/health/live``. ה-endpoint הזה
#: אינו דורש הזדהות, ולכן הוא מקבל **רק** את אחת המחרוזות הקבועות האלה —
#: בלי נתיבים ובלי stderr. הסיבה המלאה הולכת ליומן ול-``GET /update``.
VERSION_ERRORS = ("git-missing", "not-a-git-tree", "dubious-ownership",
                  "no-tag", "git-failed", "not-wired")


class VersionError(RuntimeError):
    """כישלון קריאת הגרסה: ``category`` מתוך ``VERSION_ERRORS``, והסיבה
    המלאה (עם נתיב ו-stderr) ב-``str(exc)``."""

    def __init__(self, category: str, detail: str):
        super().__init__(detail)
        self.category = category


def _describe_error(err: str) -> str:
    if _NOT_INSTALLED in err:
        return "git-missing"
    low = err.lower()
    if "not a git repository" in low:
        return "not-a-git-tree"
    if "dubious ownership" in low:
        return "dubious-ownership"
    if "no names found" in low or "no tags can describe" in low or "cannot describe" in low:
        return "no-tag"
    return "git-failed"


def _describe(repo_dir: str | Path) -> str:
    """‏#1159: כישלון זורק ``VersionError`` — קטגוריה + הסיבה של git.
    ‏``current_version`` מקפל אותו ל-``None`` כמו קודם."""
    ok, out, err = _run(["git", "-C", str(repo_dir), "describe", "--tags"], timeout=10)
    if not ok:
        raise VersionError(_describe_error(err),
                           f"git describe --tags על {repo_dir} נכשל: {err or 'בלי פלט שגיאה'}")
    if not out.strip():
        raise VersionError("no-tag", f"git describe --tags על {repo_dir} לא החזיר תג")
    return out.strip()


def _ls_remote_tags(url: str) -> tuple[bool, str, str]:
    # חד-פעמי, 20 שניות: זה החיבור היוצא היחיד שהבדיקה פותחת (סעיף 4
    # בתגובת נדב) — אין polling ואין חיבור קבוע לציבורי.
    return _run(["git", "ls-remote", "--tags", url], timeout=20)


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


class VersionRead(NamedTuple):
    """‏``version`` שנקרא ו-``error``/``detail`` שניהם ``None``; או ``version``
    ‏``None`` עם ``error`` (קטגוריה קבועה, ל-endpoint ללא הזדהות) ו-``detail``
    (הסיבה המלאה, ליומן ולמנהל)."""
    version: str | None
    error: str | None
    detail: str | None


def read_version(hooks: Hooks, repo_dir: str | Path) -> VersionRead:
    """‏#1159: לעולם לא גרסה ``None`` בלי קטגוריה, ולעולם לא ניחוש (בלי
    נפילה לתג של ה-ISO: אחרי עדכון הוא כבר לא נכון). גרסה שלא נקראה
    נכתבת גם ליומן, כי ``/health/live`` נשאל בדיוק ברגעים שבהם היא
    קובעת — סוף ההתקנה וסוף השדרוג."""
    try:
        version = hooks["describe"](repo_dir)
    except VersionError as exc:
        category, detail = exc.category, str(exc)
    except Exception as exc:                           # noqa: BLE001
        category, detail = "git-failed", str(exc) or type(exc).__name__
    else:
        if version:
            return VersionRead(version, None, None)
        category, detail = "no-tag", f"git describe --tags על {repo_dir} לא החזיר תג"
    log.warning("version: not read from %s (%s): %s", repo_dir, category, detail)
    return VersionRead(None, category, detail)


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
    except Exception:                                    # noqa: BLE001
        # ‏#1127: DB נעול = המפעיל רואה `idle` במקום `failed`; לפחות ביומן
        log.warning("update: status %s not written (db)", doc.get("state"), exc_info=True)


def get_status(conn) -> dict | None:
    return _get_json_setting(conn, STATUS_KEY)


def _get_json_setting(conn, key: str) -> dict | None:
    try:
        raw = get_setting(conn, key)
    except Exception:                                    # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def record_last_check(conn, result: dict) -> dict:
    """‏#1000: שומר את תוצאת הבדיקה עם חותמת השרת. הכתיבה **אינה** נבלעת:
    בדיקה שרצה ולא נזכרה תציג "לא נבדק" — וזה שקר שקט (עיקרון 5)."""
    doc = {"at": now_iso(), **result}
    set_setting(conn, LAST_CHECK_KEY, json.dumps(doc))
    return doc


def get_last_check(conn) -> dict | None:
    """‏``None`` = מעולם לא נבדק (או רשומה פגומה) — לא "אין חדש"."""
    return _get_json_setting(conn, LAST_CHECK_KEY)


def status_with_verification(conn, hooks: Hooks, repo_dir: str | Path) -> dict:
    """מצב ההרצה האחרונה + הראיה החיובית: האם הגרסה שרצה בפועל, עכשיו,
    היא התג שביקשנו. "לא ידוע עדיין" (השרת עוד לא עלה מחדש) שונה מפורשות
    מ"נכשל" — עיקרון 5, אותה משפחה כמו ``ssh_switch.Listeners``."""
    doc = get_status(conn) or {"state": "idle"}
    current = current_version(hooks, repo_dir)
    result = {**doc, "current": current}
    if doc.get("state") == "running" and doc.get("tag"):
        if current == doc["tag"]:
            result["verified"] = True
            result["state"] = "done"
        else:
            result["verified"] = False
    return result


def _apply_in_background(conn, hooks: Hooks, repo_dir: str | Path, tag: str,
                         user: str) -> None:
    previous = current_version(hooks, repo_dir)
    if previous:
        try:
            set_setting(conn, PREVIOUS_KEY, previous)
        except Exception:                                # noqa: BLE001
            log.warning("update: previous version %s not recorded (db)", previous, exc_info=True)

    # ‏#1193: תהליך ה-web רץ עם ProtectSystem=strict, ולכן אסור לו לכתוב
    # לעץ הקוד. ה-fetch וה-checkout נעשים בתוך server-upgrade.sh, ביחידה
    # המנותקת שמחוץ לארגז החול. הסטטוס נכתב לפני השיגור כדי שלא יהיה חלון
    # שבו בקשה שהתקבלה עדיין נראית idle.
    _set_status(conn, {"state": "running", "tag": tag})
    ok, err = hooks["run_upgrade"](repo_dir, tag)
    if not ok:
        _set_status(conn, {"state": "failed", "tag": tag,
                           "error": f"הפעלת tools/server-upgrade.sh נכשלה: {err}"})
        journal(conn, "update_apply_failed", f"{tag}: script — {err}", user)
        return

    # ‏#748 סעיף 5: "אומת" נקבע רק ב-``status_with_verification`` אחרי
    # שהשרת עלה מחדש עם התג — לא כאן. ``running`` נשאר עד אז; הסקריפט
    # עצמו מחליף אותו ל-``failed`` אם אחד משלבי השדרוג נכשל.
    journal(conn, "update_apply_started", tag, user)


def start_apply(ctx, hooks: Hooks, repo_dir: str | Path, tag: str, user: str) -> None:
    """שיגור הסקריפט המנותק (שניות ספורות, לא השדרוג עצמו). ‏FastAPI
    מריץ נתיבי `def` סינכרוניים בתוך thread pool משלו, כך שזה אינו חוסם
    את ה-event loop; אין צורך ב-``threading.Thread`` נוסף."""
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
    del server_base  # שם השרת מגיע מ-/me ומהגדרת server_name, לא מכתובת ההאזנה.
    hooks = {**default_hooks(), **(hooks or {})}

    def _server_name() -> str:
        import socket
        return get_setting(ctx.conn, "server_name") or socket.gethostname()

    @router.get("/backup")
    def settings_backup(user=Depends(admin_only)):
        """‏#1128: "הורד גיבוי הגדרות" — tar.gz של data_dir עם עותק עקבי של
        ה-DB (backup API, לא cp), ‏MANIFEST.txt ו-SHA256SUMS; **בלי אימג'ים**.
        הארכיון מכיל סודות — הקונסולה מזהירה לפני ההורדה."""
        from fastapi.responses import Response  # noqa: PLC0415
        from . import backup  # noqa: PLC0415
        if ctx.data_dir is None:
            raise HTTPException(503, "תיקיית הנתונים אינה ידועה לשרת הזה — אין מה לגבות")
        payload, name = backup.build_archive(ctx.conn, Path(ctx.data_dir),
                                             current_version(hooks, repo_dir))
        journal(ctx.conn, "settings_backup_downloaded", f"{name} {len(payload)} bytes", user[0])
        return Response(payload, media_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})

    @router.get("")
    def info(user=Depends(admin_only)):
        del user
        ver = read_version(hooks, repo_dir)
        return {
            "current": ver.version,
            # ‏#1159: הסיבה המלאה (נתיב + stderr של git) — admin בלבד; ל-
            # ‏`/health/live` ללא ההזדהות מגיעה רק הקטגוריה.
            "current_error": ver.detail,
            "enabled": enabled(ctx.conn),
            "previous": get_setting(ctx.conn, PREVIOUS_KEY),
            # ‏#1000: ‏null = מעולם לא נבדק בשרת הזה; אחרת {at, current,
            # latest, available, reason} כפי שנשמר ב-POST /update/check.
            "last_check": get_last_check(ctx.conn),
        }

    @router.get("/status")
    def status(user=Depends(admin_only)):
        del user
        return status_with_verification(ctx.conn, hooks, repo_dir)

    @router.post("/check")
    def check(user=Depends(admin_only)):
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        result = record_last_check(ctx.conn, check_update(hooks, repo_dir, public_url))
        journal(ctx.conn, "update_check",
               f"current={result['current']} latest={result['latest']}", user[0])
        return result

    def _apply_or_revert(body: dict, user, tag: str, *, confirm_name: bool) -> dict:
        if not enabled(ctx.conn):
            raise HTTPException(404, "עדכון כבוי בהגדרות השרת")
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        if confirm_name and typed != _server_name():
            raise HTTPException(403, "השם שהוקלד אינו תואם את שם השרת")
        if _active_round(ctx):
            raise HTTPException(409, "יש סבב פתוח/רץ — לא ניתן לעדכן עכשיו")
        start_apply(ctx, hooks, repo_dir, tag, user[0])
        return {"ok": True, "started": True, "tag": tag}

    # ‏def רגיל (לא async): FastAPI מריץ אותו ב-thread pool משלו. הקריאה
    # ממתינה רק ל-systemd-run שמקבל את היחידה; git והרסטארט רצים ביחידה
    # המנותקת, מחוץ לתהליך ולארגז החול של שרת ה-web.
    @router.post("/apply")
    def apply(body: dict = Body(default={}), user=Depends(admin_only)):
        tag = body.get("tag", "") if isinstance(body, dict) else ""
        if not semver_key(tag):
            raise HTTPException(400, f"תג לא תקין: {tag!r}")
        # עדכון הפיך דרך update_previous, ולכן אינו דורש הקלדת שם (#1192).
        return _apply_or_revert(body, user, tag, confirm_name=False)

    @router.post("/revert")
    def revert(body: dict = Body(default={}), user=Depends(admin_only)):
        previous = get_setting(ctx.conn, PREVIOUS_KEY)
        if not previous:
            raise HTTPException(404, "אין גרסה קודמת לחזור אליה")
        return _apply_or_revert(body, user, previous, confirm_name=True)

    return router
