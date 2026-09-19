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
#:
#: **סדר הנעילות, ואין ממנו חריג (#457):** קודם ``_write_lock`` ורק
#: אחריו נעילת הכתיבה של sqlite. מי שכבר מחזיק בנעילת הכתיבה — כלומר
#: יש לו טרנזאקציה פתוחה — ‏**אינו רשאי להמתין כאן**, אחרת שני הכיוונים
#: נפגשים בנעילה משולבת. ‏`_settle` הוא האכיפה, והוא נקרא לפני **כל**
#: נטילה כאן.
_write_lock = threading.Lock()


class SchemaError(RuntimeError):
    """סכימה קיימת שאינה תואמת ואי אפשר למגר בשקט (#732).

    ‏``CREATE TABLE IF NOT EXISTS`` הוא no-op על טבלה קיימת גם כשהיא
    חסרה עמודות — ואז הכשל מתגלה רק מאוחר, מול כיתה, כ-``no such
    column``. עדיף להיכשל בקול באתחול, ליד ההתקנה (עיקרון 5)."""


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
    drawer_count INTEGER NOT NULL DEFAULT 3    -- מספר המגירות/פורטים שהוגדר במסוף
                 CHECK (drawer_count BETWEEN 1 AND 8),
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
                     CHECK (kind IN ('multicast', 'unicast')),
    -- בחירת המחיצה להרחבה, מהקונסולה בפתיחת הסבב (#59). NULL = לא
    -- נבחר במפורש, כלומר הבחירה האוטומטית — בדיוק ההתנהגות מלפני #59,
    -- וזה מה שכל פותח סבב שלא עודכן (pulls.py, station.py) עדיין כותב.
    -- 'none' מכבה את ההרחבה; מחרוזת ספרות היא אינדקס מחיצה שנבחר ביד.
    expand_partition TEXT
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
    target_slots_json TEXT,                -- בחירת דיסקי יעד לכל מכונה; NULL=סבב ישן
    expand_partition TEXT,                 -- בחירת ההרחבה לכל גלי הסבב (#59); NULL=אוטומטי
    -- ‏#715: המקור. 'library' = udp-sender של השרת על אימג' מהספרייה;
    -- 'build_disk' = מחשב הבנייה משדר מהדיסק שלו, ו-image_id הוא מזהה
    -- חי (live_…) שהמניפסט שלו הוא live_manifest_json. סבב יחיד, בלי גלים.
    source_kind     TEXT NOT NULL DEFAULT 'library',
    source_mac      TEXT,                  -- מחשב הבנייה (build_disk בלבד)
    source_disk     TEXT,                  -- הדיסק שלו, כפי שדווח ב-hello
    source_task_id  TEXT,                  -- משימת direct_send שנפתחה לו
    live_manifest_json TEXT,               -- המניפסט שמחשב הבנייה דיווח; NULL = טרם
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
    disabled_at TEXT,
    -- #1093: ערכת הנושא של המשתמש. auto = לפי מערכת ההפעלה.
    theme    TEXT NOT NULL DEFAULT 'auto'
             CHECK (theme IN ('auto', 'light', 'dark')),
    must_change_password INTEGER NOT NULL DEFAULT 0,
    mfa_secret TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    mfa_enrolled_at TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    auth_epoch INTEGER NOT NULL DEFAULT 0,
    mfa_last_step INTEGER
);

-- ‏#1085: קודי גיבוי חד-פעמיים ל-TOTP. מוצגים פעם אחת בהפעלה, נשמרים כ-hash.
CREATE TABLE IF NOT EXISTS mfa_backup_codes (
    username  TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at   TEXT,
    PRIMARY KEY (username, code_hash)
);

-- ‏#1085/#1075: מונה כשלונות כניסה לפי משתמש+IP. 5 → השהיה 2^n, 10 → נעילה 15 דק'.
CREATE TABLE IF NOT EXISTS login_attempts (
    key          TEXT PRIMARY KEY,
    failures     INTEGER NOT NULL DEFAULT 0,
    locked_until REAL,
    last_at      REAL NOT NULL
);

-- ‏#1085: דפדפן זכור — מדלג על שלב ה-TOTP ל-7 שעות.
CREATE TABLE IF NOT EXISTS trusted_browsers (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL,
    ua         TEXT NOT NULL DEFAULT ''
);

-- ‏#1085: אתגר MFA חד-פעמי אחרי סיסמה נכונה, בלי session.
CREATE TABLE IF NOT EXISTS mfa_challenges (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    expires_at REAL NOT NULL
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

-- בריאות ה-SMART וההכרעה על כל דיסק יעד בסבב שחזור (#652). שורה אחת
-- לכל (סבב, מכונה, דיסק), נדרסת בכל disk_event — התמונה החיה של
-- הבחירות שהמפעיל עשה ליד המכונה. השרת **אינו** מכריע ממנה: ההכרעה
-- על דיסק פגום נעשית בסוכן (ליד המכונה, ASCII), וכאן נשמרת לצפייה
-- (‏#380) ולריבוט-החלפה (זיהוי מחדש לפי port+serial). לכן `verdict`
-- כולל `unchecked` — לא-נבדק, לא כשל (עיקרון 5).
CREATE TABLE IF NOT EXISTS disk_events (
    session_id    TEXT NOT NULL,
    mac           TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    disk          TEXT NOT NULL,        -- שם ההתקן שהסוכן דיווח (sda)
    port          INTEGER,              -- החריץ הפיזי; ממנו הקונסולה גוזרת "דיסק N"
    serial        TEXT,
    verdict       TEXT NOT NULL,        -- ok|warn|fail|unchecked
    reason        TEXT,
    realloc       INTEGER NOT NULL DEFAULT 0,
    pending       INTEGER NOT NULL DEFAULT 0,
    uncorrectable INTEGER NOT NULL DEFAULT 0,
    crc           INTEGER NOT NULL DEFAULT 0,
    write_state   TEXT,                 -- pending|rescue|skipped|replacing|...
    decision      TEXT,                 -- NULL|replace|rescue|skip
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (session_id, mac, disk)
);

-- זיכרון כשלי כתיבה (#874): במקום סימון על הדיסק (#845, נפסל) השרת זוכר
-- **איזה דיסק (סידורי) ואיזה חריץ (מכונה+פורט)** נכשלו, ומה הקרנל אמר.
-- הסוכן שולח את שורות ה-ATA; הסיווג (cable/disk/unclassified) נעשה כאן
-- (`ata_cause.py`). ‏`cleared_at` הוא "נקה" מהקונסולה — הדיסק הוחלף או
-- הכבל תוקן; רשומה מנוקה אינה צובעת עוד. שורה אחת לכל (סבב, מכונה, יעד):
-- הדיווח חוזר כל 2 שניות, והכשל נרשם פעם אחת.
CREATE TABLE IF NOT EXISTS disk_failures (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    mac        TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    dev        TEXT NOT NULL,        -- שם ההתקן באותו אתחול (sda) -- מתחלף, לא זהות
    serial     TEXT,                 -- זהות הדיסק
    port       INTEGER,              -- החריץ (SATA N, כמו ב-hello) -- זהות המקום
    ata_port   INTEGER,              -- ata<N> של הקרנל, לקריאה מול dmesg
    at         TEXT NOT NULL,
    cause      TEXT NOT NULL,        -- cable|disk|unclassified
    error      TEXT,
    ata_log    TEXT,                 -- JSON: שורות הקרנל, הראיה
    cleared_at TEXT,
    UNIQUE (session_id, mac, dev)
);

-- זיכרון הכיווץ של דיסק המקור (#926): הפריסה המקורית של מחיצת ה-NTFS
-- שכווצה בקליטה (#87), נרשמת **לפני** הכתיבה הראשונה למקור. עד כאן היא
-- ישבה ב-tmpfs של הסוכן, ואובדן חשמל בין הכיווץ להחזרה השאיר את דיסק
-- הבנייה מכווץ בלי שאיש יודע. הסידורי הוא הזהות (הדיסק נודד); mac/dev/
-- port הם המקום, לתצוגה. `closed_at` = הוחזר (הסוכן) או "נקה" מהקונסולה.
CREATE TABLE IF NOT EXISTS shrink_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mac          TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    dev          TEXT,                 -- שם ההתקן באותו אתחול (sda) -- מתחלף, לא זהות
    serial       TEXT NOT NULL,        -- זהות הדיסק
    port         INTEGER,              -- החריץ (SATA N, כמו ב-hello)
    model        TEXT,
    image_name   TEXT,                 -- הקליטה שבמהלכה כווץ
    task_id      TEXT,
    idx          INTEGER NOT NULL,     -- הכניסה ב-GPT
    start_sector INTEGER NOT NULL,
    size_sectors INTEGER NOT NULL,     -- הגודל **המקורי**, לפני הכיווץ
    type_guid    TEXT NOT NULL,
    unique_guid  TEXT,
    attrs        TEXT,                 -- 16 ספרות הקס, כמו sgdisk -i
    name         TEXT,                 -- שם המחיצה
    ntfs_bytes   INTEGER,              -- גודל מערכת הקבצים לפני הכיווץ
    opened_at    TEXT NOT NULL,
    note         TEXT,                 -- למה הרשומה עדיין פתוחה (הטבלה הוחזרה, המתיחה נפלה)
    partitions   TEXT,                 -- #929: JSON, **כל** המחיצות שכווצו; העמודות למעלה = הראשונה
    closed_at    TEXT,
    closed_by    TEXT                  -- agent | console | <user>
);
CREATE INDEX IF NOT EXISTS shrink_records_serial ON shrink_records (serial, closed_at);

-- המלאי החומרתי מה-hello (#720, schema 2): DMI, PCI של רשת+אחסון, TPM.
-- **מגורסת**: שורה חדשה רק כשה-JSON הקנוני השתנה (server/inventory.py);
-- seen_at = מתי הגרסה הזו נראתה לראשונה. השורה האחרונה לכל MAC היא
-- המלאי הנוכחי, ולפיו מותאמות חבילות הדרייברים (server/drivers.py).
CREATE TABLE IF NOT EXISTS machine_inventory (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    mac            TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    seen_at        TEXT NOT NULL,
    inventory_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_inventory_mac ON machine_inventory (mac, id);

-- בדיקת המכונה מה-hello (#1049 שלב ב'): חשמל, שעון, מעבד, זיכרון, רשת,
-- NVMe, pstore, מפתח OEM… **מגורסת** כמו machine_inventory: שורה חדשה רק
-- כשהתוכן היציב השתנה (server/probe.py); שדות נדיפים (rtc, temp_c, מוני
-- רשת, probe_seconds) מרעננים את השורה האחרונה במקום — sampled_at זז,
-- seen_at (הגרסה נראתה לראשונה) נשאר. השורה האחרונה לכל MAC היא הדגימה
-- הנוכחית, ולפיה מחושבים השערים (verdicts).
CREATE TABLE IF NOT EXISTS machine_probe (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mac        TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    seen_at    TEXT NOT NULL,
    sampled_at TEXT NOT NULL,
    probe_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_probe_mac ON machine_probe (mac, id);

-- מפתח ה-host של dropbear בתחנה, מ-hello (#1080). **מגורסת** כמו
-- machine_inventory: שורה חדשה רק כשה-JSON הקנוני השתנה; seen_at =
-- מתי הגרסה הזו נראתה לראשונה. השורה האחרונה לכל MAC היא המפתח
-- הנוכחי, וממנה נכתב <data_dir>/ssh/known_hosts. boot_id בתוך ה-JSON
-- מבחין בין אתחול (מפתח חדש לגיטימי) לבין שינוי בתוך אותו אתחול.
CREATE TABLE IF NOT EXISTS machine_ssh_hostkey (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    mac          TEXT NOT NULL,        -- קנוני: lowercase עם נקודתיים
    seen_at      TEXT NOT NULL,
    hostkey_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_ssh_hostkey_mac ON machine_ssh_hostkey (mac, id);

-- Storage Nodes (#655, tracer 1.1 / #723): רישום המשניים שהראשי מחזיק,
-- וקבוצות האחסון שמשייכות אותם. קיים על **כל** בסיס לצורך עקביות
-- הסכימה — גם על משני, ששם הוא נשאר ריק (מוטציה מסורבת בשכבת ה-service,
-- שלב הבא בשרשרת). מזהים הם TEXT (UUID/מזהה יציב), כמו שאר הטבלאות כאן.
CREATE TABLE IF NOT EXISTS storage_node_groups (
    id       TEXT PRIMARY KEY,
    label    TEXT NOT NULL,
    sort     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS storage_nodes (
    id              TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    base_url        TEXT NOT NULL UNIQUE,
    group_id        TEXT REFERENCES storage_node_groups(id) ON DELETE SET NULL,
    -- ‏#740: ``tls_fingerprint`` יורד מלהיות חומר אמון — הוא תצוגה/ביקורת
    -- בלבד. חומר האמון הוא ``pinned_spki`` (טביעת ה-SubjectPublicKeyInfo
    -- של המשני, שאליה הראשי נצמד) ו-``client_cert_ref`` (הפניית sha256
    -- לתעודת-הלקוח של הראשי, שאותה המשני קושר לטוקן — RFC 8705).
    tls_fingerprint TEXT,
    -- ‏#740: מזהה המשני היציב, נגזר מ-SPKI התעודה שלו (לא UUID מקומי).
    node_id         TEXT,
    protocol_version TEXT,
    pinned_spki     TEXT,
    client_cert_ref TEXT,
    -- מזהה קובץ-אישורים קריא-ל-root מתחת ל-data-dir, לא האישור עצמו.
    credential_ref  TEXT NOT NULL,
    enrolled_at     TEXT NOT NULL,
    -- חותמת ולא דגל: משני מושבת אינו נמחק, ו-NULL הוא "פעיל" (כמו
    -- users.disabled_at). ``enabled_node_count`` סופר NULL בלבד.
    disabled_at     TEXT
);

-- Storage Nodes — enrollment מוקשח (#740, tracer 2.1). שתי טבלאות
-- singleton על **המשני**: רשומת אישור-האב היחיד, וחלון ה-pairing
-- המקומי. ה-PK הקבוע (=1) הוא אכיפת אב-יחיד ברמת ה-DB — "בדוק ואז
-- הכנס" לבדו אינו מספיק תחת מקביליות (עיקרון 5). ריקות על ראשי/עצמאי.
--
-- ‏storage_identity (node_id/SPKI/cert/key) ורשומת המשני-הרשום על
-- הראשי (node_id/pinned_spki/client_cert_ref) ממתינות לשכבת ה-TLS
-- של #740 — הן נגזרות מהתעודה שאותה שכבה מפיקה, ואין להן ערך בלעדיה.
CREATE TABLE IF NOT EXISTS parent_credentials (
    singleton          INTEGER PRIMARY KEY CHECK (singleton = 1),
    parent_id          TEXT NOT NULL,
    token_hash         BLOB NOT NULL,
    bound_cert_ref     TEXT NOT NULL,
    pinned_parent_spki TEXT NOT NULL,
    protocol_version   TEXT NOT NULL,
    enrolled_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_windows (
    singleton          INTEGER PRIMARY KEY CHECK (singleton = 1),
    code_hash          BLOB NOT NULL,
    code_salt          BLOB NOT NULL,
    opened_at          TEXT NOT NULL,
    expires_at         TEXT NOT NULL,
    attempts_remaining INTEGER NOT NULL
);

-- ‏#740 (tracer 2.1): זהות ה-TLS של השרת עצמו — singleton על **כל** בסיס.
-- ‏node_id נגזר מ-SPKI התעודה; ``server_cert_ref``/``server_key_ref`` הן
-- הפניות קובץ (לא החומר עצמו — המפתח הפרטי לעולם אינו ב-DB, עיקרון 6/7
-- של הדגם הנעול). ריקה עד שנוצרת זהות (‏interserver_auth.ensure_identity).
CREATE TABLE IF NOT EXISTS storage_identity (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    node_id         TEXT NOT NULL UNIQUE,
    server_spki     TEXT NOT NULL,
    server_cert_ref TEXT NOT NULL,
    server_key_ref  TEXT NOT NULL
);

-- ‏#655 v1: העברת אימג' ראשי→משני. **תיאום זמני בלבד** — לא מלאי: "האם
-- למשני יש אימג' X" נענה מהספרייה שעל הדיסק של המשני (עיקרון 3), וכאן
-- רק ההתקדמות והתוצאה של ניסיון אחד, כדי שהקונסולה תציג אותם ושכשל
-- יהיה גלוי (עיקרון 4/5: ניתוק = ``failed`` עם סיבה, לא "נתקע").
CREATE TABLE IF NOT EXISTS storage_transfers (
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES storage_nodes(id) ON DELETE CASCADE,
    image_id    TEXT NOT NULL,
    image_name  TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (
                    state IN ('queued', 'sending', 'verifying', 'done', 'failed')
                ),
    bytes_sent  INTEGER NOT NULL DEFAULT 0,
    bytes_total INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    started_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    direction   TEXT NOT NULL DEFAULT 'push'
                CHECK (direction IN ('push', 'pull'))
);

-- מיקומי אחסון לספרייה (#1066 שלב א'): תיקייה / NFS / SMB / iSCSI.
-- `--images` הוא loc_local. last_images_json הוא המטמון כשהדיסק לא נגיש
-- (אין ייצוג טבעי כקבצים אז — עיקרון 3).
CREATE TABLE IF NOT EXISTS storage_locations (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    type             TEXT NOT NULL CHECK (type IN ('local', 'nfs', 'smb', 'iscsi')),
    params_json      TEXT NOT NULL DEFAULT '{}',
    mount_point      TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (
                         state IN ('connected', 'unreachable', 'disconnected', 'unchecked')
                     ),
    state_since      TEXT NOT NULL,
    state_detail     TEXT NOT NULL DEFAULT '',
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    last_images_json TEXT NOT NULL DEFAULT '[]',
    last_df_json     TEXT
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
    # ‏#880: v1 "מהדורת שיכפול" — הפצה לכיתות ממחשב הבנייה כבויה כברירת
    # מחדל. הקוד נשאר (v2); המתג בקונסולה, מסך ההגדרות.
    "class_deploy_enabled": "false",
}


def now_iso() -> str:
    """זמן בפורמט המוסכם: ISO 8601 עם אזור זמן."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: עמודות שנוספו אחרי שכבר היו התקנות בשטח. CREATE TABLE IF NOT EXISTS
#: לא מוסיף עמודה לטבלה קיימת, ולכן ההשלמה נעשית כאן במפורש.
ADDED_COLUMNS = [
    ("groups", "sort", "INTEGER NOT NULL DEFAULT 0"),
    ("net_devices", "disks_json", "TEXT"),
    # ‏#839: סוד המוניטור שהמכונה הגרילה באתחול ודיווחה ב-hello. הפרוקסי
    # (server/monitor.py) מזדהה איתו מול 5900 של אותה מכונה, ושום תשובת
    # קונסולה אינה מחזירה אותו.
    ("net_devices", "monitor_secret", "TEXT"),
    # ‏#1077: ``hmac`` כשהסוכן תומך באתגר-תגובה. NULL = סוכן ישן; הפרוקסי
    # מסרב ולא נופל לשליחת הסוד הגולמי.
    ("net_devices", "monitor_auth", "TEXT"),
    # ‏#906: השאלה שהמכונה ממתינה עליה לאדם (‏hello עם `waiting_for`).
    # NULL = לא ממתינה; כל hello בלי השדה מנקה אותה.
    ("net_devices", "prompt", "TEXT"),
    # מספר המגירות שהוגדר לכל מחשב שכפול במסוף (#695).
    ("machines", "drawer_count",
     "INTEGER NOT NULL DEFAULT 3 CHECK (drawer_count BETWEEN 1 AND 8)"),
    # בחירת דיסקי היעד לסבב, לכל מכונה. NULL = סבב ישן (התנהגות "כל הדיסקים").
    # '[]' הוא בחירה ריקה מפורשת ואינו חוקי לסבב חדש (#695).
    ("room_rounds", "target_slots_json", "TEXT"),
    # הדיווח האחרון כפי שהגיע, יעד-יעד. סבב החדר סופר ממנו אילו
    # מגירות (לפי serial) נכתבו בהצלחה.
    ("session_members", "targets_json", "TEXT"),
    # ‏#720: תוצאת ה-staging של הדרייברים כפי שהסוכן דיווח בדיווח הסיום
    # (‏staged / no_match / skipped / failed + סיבה). NULL = השלב לא רץ
    # (סוכן ישן, או שהדיווח הסופי הגיע בלי השדה) — לא "אין התאמה".
    ("session_members", "drivers_json", "TEXT"),
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
    # ‏#1093: ערכת נושא לפי משתמש. התקנה קיימת מקבלת auto — לפי המערכת,
    # כמו לפני שהבחירה עברה מהדפדפן לשרת.
    ("users", "theme", "TEXT NOT NULL DEFAULT 'auto'"),
    # ‏#1085: החלפת סיסמה כפויה, MFA, החשבון המקומי, וביטול sessions (#1075).
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_secret", "TEXT"),
    ("users", "mfa_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_enrolled_at", "TEXT"),
    ("users", "is_builtin", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "auth_epoch", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "mfa_last_step", "INTEGER"),
    # ‏#530: האסימון שמוכיח שהפונה הוא בעל המשימה. ‏NULL בהתקנה קיימת,
    # כלומר משימות שנוצרו לפני המיגרציה **אינן ניתנות לכתיבה** —
    # ‏`claim` מסרב על `token` ריק. זו הכרעה: משימה ישנה שתיתקע עדיפה
    # על משימה ישנה שכל אחד יכול לכתוב עליה.
    ("tasks", "token", "TEXT"),
    # ‏#435: הדיווח האחרון של הקליטה כפי שהגיע, יעד-יעד. משם
    # ‏`capture_progress` שולף את `source_progress` — מכנה ההתקדמות בציר
    # הלא-דחוס (בלוקי partclone), שאין לו ייצוג בעמודות bytes_* הדחוסות.
    # ‏NULL בשורות שקדמו למיגרציה, כלומר "אין דיווח מפורט", לא "אפס".
    ("tasks", "targets_json", "TEXT"),
    # ‏#740: חומר האמון הבין-שרתי על הראשי. NULL בשורות משני שקדמו ל-#740
    # (נרשמו לפני שכבת ה-mTLS) — הן תצוגה בלבד עד שנרשמות מחדש. שורה
    # חדשה מ-``enroll_node`` ממלאת את כולן.
    ("storage_nodes", "node_id", "TEXT"),
    ("storage_nodes", "protocol_version", "TEXT"),
    ("storage_nodes", "pinned_spki", "TEXT"),
    ("storage_nodes", "client_cert_ref", "TEXT"),
    # ‏#59: בחירת המחיצה להרחבה מהקונסולה בפתיחת הסבב. NULL בכל שורה
    # קיימת — התקנה שמוגרת רואה בדיוק את הבחירה האוטומטית שהייתה לה.
    ("sessions", "expand_partition", "TEXT"),
    ("room_rounds", "expand_partition", "TEXT"),
    # ‏#715: המקור של סבב החדר. כל שורה קיימת היא סבב מספרייה — ברירת
    # המחדל אומרת בדיוק את זה, ואף סבב ישן אינו משנה משמעות.
    ("room_rounds", "source_kind", "TEXT NOT NULL DEFAULT 'library'"),
    ("room_rounds", "source_mac", "TEXT"),
    ("room_rounds", "source_disk", "TEXT"),
    ("room_rounds", "source_task_id", "TEXT"),
    ("room_rounds", "live_manifest_json", "TEXT"),
    # ‏#929: רשומת כיווץ לדיסק עם כמה מחיצות. NULL בשורה שנפתחה לפני —
    # רשומה של מחיצה אחת, והעמודות idx/start_sector/… הן היא.
    ("shrink_records", "partitions", "TEXT"),
    # ‏#1071: כיוון ההעברה. התקנה קיימת — כל השורות הן push, ברירת המחדל.
    ("storage_transfers", "direction",
     "TEXT NOT NULL DEFAULT 'push'"),
    # ‏#1017: מתי המשני ענה לאחרונה, ומה הכשל של הקריאה האחרונה (ומתי).
    # נכתבים בכל קריאה בין-שרתית (`storage_nodes.record_contact`). NULL
    # בשורה קיימת = "לא נמדד עדיין", לא "לא ענה".
    ("storage_nodes", "last_seen_at", "TEXT"),
    ("storage_nodes", "last_error", "TEXT"),
    ("storage_nodes", "last_error_at", "TEXT"),
]


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


#: העמודות שכל טבלת אחסון (#655/#723/#727) חייבת. טבלה חלקית שנוצרה
#: לפני שהסכימה התייצבה אינה ממוגרת ע"י ``CREATE TABLE IF NOT EXISTS``,
#: וגם ``ADDED_COLUMNS`` אינו מכסה אותה (חלק מעמודותיה ``NOT NULL`` בלי
#: ברירת מחדל, ו-``ALTER TABLE ADD COLUMN`` פשוט אינו יכול להוסיף כאלה).
#: לכן במקום מיגרציה שקטה — בדיקה שנכשלת בקול (#732).
_STORAGE_SCHEMA_COLUMNS = {
    "storage_node_groups": {"id", "label", "sort"},
    "storage_nodes": {"id", "label", "base_url", "group_id", "tls_fingerprint",
                      "node_id", "protocol_version", "pinned_spki",
                      "client_cert_ref", "credential_ref", "enrolled_at",
                      "disabled_at"},
    "parent_credentials": {"singleton", "parent_id", "token_hash",
                           "bound_cert_ref", "pinned_parent_spki",
                           "protocol_version", "enrolled_at"},
    "pairing_windows": {"singleton", "code_hash", "code_salt", "opened_at",
                        "expires_at", "attempts_remaining"},
    "storage_identity": {"singleton", "node_id", "server_spki",
                         "server_cert_ref", "server_key_ref"},
    "storage_transfers": {"id", "node_id", "image_id", "image_name", "state",
                          "bytes_sent", "bytes_total", "error", "started_by",
                          "created_at", "updated_at", "direction"},
    "storage_locations": {"id", "name", "type", "params_json", "mount_point",
                          "state", "state_since", "state_detail", "created_by",
                          "created_at", "last_images_json", "last_df_json"},
}


def _verify_storage_schema(conn: sqlite3.Connection) -> None:
    """נכשל בקול אם טבלת אחסון קיימת חסרה עמודות (#732).

    רץ **אחרי** ``executescript``: אם הטבלה נוצרה זה עתה היא מלאה
    ותעבור; אם קדמה לה טבלה חלקית, ``CREATE TABLE IF NOT EXISTS`` לא
    נגע בה, וכאן זה נתפס — לא בשאילתה הראשונה שמפילה 500 מול כיתה."""
    for table, expected in _STORAGE_SCHEMA_COLUMNS.items():
        columns = {row["name"]
                   for row in conn.execute(f"PRAGMA table_info({table})")}
        if not columns:
            continue                 # לא קיימת — executescript נכשל קודם אם צריך
        missing = expected - columns
        if missing:
            raise SchemaError(
                f"טבלת '{table}' קיימת אך חסרה עמודות: "
                f"{', '.join(sorted(missing))}. שחזרו את קובץ הנתונים מגיבוי "
                f"או מחקו את הטבלה החלקית — אתחול לא ממגר אותה בשקט.")


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
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS users_username_nocase"
        " ON users (username COLLATE NOCASE)"
    )
    # ‏#1123: שני מיקומים על נקודת עיגון אחת = שתי שורות fstab על יעד אחד.
    # נכשל בקול על DB ותיק עם כפילות — אין "פינוי" אוטומטי למיקומי אחסון.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS storage_locations_mount_point"
        " ON storage_locations (mount_point)"
    )
    _verify_storage_schema(conn)
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

    def executemany(self, sql: str, seq_of_parameters) -> sqlite3.Cursor:
        return self.connection.executemany(sql, seq_of_parameters)

    def executescript(self, sql: str) -> sqlite3.Cursor:
        return self.connection.executescript(sql)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    @property
    def in_transaction(self) -> bool:
        """האם לתהליכון הזה יש טרנזאקציה פתוחה — כלומר נעילת כתיבה בידו.

        ‏``sqlite3.Connection`` מפרסם את זה, וכל מי שמקבל ``conn`` צריך
        לשאול בלי לדעת אם קיבל חיבור גולמי או את העטיפה (‏`_settle`).
        """
        return self.connection.in_transaction

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


def _settle(conn) -> None:
    """סוגר טרנזאקציה שהקורא עוד מחזיק, **לפני** ההמתנה ל-``_write_lock``.

    זהו סדר הנעילות של #457 באכיפה. כתיבה שהצליחה משאירה את הטרנזאקציה
    פתוחה — ‏`update_one` עושה ``rollback`` רק כשלא תאם שורה — ולכן
    הקורא ממשיך ל-`journal` כשנעילת הכתיבה של sqlite עדיין בידו. שם הוא
    ממתין ל-`_write_lock`, ובאותו רגע כותב אחר יכול להחזיק את
    ‏`_write_lock` ולהמתין לנעילת sqlite שלו. שניהם ממתינים זה לזה עד
    ש-``busy_timeout`` שובר, והצד שנשבר מדווח ``database is locked``.
    הכשל **מתחזה לעומס** — בדיוק כמו ב-#54.

    ‏``commit`` ולא ``rollback``: הקורא כתב בכוונה, וכל אתר קריאה כאן
    ממילא עושה ``commit`` בשורה הבאה. גם ``writing`` שבתוך הבלוק היה
    מקמט את הטרנזאקציה הזו — ההבדל היחיד הוא **מתי**, ו"מתי" הוא כל
    הבאג: אחרי הנעילה זו נעילה משולבת, לפניה זו לא.

    לכן זה יושב כאן, במקום אחד, ולא כשורה שצריך לזכור בכל אתר קריאה —
    אותה צורה שנבחרה ב-#54 עבור `update_one`.

    **מה זה לא עושה:** אטומיות של "כתיבה + שורת יומן" אינה קיימת כאן
    ממילא. ‏`_write_lock` אינו ``RLock`` (‏`test_the_write_lock_is_not_reentrant`),
    ולכן קריאה ל-`journal` **בתוך** בלוק כתיבה נתקעת לנצח. שתי
    הטרנזאקציות היו נפרדות בכל מקרה; ‏`_settle` רק מפריד אותן במפורש.
    """
    if conn.in_transaction:
        conn.commit()


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
                        disks_json: str | None, now: datetime,
                        monitor_secret: str | None = None,
                        monitor_auth: str | None = None,
                        prompt: str | None = None) -> bool:
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
    if monitor_secret is not None and monitor_secret != row["monitor_secret"]:
        return False
    if monitor_secret is not None and (row["monitor_auth"] or None) != (monitor_auth or None):
        return False
    if prompt != row["prompt"]:   # #906: גם המעבר שאלה→אין-שאלה נכתב
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
    monitor_secret: str | None = None,
    monitor_auth: str | None = None,
    prompt: str | None = None,
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

    ‏#906: ``prompt`` הוא השאלה שהמכונה ממתינה עליה לאדם — ובניגוד
    לשאר השדות הוא **נכתב תמיד**, גם כ-NULL: ‏hello בלי השדה אומר
    "כבר לא ממתינה", ו-COALESCE היה משאיר שאלה שכבר נענתה על המסך.
    """
    now = datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT ip, last_seen, disks_json, monitor_secret, monitor_auth, prompt"
        " FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    if row is not None and _net_seen_unchanged(row, ip, disks_json, now,
                                               monitor_secret, monitor_auth,
                                               prompt):
        return

    ts = now.isoformat(timespec="seconds")
    # ‏`_write_lock` — זו הכתיבה שכיתה שלמה דורכת עליה בו-זמנית, והיא
    # חייבת תור הוגן ולא מרוץ על נעילת sqlite (#272). ‏`writing` —
    # כתיבה שנכשלה חייבת להשאיר חיבור נקי; ראו שם. ‏`_settle` — סדר
    # הנעילות (#457): לא ממתינים כאן עם נעילת כתיבה ביד.
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO net_devices (mac, ip, first_seen, last_seen, disks_json,"
            " monitor_secret, monitor_auth, prompt) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (mac) DO UPDATE SET ip = COALESCE(excluded.ip, ip),"
            " last_seen = ?, disks_json = COALESCE(excluded.disks_json, disks_json),"
            " monitor_secret = COALESCE(excluded.monitor_secret, monitor_secret),"
            " monitor_auth = CASE WHEN excluded.monitor_secret IS NOT NULL"
            " THEN excluded.monitor_auth ELSE monitor_auth END,"
            " prompt = excluded.prompt",
            (mac, ip, ts, ts, disks_json, monitor_secret, monitor_auth, prompt, ts),
        )


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    """מסלול הכתיבה של **כל** מסכי ההגדרות — DHCP, כתובות, מתג SSH,
    רשימת התיקיות. ‏`_write_lock` ו-`writing` מאותה סיבה כמו ב-`net_seen`
    (#313): כתיבה שנכשלה כאן משאירה את החיבור בטרנזאקציה, ומשם הקונסולה
    מפסיקה לשמור עד אתחול השרת. ‏`_settle` — סדר הנעילות של #457."""
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def set_settings(conn: sqlite3.Connection, pairs: dict[str, str]) -> None:
    """כותב כמה הגדרות ב**טרנזאקציה אחת** — הכל-או-כלום (#732).

    כמה קריאות ל-`set_setting` הן כמה טרנזאקציות: אם השנייה נכשלת
    (דיסק מלא, נעילה) הראשונה כבר נכתבה, והתוצאה היא מצב מעורב שנקרא
    כתצורה תקינה (עיקרון 5). כאן שתי הכתיבות באותו בלוק ``writing``,
    וכישלון מגלגל את שתיהן. אותו מסלול נעילות של `set_setting`."""
    _settle(conn)
    with _write_lock, writing(conn):
        for key, value in pairs.items():
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

    ‏`_settle` הוא סדר הנעילות של #457, וכאן הוא נדרש יותר מבכל אתר
    אחר: ‏`journal` הוא מה שנקרא **מיד אחרי** כתיבה מותנית שהצליחה, וגם
    מה שתהליכון הרקע של השידור כותב — כלומר שני הצדדים של הנעילה
    המשולבת נפגשים כאן.
    """
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO journal (ts, user, event, detail) VALUES (?, ?, ?, ?)",
            (now_iso(), user, event, detail),
        )
