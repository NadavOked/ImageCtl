"""שלב 0 — הקונסולה: משתמשים, קבוצות, וטבלת ה-MAC.

משתמש ההתחלה נוצר ישירות ב-DB (כמו בהתקנה); כל השאר דרך ה-API
של הקונסולה, בדיוק כפי שנדב היה עושה בדפדפן.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from .harness import (ADMIN, BUILD_MAC, CLASS_MACS, CLONER_MAC, DEPLOY,
                      GB256, REPO, Client, check, disk)
from .machines import SimMachine


def run(data_dir, images_dir) -> SimpleNamespace:
    print("0. קונסולה ורישום")
    sys.path.insert(0, str(REPO))
    from server import users                   # noqa: PLC0415
    from server.db import connect              # noqa: PLC0415
    conn = connect(data_dir / "imagectl.db")
    users.create(conn, ADMIN["username"], ADMIN["password"], "admin", by="sim")
    conn.close()

    console = Client()
    status, _ = console.json("POST", "/api/console/login", ADMIN)
    check("כניסה לקונסולה", status == 200, str(status))
    status, me = console.json("GET", "/api/console/me")
    check("ה-cookie נשמר בין בקשות", status == 200 and me["role"] == "admin", str(me))

    status, _ = console.json("POST", "/api/console/users", dict(DEPLOY, role="deploy"))
    check("משתמש הפצה נוצר", status == 200, str(status))
    deploy = Client()
    status, _ = deploy.json("POST", "/api/console/login", DEPLOY)
    check("כניסת משתמש ההפצה", status == 200, str(status))

    status, _ = console.json("POST", "/api/console/groups",
                             {"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    check("קבוצת כיתה נוצרה", status == 200)
    status, imported = console.json("POST", "/api/console/machines/import", {
        "group_id": "grp_LAB1",
        "text": "\n".join(f"{m} {i:02d}" for i, m in enumerate(CLASS_MACS, start=5)),
    })
    check("ארבע תחנות יובאו", imported.get("saved") == 4, str(imported))
    status, _ = console.json("POST", "/api/console/machines", {
        "mac": BUILD_MAC, "name": "מחשב בנייה", "group_id": "grp_BUILD"})
    check("מחשב הבנייה נרשם (קבוצה קבועה)", status == 200, str(status))
    status, _ = console.json("POST", "/api/console/machines", {
        "mac": CLONER_MAC, "name": "01", "group_id": "grp_CLONERS"})
    check("מחשב השיכפול נרשם", status == 200, str(status))

    # המכונות המדומות — כל אחת תהליכון עם לולאת hello משלה.
    builder = SimMachine(BUILD_MAC, [disk("sda", GB256, "BLD-1")])
    stations = {mac: SimMachine(mac, [disk("sda", GB256, f"STN-{i}")])
                for i, mac in enumerate(CLASS_MACS)}
    # שלוש מגירות זו מעל זו: החריץ (port) הוא סדר המגירות, ושם ההתקן
    # הוא סדר הגילוי — כאן הם הפוכים בכוונה, בדיוק כמו בחדר (#27).
    cloner = SimMachine(CLONER_MAC, [disk("sda", GB256, "DRW-A", port=3),
                                     disk("sdb", GB256, "DRW-B", port=1),
                                     disk("sdc", GB256, "DRW-C", port=2)])
    return SimpleNamespace(
        console=console, deploy=deploy, images_dir=images_dir,
        builder=builder, stations=stations, cloner=cloner,
        image_a=None, manifest_a=None, image_b=None, manifest_b=None,
    )
