"""‏#738 (tracer 2 של #703): הקיוסק כאפליקציה נפרדת עם allowlist קשיח.

הקיוסק הוא דף הקונסולה שרץ **על וילן ההפצה** — מסך התחנה
(`/console/station`) שממנו קולטים אימג', פותחים סבב כיתה, ומפעילים את
חדר השיכפולים. ‏tracer 1 (#731) פיצל את המאזינים לסוכן ולקונסולה אבל
השאיר את הקיוסק על אפליקציית הקונסולה — כלומר על אותו socket שחושף גם
משתמשים, מכונות, יומן, הגדרות, אחסון וניטור. אסטרה (#703): **הגבול הוא
ה-socket, לא הרשאת-route** — אפליקציית הניהול המלאה לא תאזין על כתובת
שכיתה רואה, והקיוסק מקבל אפליקציה משלו עם רשימת-היתר, לא חריג בתוך
המאזין הרחב.

מה שהקיוסק **באמת** צורך נמדד מקוד ה-JS שלו (`static/station/*.js`),
לא הונח: ‏`grep` על כל קריאות ה-`fetch` נותן בדיוק את הקבוצה שב-
`KIOSK_CONSOLE_ROUTES`, ועוד שני ראוטרים שכולם קיוסק (חדר השיכפולים
ומסך התחנה). כל השאר — users/machines/net/dhcp/storage/monitor/journal/
settings/library-management — **אינו נכנס**, וזו הבטחה שנבדקת: טסט ה-
allowlist מונה את כל הנתיבים על `kiosk_app` ונופל אם נתיב ניהול הופיע.

הראוטרים המשותפים (console_api/console_library/capture) מכילים גם נתיבי
ניהול, ולכן **מסננים** מהם רק את הנתיבים שברשימה — אותם אובייקטי route
בדיוק, ולכן חוזה-התגובה זהה לזה של הקונסולה בהגדרה, בלי שכפול לוגיקה.
`create_room_router`/`create_station_router` הם כולם קיוסק ולכן נכנסים
בשלמותם."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.routing import APIRoute

from .api import ServerContext
from .capture import create_console_capture_router
from .console_api import create_console_router
from .console_library import create_library_router
from .room import create_room_router
from .station import create_station_router

#: רשימת-ההיתר הקשיחה לנתיבי ``/api/console`` שהקיוסק צורך — (method, path).
#: נמדדה מקוד ה-JS של הקיוסק (`static/station/{station,classes,room}.js`):
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
    for source in (create_console_router(ctx), create_library_router(ctx),
                   create_console_capture_router(ctx)):
        router.routes.extend(_allowlisted(source, KIOSK_CONSOLE_ROUTES))
    router.include_router(create_room_router(ctx, wake=room_wake))
    router.include_router(create_station_router(ctx))
    return router
