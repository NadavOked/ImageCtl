"""שכבת האחסון — SQLite יחיד על דיסק השרת.

הספרייה של האימג'ים היא *לא* כאן: אימג' הוא תיקייה עם manifest.json,
והדיסק הוא מקור האמת (ראו images.py). ה-DB מחזיק את מה שאין לו ייצוג
טבעי כקבצים: טבלת ה-MAC, קבוצות, סבבים, משתמשים ויומן.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

#: התור של הכותבים בתוך התהליך. חיבור נפרד לכל תהליכון (ראו Database
#: למטה) פותר את *שיתוף מצב הטרנזאקציה*, אבל לא את **ההוגנות**: ‏sqlite
#: אינו מבטיח שכותב ממתין יקבל את הנעילה אי פעם. הממתין ישן ב-busy
#: handler, ובזמן השינה האחרים כותבים שוב ושוב; כשכל commit ארוך (דיסק
#: איטי, כמו של runner ב-CI) ההסתברות שהנעילה תהיה פנויה בדיוק ברגע
#: ההתעוררות שואפת לאפס, וההמתנה מתכלה עד ``busy_timeout`` מלא. התוצאה
#: היא ``database is locked`` שנראה כמו עומס והוא בסך הכל חוסר הוגנות
#: ‏(#272 — ‏`journal` מעולם לא סבל מזה, ‏`net_seen` כן, וזה כל ההבדל).
#:
#: לכן כתיבה חמה שרצה מכמה תהליכונים עוברת כאן: התור בפייתון הוגן,
#: ו-sqlite אינו רואה תחרות בין התהליכונים שלנו. הנעילה נלקחת סביב
#: **הטרנזאקציה כולה** ולא סביב execute בודד — אחרת אין לה משמעות.
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('build', 'cloner', 'classroom')),
    sort     INTEGER NOT NULL DEFAULT 0    -- הסדר שנקבע בגרירה בקונסולה
);

CREATE TABLE IF NOT EXISTS machines (
    mac      TEXT PRIMARY KEY,          -- קנוני: lowercase עם נקודתיים
    suffix   TEXT NOT NULL,             -- "01".."99" או "INS"
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    note     TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    group_id         TEXT NOT NULL REFERENCES groups(id),
    image_id         TEXT NOT NULL,
    prefix           TEXT NOT NULL,
    expected_clients INTEGER NOT NULL,
    wait_seconds     INTEGER NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('open', 'running', 'closed')),
    opened_by        TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_join_at     REAL NOT NULL,     -- epoch; הטיימר מתאפס בכל מצטרף
    started_at       TEXT,
    closed_at        TEXT,
    roster_json      TEXT,              -- בחירת מחשבים; NULL = כל הקבוצה
    -- הזרם: 'multicast' הוא udp-sender, ולכן אחד בכל המערכת. 'unicast'
    -- הוא משיכת HTTP של תחנה בודדת — לא נוגעת בשידור, וכמה כאלה יחד.
    kind             TEXT NOT NULL DEFAULT 'multicast'
                     CHECK (kind IN ('multicast', 'unicast'))
);

CREATE TABLE IF NOT EXISTS session_members (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    mac        TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'waiting',
    done       INTEGER NOT NULL DEFAULT 0,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    error      TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, mac)
);

CREATE TABLE IF NOT EXISTS room_rounds (
    id              TEXT PRIMARY KEY,
    image_id        TEXT NOT NULL,
    target_drives   INTEGER NOT NULL,      -- כמה כוננים צריך הפעם, סה"כ
    written_drives  INTEGER NOT NULL DEFAULT 0,
    written_serials TEXT NOT NULL DEFAULT '[]',  -- JSON; מגירה נספרת פעם אחת
    state           TEXT NOT NULL CHECK (state IN ('active', 'closed')),
    wave_session_id TEXT,                  -- הגל הנוכחי הוא session רגיל
    wave_number     INTEGER NOT NULL DEFAULT 1,
    opened_by       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    closed_at       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_hash  TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('admin', 'deploy')),
    created_at TEXT NOT NULL,
    disabled_at TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    user   TEXT NOT NULL DEFAULT '',    -- ריק = המערכת עצמה
    event  TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    mac         TEXT NOT NULL,             -- המכונה שהמשימה מיועדת לה
    type        TEXT NOT NULL,
    disk        TEXT NOT NULL,
    image_id    TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    folder      TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    bytes_written INTEGER NOT NULL DEFAULT 0,
    bytes_total   INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- כמה פעמים השרת כבר שלח מכונה לסוכן עבור אותה עבודה, ומתי. זה מה
-- שעוצר את לולאת האתחול של #75: התפריט הוא של השרת, ולכן הספירה כאן
-- היא ראיה חיובית ולא הסתמכות על דיווח ממכונה שאולי אין לה רשת בכלל.
-- ההקשר תחום בזמן ('session:<id>' / 'task:<id>') וכל הקשר חדש מאפס.
CREATE TABLE IF NOT EXISTS boot_attempts (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    context  TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- מי הגיע לסוכן אף שהשרת שלח אותו לדיסק המקומי (#112). זו התמונה
-- החיה בלבד, לא ארכיון: שורה מתארת את הלולאה *הנוכחית*, והמונה מתאפס
-- כשעברו LOOP_SILENCE_SECONDS בלי hello. טבלה נפרדת ולא עמודה
-- ב-net_devices, מפני ש-net_devices היא מלאי קבוע (first_seen שלא
-- מתאפס אף פעם) ואילו כאן כל שדה הוא בן-חלוף לפי הגדרה. הארכיון הוא
-- היומן, שאינו נמחק — ראו server/hello.py.
CREATE TABLE IF NOT EXISTS agent_loops (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    hits     INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- מי פנה לשרת מכתובת מקומית שאינה כתובת וילן ההפצה (#137). כמו
-- agent_loops זו התמונה החיה בלבד: השורה יורדת אחרי SILENCE_SECONDS
-- בלי פנייה, והמונה איתה. `address` היא הרגל של השרת שעליה הבקשה
-- התקבלה — כלומר מאיזו רשת המכונה מדברת. הארכיון הוא היומן.
CREATE TABLE IF NOT EXISTS off_vlan_contacts (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    address  TEXT NOT NULL,
    hits     INTEGER NOT NULL DEFAULT 0,
    first_at TEXT NOT NULL,
    last_at  TEXT NOT NULL
);

-- שביל הפירורים של האתחול (#400): עד לאן הגיעה כל מכונה בין תפריט
-- ה-GRUB לבין ה-hello הראשון. מחשב שיכפול חסר-ראש שנעצר באמצע אינו
-- משאיר שום עקבה אחרת, וזה מה שחסם את הבדיקה על הברזל.
--
-- כמו agent_loops זו התמונה **החיה** בלבד: שורה אחת למכונה, שנדרסת
-- בכל אתחול. ‏`idx` הוא מקומו של הצעד ברשימה המנויה (boot/trace.py),
-- ושמור כאן כדי שהאיפוס — צעד שחזר אחורה, כלומר אתחול חדש — יוכרע
-- בתוך ה-UPSERT עצמו ולא בקריאה-ואז-כתיבה שיש בה מרוץ.
CREATE TABLE IF NOT EXISTS boot_steps (
    mac      TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    step     TEXT NOT NULL,
    idx      INTEGER NOT NULL,
    at       TEXT NOT NULL,
    first_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS net_devices (
    mac         TEXT PRIMARY KEY,       -- קנוני: lowercase עם נקודתיים
    ip          TEXT,
    description TEXT NOT NULL DEFAULT '',
    first_seen  TEXT,
    last_seen   TEXT,
    disks_json  TEXT                    -- הדיסקים מה-hello האחרון, כפי שדווחו
);
"""

#: הקבוצות הקבועות: חדר שיכפולים ומחשב הבנייה הם יחידים במערכת —
#: אין ניהול קבוצות עבורם, רק רשימת מכונות.
FIXED_GROUPS = [
    ("grp_CLONERS", "מחשבי שיכפול", "cloner"),
    ("grp_BUILD", "מחשב בניית אימג'ים", "build"),
]

#: ברירות מחדל. recovery דורש כניסה כל עוד אין endpoint כניסה בסוכן —
#: הצד הבטוח. לפתיחה זמנית (הדגמה) יש מתג בקונסולה.
DEFAULT_SETTINGS = {
    "recovery_require_login": "true",
    "session_wait_seconds": "300",
    # ניתוק אוטומטי של הקונסולה בחוסר פעילות. המסך עומד בכיתה או במשרד
    # פתוח — מי שקם והלך לא משאיר אחריו קונסולת ניהול פתוחה.
    "console_idle_seconds": "300",
}


def now_iso() -> str:
    """זמן בפורמט המוסכם: ISO 8601 עם אזור זמן."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: עמודות שנוספו אחרי שכבר היו התקנות בשטח. CREATE TABLE IF NOT EXISTS
#: לא מוסיף עמודה לטבלה קיימת, ולכן ההשלמה נעשית כאן במפורש.
ADDED_COLUMNS = [
    ("groups", "sort", "INTEGER NOT NULL DEFAULT 0"),
    ("net_devices", "disks_json", "TEXT"),
    # הדיווח האחרון כפי שהגיע, יעד-יעד. סבב החדר סופר ממנו אילו
    # מגירות (לפי serial) נכתבו בהצלחה.
    ("session_members", "targets_json", "TEXT"),
    # סבב לחלק מהכיתה: רשימת ה-MAC שנבחרו. NULL = כל הקבוצה.
    # רק הנבחרים מוערים ב-WoL ורק הם מצטרפים — השאר עולים מהדיסק.
    ("sessions", "roster_json", "TEXT"),
    # הזרם (#60). התקנה קיימת מכילה סבבי מולטיקאסט בלבד, ולכן ברירת
    # המחדל היא בדיוק מה שהיה — ואף שורה קיימת לא משנה משמעות.
    ("sessions", "kind",
     "TEXT NOT NULL DEFAULT 'multicast'"
     " CHECK (kind IN ('multicast', 'unicast'))"),
    # ‏#186: חסימת משתמש. **חותמת ולא דגל** — "מתי נחסם" הוא מידע
    # שהמפעיל צריך, ו-NULL הוא "פעיל". התקנה קיימת מקבלת NULL בכל
    # שורה, כלומר אף משתמש קיים לא נחסם על ידי המיגרציה.
    ("users", "disabled_at", "TEXT"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


#: מה ש"בדיקה ואז כתיבה" בקוד לא יכולה לאכוף. שני תהליכונים, שני
#: חיבורים, ובין ה-SELECT ל-INSERT יש חלון של כמה שאילתות — ‏#103 (שני
#: שידורים פתוחים) ו-#104 (שתי משיכות לאותה תחנה). האינדקס החלקי
#: הייחודי מכריע ברמת ה-DB: ה-INSERT השני **נכשל**, ולא מנצח בשקט.
#:
#: ‏`roster_json` של משיכה הוא תמיד רשימה בת MAC אחד (‏`open_pull`),
#: ולכן ייחודיות עליו היא בדיוק "משיכה פעילה אחת לתחנה". ‏NULL אינו
#: ייחודי ב-sqlite, ולכן שורות ישנות בלי roster לא חוסמות דבר — הן
#: נבדקות בקוד (`pulls.active_for`).
_ACTIVE = "state IN ('open', 'running')"
UNIQUE_ACTIVES = [
    ("one_active_broadcast", "kind", "kind = 'multicast'"),
    ("one_active_pull_per_station", "roster_json", "kind = 'unicast'"),
]


def _close_duplicate_actives(conn: sqlite3.Connection) -> None:
    """התקנה בשטח יכולה כבר להחזיק שורות שמפרות את האינדקס, ואז יצירתו
    נכשלת והשרת לא עולה. אלה בדיוק סבבי הרפאים של #103: ‏`active_broadcast`
    מחזירה ``LIMIT 1``, ולכן כל מה שמעבר לראשון היה בלתי נראה ממילא — אף
    מכונה לא הצטרפה אליו ואף מפעיל לא ראה אותו.

    נשמר הראשון (אותו סדר בדיוק: ‏``created_at`` ואז ``rowid``), והשאר
    נסגרים **ביומן**. סגירה שקטה של סבב היא מה שאסור; סגירה רשומה של
    סבב שלא היה נראה מעולם היא ניקוי המצב שהבאג הותיר.
    """
    for name, key, kind in UNIQUE_ACTIVES:
        extra = conn.execute(
            f"SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY {key}"
            f" ORDER BY created_at, rowid) AS rn FROM sessions"
            f" WHERE {kind} AND {_ACTIVE} AND {key} IS NOT NULL) WHERE rn > 1"
        ).fetchall()
        for row in extra:
            conn.execute(
                "UPDATE sessions SET state = 'closed', closed_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
            journal(conn, "session_dedupe", f"{row['id']} — {name}")
        conn.commit()


def _create_unique_indexes(conn: sqlite3.Connection) -> None:
    """אחרי הפינוי — והוא חייב להצליח. אינדקס שלא נוצר הוא אכיפה שאינה
    קיימת, ושרת שעולה בלעדיו נראה תקין בדיוק כמו שרת מוגן (עיקרון 5)."""
    for name, key, kind in UNIQUE_ACTIVES:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name} ON sessions ({key})"
            f" WHERE {kind} AND {_ACTIVE}"
        )
    conn.commit()


def _open(path: str) -> sqlite3.Connection:
    """חיבור גולמי אחד, עם ההגדרות שכל חיבור לבסיס הזה חייב."""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # ממתינים על נעילה במקום להיכשל מיד. עם חיבור לכל תהליכון זו כבר
    # לא חגורה תיאורטית: WAL מרשה כותב אחד בכל רגע, והשאר ממתינים כאן.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _initialize(conn: sqlite3.Connection) -> None:
    """הסכימה וברירות המחדל — פעם אחת לקובץ, לא פעם אחת לחיבור."""
    conn.executescript(SCHEMA)
    _add_missing_columns(conn)
    _close_duplicate_actives(conn)
    _create_unique_indexes(conn)
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
    # סוד חתימת ה-cookie של הקונסולה נולד פעם אחת לכל התקנה.
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("console_secret", secrets.token_hex(32)),
    )
    for gid, label, role in FIXED_GROUPS:
        conn.execute(
            "INSERT OR IGNORE INTO groups (id, label, role) VALUES (?, ?, ?)",
            (gid, label, role),
        )
    conn.commit()


class Database:
    """אובייקט אחד שמחזיק חיבור sqlite נפרד לכל תהליכון.

    זה לא ליטוש: חיבור sqlite אחד המשותף לכמה תהליכונים מחזיק *מצב
    טרנזאקציה אחד*. ‏uvicorn מריץ endpoint שהוא ``async def`` על לולאת
    האירועים, אבל ``def`` רגיל — וגם כל dependency סינכרוני, כולל
    ``current_user`` של הקונסולה — רץ בתהליכון מהמאגר. כלומר שני
    תהליכונים כותבים על אותו חיבור בכל בקשת קונסולה שמתרחשת תוך כדי
    hello. התוצאה היא לא איטיות אלא חריגות אקראיות —
    "cannot start a transaction within a transaction",
    "cannot commit - no transaction is active", "bad parameter or other
    API misuse" — שיוצאות מה-endpoint כ-500. במדידה: כ-15% מהכתיבות
    נופלות כששלושה תהליכונים חולקים חיבור.

    לכן ההגנה אינה נעילה סביב כל execute (טרנזאקציה נפרשת על כמה
    קריאות, ונעילה לכל קריאה לא מגינה עליה) אלא חיבור לכל תהליכון:
    לכל טרנזאקציה יש מצב משלה, ו-WAL מסדר את הכותבים ביניהם.

    האובייקט מתחזה לחיבור (``execute``/``commit``/``close``) כדי שכל
    מי שמקבל ``conn`` ימשיך לעבוד בלי לדעת. תהליכון שמת משחרר את
    החיבור שלו עם ה-``threading.local``.
    """

    def __init__(self, path: str | Path):
        self._path = str(path)
        self._local = threading.local()
        self._ready = False
        self._init_lock = threading.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        """החיבור של התהליכון הנוכחי; נפתח בפעם הראשונה שנדרש."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _open(self._path)
            with self._init_lock:
                if not self._ready:
                    _initialize(conn)
                    self._ready = True
            self._local.conn = conn
        return conn

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        return self.connection.execute(sql, parameters)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return self.connection.executescript(sql)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        """סוגר את החיבור של התהליכון הקורא בלבד — לשאר יש משלהם."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def connect(path: str | Path) -> Database:
    """פותח (ובמידת הצורך יוצר) את בסיס הנתונים, מוכן לעבודה.

    מוחזר ``Database`` ולא ``sqlite3.Connection``: אותו ממשק, אבל
    בטוח לתהליכונים. ראו את ההסבר ב-``Database``.
    """
    db = Database(path)
    db.connection            # יצירת הסכימה כאן, על התהליכון שקרא
    return db


def update_one(conn, sql: str, parameters=()) -> bool:
    """‏UPDATE מותנה: מחזיר האם השורה השתנתה, ולא משאיר נעילה מאחוריו.

    כתיבה פותחת טרנזאקציה גם כשלא תאמה אף שורה, ו-sqlite אוחז בנעילת
    הכתיבה עד ``commit`` או ``rollback``. עם חיבור משותף זה היה בלתי
    נראה — כולם ישבו באותה טרנזאקציה. עם חיבור לכל תהליכון, קורא
    שהפסיד במרוץ וזרק חריגה משאיר **נעילה יתומה**: כל שאר התהליכונים
    ממתינים ``busy_timeout`` שלם ואז מקבלים "database is locked".

    הכשל מתחזה לעומס, והוא בסך הכל מנעול שאיש לא שחרר — ולכן השחרור
    יושב כאן, במקום אחד, ולא כשורה שצריך לזכור בכל אתר קריאה.
    """
    cur = conn.execute(sql, parameters)
    if cur.rowcount == 1:
        return True
    conn.rollback()
    return False


@contextmanager
def writing(conn):
    """רצף כתיבות שמסתיים ב-``commit`` — ובכל מסלול יציאה אחר ב-``rollback``.

    זו ההשלמה של ``update_one`` לרצפים. ברצף, הכתיבה הראשונה כבר תפסה
    את נעילת הכתיבה, ואם השנייה זורקת ואיש אינו עושה ``rollback`` —
    הנעילה נשארת יתומה. החיבור הוא של התהליכון (``Database``) והוא
    נשאר בחיים, ולכן היא משוחררת רק בכתיבה המוצלחת הבאה *באותו
    תהליכון*; בינתיים כל השאר ממתינים ``busy_timeout`` שלם ומקבלים
    "database is locked" (#54, ‏#184, ‏#200, ‏#290).

    **וגם כתיבה בודדת צריכה את זה** — כאן עמדה קודם טענה הפוכה
    ("כתיבה בודדת שנכשלת אינה משאירה דבר"), והיא נכונה רק לגבי
    ה*נעילה*. ‏pysqlite מוציא ``BEGIN`` לפני כל DML; כתיבה בודדת
    שנכשלה ב-``database is locked`` לא תפסה נעילה, אבל **הטרנזאקציה
    נשארת פתוחה על החיבור**. מכאן והלאה כל כתיבה עליו נכשלת *מיד*, כי
    ‏sqlite אינו מפעיל את ה-busy handler לשדרוג נעילה בתוך טרנזאקציה
    פתוחה. תהליכון של uvicorn חי לאורך זמן וממוחזר, ולכן אירוע עומס
    חולף אחד היה הופך אותו למורעל עד אתחול התהליך (#272).

    הכשל מתחזה לעומס, ולכן השחרור יושב **כאן, במקום אחד**, ולא כשורה
    שצריך לזכור בכל אתר קריאה.

    ‏``BaseException`` ולא ``Exception``: ‏``KeyboardInterrupt`` בזמן
    כיבוי השרת היה משאיר בדיוק את הנעילה שכאן נסגרת.

    החריגה **ממשיכה הלאה** — רצף שנכשל בשקט הוא בדיוק מה שעיקרון 5
    אוסר: מכונה שהצטרפה ואינה רשומה.

    ה-``commit`` עצמו נמצא בתוך השמירה: גם הוא יכול לזרוק (דיסק מלא
    ברגע הכתיבה), וגם אז הנעילה צריכה להשתחרר.
    """
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


#: מתחת לגיל הזה של ``last_seen``, ‏hello חוזר אינו מצדיק כתיבה (#136).
#:
#: כל hello היה טרנזאקציית כתיבה מלאה: 30 כתיבות בדקה לכל מכונה, 600
#: לכיתה של 20 — ודווקא ברגע שבו השרת עסוק בפתיחת סבב. זה המזון של
#: ‏#54. החניקה היא על ה*כתיבה* ולא על הבקשה: ה-hello נענה כרגיל.
#:
#: הסף חייב להישאר קטן מ-``room.AWAKE_SECONDS`` (30), אחרת "חסכנו
#: כתיבות" הופך מכונה חיה לכבויה על המסך. 15 משאיר מרווח של פי שניים,
#: ו-`test_net_seen_throttle` שומר על היחס.
NET_SEEN_MIN_INTERVAL_SECONDS = 15


def _net_seen_unchanged(row: sqlite3.Row, ip: str | None,
                        disks_json: str | None, now: datetime) -> bool:
    """האם השורה כבר אומרת בדיוק את מה שהכתיבה הזו הייתה כותבת.

    ראיה חיובית בלבד (עיקרון 5): חותמת שאי אפשר לפענח, חותמת בלי אזור
    זמן, חותמת מהעתיד (שעון שקפץ), או כל שינוי בכתובת/בדיסקים —
    כולם מחזירים False, כלומר **כותבים**. "לא הצלחנו לבדוק אם צריך
    לכתוב" אינו "בדקנו, אין צורך".
    """
    if ip is not None and ip != row["ip"]:
        return False
    if disks_json is not None and disks_json != row["disks_json"]:
        return False
    try:
        last = datetime.fromisoformat(row["last_seen"])
    except (TypeError, ValueError):
        return False
    if last.tzinfo is None:
        return False
    age = (now - last).total_seconds()
    return 0 <= age < NET_SEEN_MIN_INTERVAL_SECONDS


def net_seen(
    conn: sqlite3.Connection, mac: str, ip: str | None,
    disks_json: str | None = None,
) -> None:
    """כל מגע של מכונה עם השרת — hello או תפריט אתחול — נרשם כאן.

    זו טבלת "מה חי לי ברשת": גם מחשבים שאינם רשומים מופיעים בה,
    עם הכתובת האחרונה שנראתה. התיאור החופשי נשמר בין עדכונים.

    הדיסקים מה-hello נשמרים כפי שדווחו — מסך מחשב הבנייה מציג מהם
    את "מה מותקן עכשיו", והקליטה נפתחת על כונן שקיים באמת.

    ‏hello חוזר בתוך ``NET_SEEN_MIN_INTERVAL_SECONDS`` שאינו מוסיף
    מידע חדש אינו נכתב (#136). הבדיקה היא ``SELECT`` — קורא, ולכן
    ב-WAL הוא אינו נוגע בנעילת הכתיבה בכלל; רק כשיש מה לכתוב נפתחת
    טרנזאקציה. ‏`agent_loops.note` סופר את **הגעת** ה-hello ולא את
    הכתיבה כאן, ולכן החניקה אינה משנה את הספירה שלו.
    """
    now = datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT ip, last_seen, disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    if row is not None and _net_seen_unchanged(row, ip, disks_json, now):
        return

    ts = now.isoformat(timespec="seconds")
    # ‏`_write_lock` — זו הכתיבה שכיתה שלמה דורכת עליה בו-זמנית, והיא
    # חייבת תור הוגן ולא מרוץ על נעילת sqlite (#272). ‏`writing` —
    # כתיבה שנכשלה חייבת להשאיר חיבור נקי; ראו שם.
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO net_devices (mac, ip, first_seen, last_seen, disks_json)"
            " VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (mac) DO UPDATE SET ip = COALESCE(excluded.ip, ip),"
            " last_seen = ?, disks_json = COALESCE(excluded.disks_json, disks_json)",
            (mac, ip, ts, ts, disks_json, ts),
        )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """מסלול הכתיבה של **כל** מסכי ההגדרות — DHCP, כתובות, מתג SSH,
    רשימת התיקיות. ‏`_write_lock` ו-`writing` מאותה סיבה כמו ב-`net_seen`
    (#313): כתיבה שנכשלה כאן משאירה את החיבור בטרנזאקציה, ומשם הקונסולה
    מפסיקה לשמור עד אתחול השרת."""
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def journal(conn: sqlite3.Connection, event: str, detail: str = "", user: str = "") -> None:
    """שורת יומן. נובע ישירות מריבוי המשתמשים — מי עשה מה ומתי.

    נעול, כי זו הכתיבה היחידה שמגיעה גם מתהליכון הרקע של השידור.

    ‏`writing` מאותה סיבה כמו ב-`net_seen` וב-`set_setting` (#379):
    הנעילה מסדרת את הכותבים **שלנו** בתור הוגן, אבל היא אינה עושה
    ``rollback`` ואינה מגנה מפני כותב חיצוני לתהליך — ‏`sqlite3` ידני
    על השרת או גיבוי. שורת יומן שנכשלה בלי ``rollback`` משאירה את
    החיבור בתוך טרנזאקציה, ומשם כל כתיבה עליו נכשלת **מיד** עד אתחול
    השרת (#272). ‏`journal` נקרא כמעט מכל מסלול בשרת, ולכן חיבור
    שהורעל כאן נודד לכל מי שיקבל את התהליכון אחריו — ‏uvicorn ממחזר
    אותם.
    """
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO journal (ts, user, event, detail) VALUES (?, ?, ?, ?)",
            (now_iso(), user, event, detail),
        )
