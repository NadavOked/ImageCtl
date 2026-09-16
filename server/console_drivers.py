"""ה-API של ספריית הדרייברים (#720) — לקונסולה ולסוכן.

קונסולה (`/api/console/drivers`): רשימה עם "מתאים ל-N מכונות" לפי
המלאי האחרון של המכונות הרשומות, ייבוא tar (admin, sha256 של כל קובץ
לפני הכניסה — `drivers.DriverLibrary.import_tar`), ומחיקה מאחורי
הקלדת שם (עיקרון 7).

סוכן (`/api/v1/agent/drivers`): אחרי השחזור ולפני האתחול הסוכן שואל
אילו חבילות מתאימות ל-MAC שלו — השרת מתאים לפי המלאי האחרון שאותו
MAC דיווח ב-hello — ומוריד את הקבצים בשמם המדויק מהמניפסט.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac

from . import auth, inventory
from .api import ServerContext
from .db import journal
from .drivers import DriverError, DriverLibrary, match


def _matches_by_package(conn, library: DriverLibrary) -> dict[str, list[str]]:
    """שם חבילה → ה-MACs של המכונות **הרשומות** שהמלאי האחרון שלהן תואם."""
    registered = {r["mac"] for r in conn.execute("SELECT mac FROM machines")}
    packages = list(library.scan().values())
    result: dict[str, list[str]] = {p["name"]: [] for p in packages}
    for mac, seen in inventory.latest_all(conn).items():
        if mac not in registered:
            continue
        for hit in match(seen["inventory"], packages):
            result[hit["name"]].append(mac)
    return result


def create_drivers_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)
    library: DriverLibrary = ctx.drivers

    @router.get("/drivers")
    def list_drivers(user=Depends(current_user)):
        hits = _matches_by_package(ctx.conn, library)
        return [{**p, "matches": sorted(hits.get(p["name"], []))}
                for p in library.public_list()]

    @router.post("/drivers/upload")
    async def upload_driver(request: Request, user=Depends(admin_only)):
        """קליטת חבילה מקובץ tar. הגוף הוא הקובץ עצמו, כמו העלאת אימג'."""
        library.root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(delete=False, dir=library.root, suffix=".upload")
        temp = Path(handle.name)
        try:
            with handle:
                async for chunk in request.stream():
                    handle.write(chunk)
            if temp.stat().st_size == 0:
                raise DriverError("לא התקבל קובץ")
            manifest = library.import_tar(temp)
        except DriverError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:                       # tar פגום, קלט חתוך
            raise HTTPException(400, f"הארכיון לא נקרא: {exc}")
        finally:
            temp.unlink(missing_ok=True)
        journal(ctx.conn, "driver_upload",
                f'{manifest["name"]} ({len(manifest["files"])} files)', user[0])
        return {"name": manifest["name"], "files": len(manifest["files"])}

    @router.post("/drivers/{name}/delete")
    async def delete_driver(name: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        if library.get(name) is None:
            raise HTTPException(404, "חבילה לא קיימת")
        if body.get("confirm_name", "") != name:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם החבילה")
        library.delete(name)
        journal(ctx.conn, "driver_delete", name, user[0])
        return {"ok": True}

    return router


def create_agent_drivers_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    library: DriverLibrary = ctx.drivers

    @router.get("/agent/drivers")
    def agent_drivers(mac: str = "") -> JSONResponse:
        """מה מתאים למכונה הזו. שלושה מצבים, לא שניים (עיקרון 5):
        ‏`inventory: false` = המכונה מעולם לא דיווחה מלאי (סוכן ישן /
        schema 1) — הסוכן מדווח `failed`, לא `no_match`; ‏`inventory:
        true` עם `packages: []` = יש מלאי ואין חבילה תואמת."""
        canonical = lenient_mac(mac)
        if canonical is None:
            return JSONResponse({"ok": False, "error": "missing or malformed mac",
                                 "code": "bad_mac"}, status_code=400)
        seen = inventory.latest(ctx.conn, canonical)
        if seen is None:
            return JSONResponse({"ok": True, "inventory": False, "packages": []})
        packages = library.scan()
        answer = []
        for hit in match(seen["inventory"], list(packages.values())):
            manifest = packages[hit["name"]]
            answer.append({
                "name": hit["name"], "by": hit["by"],
                "files": [{"path": f["path"], "sha256": f["sha256"],
                           "url": f"/api/v1/agent/drivers/{hit['name']}/files/{f['path']}"}
                          for f in manifest["files"]],
            })
        return JSONResponse({"ok": True, "inventory": True, "packages": answer})

    @router.get("/agent/drivers/{name}/files/{path:path}")
    def agent_driver_file(name: str, path: str):
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        found = library.file_path(name, path)
        if found is None:
            return JSONResponse({"ok": False, "error": "file not in this package",
                                 "code": "no_file"}, status_code=404)
        return FileResponse(found, media_type="application/octet-stream")

    return router
