"""‏allowlist מחשב הבנייה על פורט הסוכן (:8080) — #738, מצומצם ב-#1247.

עד #1247 היה כאן גם הקיוסק כאפליקציה נפרדת על :8082 עם דף web
(`/console/station`). הדף והמאזין הוסרו — מחשב הבנייה עולה לגואי
הנייטיב. **הראוטר נשאר**, כי הצרכן שלו הוא הסוכן: ‏buildmenu.sh/
roomflow.sh/הגואי נכנסים ל-`/api/console/login`, קולטים וניהול חדר
השיכפולים דרך `$SERVER` שהוא פורט הסוכן (ראה `_add_agent_routes`).

אסטרה (#703): **הגבול הוא ה-socket, לא הרשאת-route** — אפליקציית הניהול
המלאה לא מאזינה על כתובת שמחשב בנייה רואה, והסוכן מקבל רק רשימת-היתר.
טסט ה-allowlist מונה את הנתיבים ונופל אם נתיב ניהול הופיע.

הראוטרים המשותפים (console_api/console_library/capture) מכילים גם נתיבי
ניהול, ולכן **מסננים** מהם רק את הנתיבים שברשימה — אותם אובייקטי route
בדיוק, ולכן חוזה-התגובה זהה לזה של הקונסולה בלי שכפול לוגיקה.
`create_room_router`/`create_station_router` נכנסים בשלמותם."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

from .api import ServerContext
from .capture import create_console_capture_router
from .console_api import create_console_router
from .console_library import create_library_router
from .room import create_room_router
from .station import create_station_router

#: רשימת-ההיתר הקשיחה לנתיבי ``/api/console`` שמחשב הבנייה צורך — (method, path).
#: נמדדה (#738) מקוד ה-JS של מסך התחנה שהוסר, וצרכני הסוכן קוראים אותה:
#:  - login  — כניסה (station.js)
#:  - groups — רשימת הכיתות לבחירה (classes.js)
#:  - images — רשימת האימג'ים לקליטה/סבב (classes.js, room.js)
#:  - folders (GET+POST) — בחירת/יצירת תיקייה לפני קליטה (station.js)
#:  - sessions (+start/close) — פתיחה/התחלה/סגירה של סבב כיתה (classes.js)
#:  - tasks/capture — הזמנת קליטה (station.js)
#: כל נתיב ניהול אחר באותם ראוטרים **אינו** נכנס.
KIOSK_CONSOLE_ROUTES = frozenset({
    ("POST", "/api/console/login"),
    ("GET", "/api/console/groups"),
    ("GET", "/api/console/images"),
    ("GET", "/api/console/folders"),
    ("POST", "/api/console/folders"),
    ("POST", "/api/console/sessions"),
    ("POST", "/api/console/sessions/{session_id}/start"),
    ("POST", "/api/console/sessions/{session_id}/close"),
    ("POST", "/api/console/tasks/capture"),
})


def _allowlisted(source: APIRouter, allow: frozenset[tuple[str, str]]) -> list[APIRoute]:
    """הנתיבים מ-`source` שברשימת-ההיתר — אובייקטי ה-route המקוריים.

    אותו endpoint, אותן dependencies, אותו response_model: החוזה זהה
    לזה של הקונסולה כי זה **אותו** route, לא עותק. נתיב שברשימה אך אינו
    קיים במקור פשוט לא יתווסף — הטסט של ה-allowlist תופס פער כזה בכך
    שהוא דורש שהקבוצה הממומשת תהיה שווה לרשימה המוצהרת."""
    picked: list[APIRoute] = []
    for route in source.routes:
        if isinstance(route, APIRoute) and any(
            (method, route.path) in allow for method in (route.methods or ())
        ):
            picked.append(route)
    return picked


def create_kiosk_router(ctx: ServerContext, *, room_wake) -> APIRouter:
    """הראוטר של הקיוסק: תת-קבוצת ``/api/console`` המסוננת + חדר + תחנה.

    ‏`room_wake` מוזרק (כמו ב-app.py) כדי ששליחת ה-WoL של החדר תהיה אותה
    פונקציה בכל המערכת וניתנת לזיוף בבדיקות."""
    router = APIRouter()
    # ‏#1073: ‏`kiosk=True` — הכניסה כאן מקבלת גם `deploy`. זו הדלת של
    # מחשב הבנייה (buildmenu.sh/ה-GUI נכנסים דרך `/api/console/login` על
    # פורט הסוכן, שנושא את אותו allowlist); הקונסולה (‎:8081) מסרבת לו.
    for source in (create_console_router(ctx, kiosk=True), create_library_router(ctx),
                   create_console_capture_router(ctx)):
        router.routes.extend(_allowlisted(source, KIOSK_CONSOLE_ROUTES))
    router.include_router(create_room_router(ctx, wake=room_wake))
    router.include_router(create_station_router(ctx))
    return router
