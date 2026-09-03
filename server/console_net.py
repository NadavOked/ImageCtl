"""לשונית "רשת" — מה חי ברשת, לפי מה שהשרת ראה בפועל.

כל מכונה שדיברה עם השרת (hello או תפריט אתחול) נרשמת אוטומטית עם
הכתובת שקיבלה. הרשימה כוללת גם מכונות שאינן בטבלת ה-MAC — זה בדיוק
הערך שלה: לראות מה יש ברשת לפני שמחליטים למי לתת תפקיד.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, registry
from .api import ServerContext
from .db import journal, now_iso, update_one


def create_net_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/net")
    def devices(user=Depends(current_user)):
        """ההתקנים + האם הם רשומים, ובאיזו קבוצה."""
        rows = ctx.conn.execute(
            "SELECT d.mac, d.ip, d.description, d.first_seen, d.last_seen,"
            "       m.suffix, g.id AS group_id, g.label AS group_label, g.role"
            "  FROM net_devices d"
            "  LEFT JOIN machines m ON m.mac = d.mac"
            "  LEFT JOIN groups g ON g.id = m.group_id"
            " ORDER BY d.last_seen DESC"
        ).fetchall()
        return [
            {
                "mac": r["mac"], "ip": r["ip"], "description": r["description"],
                "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                "registered": r["suffix"] is not None,
                "name": r["suffix"], "group_id": r["group_id"],
                "group_label": r["group_label"], "role": r["role"],
            }
            for r in rows
        ]

    @router.post("/net")
    async def add_device(request: Request, user=Depends(admin_only)):
        """הוספה ידנית — למכונה שעוד לא דיברה עם השרת."""
        body = await request.json()
        mac = registry.normalize_mac(body.get("mac", ""))
        if mac is None:
            raise HTTPException(400, "MAC לא תקין")
        ts = now_iso()
        ctx.conn.execute(
            "INSERT INTO net_devices (mac, ip, description, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (mac) DO UPDATE SET description = excluded.description",
            (mac, (body.get("ip") or "").strip() or None,
             (body.get("description") or "").strip(), ts, ts),
        )
        ctx.conn.commit()
        journal(ctx.conn, "net_add", mac, user[0])
        return {"mac": mac}

    @router.put("/net/{mac}")
    async def describe_device(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        canonical = registry.normalize_mac(mac)
        if not update_one(
            ctx.conn,
            "UPDATE net_devices SET description = ? WHERE mac = ?",
            ((body.get("description") or "").strip(), canonical),
        ):
            raise HTTPException(404, "התקן לא ברשימה")
        ctx.conn.commit()
        journal(ctx.conn, "net_describe", f"{canonical} {body.get('description', '')}", user[0])
        return {"ok": True}

    @router.delete("/net/{mac}")
    def forget_device(mac: str, user=Depends(admin_only)):
        """הסרה מהרשימה בלבד. אם המכונה תדבר שוב — היא תחזור."""
        ctx.conn.execute(
            "DELETE FROM net_devices WHERE mac = ?", (registry.normalize_mac(mac),)
        )
        ctx.conn.commit()
        journal(ctx.conn, "net_forget", mac, user[0])
        return {"ok": True}

    return router
