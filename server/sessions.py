"""סבבי הפצה — הסבב חי בשרת, לא במחשב שפתח אותו.

עקרונות מהאפיון (סעיף 13.3):
- לעולם לא יותר מ**שידור** פעיל אחד בו-זמנית — נאכף כאן, לא בקונסולה.
- hello של מכונה מהקבוצה בזמן סבב פתוח הוא ההצטרפות; אין endpoint נפרד.
- השידור מתחיל כשהגיע המספר שהוצהר, או שעברו X שניות מהמצטרף האחרון
  (כל מצטרף מאפס את הטיימר), או "התחל עכשיו" בקונסולה.
- מי שדיווח done לא מקבל את הסבב שוב — אחרת ישחזר בלולאה אחרי אתחול.

שני זרמים, שני כללים (#60). המגבלה "אחד בכל המערכת" נובעת מ-`udp-sender`
היחיד, ולכן היא חלה על מה שמשתמש בו — סבב כיתה וגל חדר שיכפולים. משיכת
יוניקאסט של תחנה בודדת (‏HTTP מול `/api/v1/images/...`) אינה נוגעת בו
ואינה מתחרה על כתובת המולטיקאסט: כמה כאלה רצות יחד, וגם בזמן שידור.
היא בכל זאת session — כדי שדיווחי ההתקדמות, המסך והיומן יראו אותה
בדיוק כמו כל עבודה אחרת. שרת שעובד לא ייראה פנוי.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from typing import Callable

from .db import get_setting, journal, now_iso, update_one, writing

#: התקרה של NetBIOS. שם מחשב ארוך ממנה אינו נכתב, ו-`hostname.sh` דוחה
#: אותו — אבל שם, בקצה, אחרי שהאימג' כבר נכתב על הדיסק (#98).
HOSTNAME_MAX = 15

#: הסיומת הקצרה ביותר ש-`registry.normalize_suffix` מייצרת: ‏"5" → "05".
#: היא הרצפה, ולכן קידומת שאינה מותירה לה מקום פסולה בוודאות — גם
#: כשהקבוצה עדיין ריקה ואי אפשר למדוד את הסיומת הארוכה בפועל.
SUFFIX_MIN = 2

#: מה ששם מחשב מורכב ממנו. ‏`_` **אינו** כאן, והוא בדיוק התו שנולד
#: מ-`label.replace(" ", "_")` ב-`console_api.py` ושורד לתוך מזהה הקבוצה.
_PREFIX_OK = re.compile(r"[A-Z0-9-]+")


#: שידור מולטיקאסט — udp-sender אחד, ולכן חריץ אחד בכל המערכת.
MULTICAST = "multicast"
#: משיכת HTTP של תחנה בודדת — בלי חריץ, כמה במקביל.
UNICAST = "unicast"
KINDS = (MULTICAST, UNICAST)

#: קידומת שם המחשב אינה רלוונטית למשיכה בודדת (אין סבב לגזור ממנו שם),
#: אבל השדה אינו יכול להיות ריק — הוא מזהה את הסבב ביומן ובמסך.
PULL_PREFIX = "PULL"

#: מה שהמפעיל רואה כשהחריץ כבר תפוס. אותה הודעה בדיוק לשני המסלולים —
#: הבדיקה המקדימה שתפסה את המצב, והאינדקס הייחודי שתפס את מי שחמק
#: מהבדיקה (‏#103, ‏#104). מבחינת מי שקורא את המסך זה אותו מצב, ולכן
#: המחרוזת אחת ולא שתיים שהתיישנו זו מול זו.
TAKEN = {
    MULTICAST: "כבר יש סבב פעיל — לעולם לא יותר מאחד בו-זמנית",
    UNICAST: "התחנה הזו כבר מושכת אימג' — יש לחכות לסיום",
}


class SessionError(ValueError):
    pass


class SessionSuperseded(SessionError):
    """הסבב שביקשנו להחליף כבר אינו פעיל — תהליכון אחר הקדים אותנו.

    זה **אינו** כישלון אלא התוצאה המכוונת של האטומיות: שני תהליכונים
    שהגיעו לאותו גל גמור, ורק אחד מהם ממשיך אותו (#177). מי שצריך
    להבחין תופס אותו במפורש; מי שלא — יורש מ-`SessionError`, ולכן
    הקונסולה וה-API מתייחסים אליו כאל כל שגיאת סבב אחרת.
    """


class SessionStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        now_fn: Callable[[], float] = time.time,
        on_running: Callable[[dict], None] | None = None,
        on_closed: Callable[[str], None] | None = None,
        on_opened: Callable[[str, str | None, list[str] | None], None] | None = None,
    ):
        """`on_running` / `on_closed` הם החיבור למנוע השידור, ו-`on_opened`
        ל-Wake-on-LAN (מקבל את הקבוצה ואת המכונה שפתחה, אם ידועה).

        הם מוזרקים ולא מיובאים, כדי שניהול הסבב יישאר לוגיקה טהורה שאפשר
        לבדוק בלי udpcast או רשת.
        """
        self.conn = conn
        self.now = now_fn
        self.on_running = on_running or (lambda session: None)
        self.on_closed = on_closed or (lambda session_id: None)
        self.on_opened = on_opened or (lambda group_id, opener_mac, roster: None)

    # --- פתיחה וסגירה --------------------------------------------------------

    def open(
        self,
        group_id: str,
        image_id: str,
        prefix: str,
        expected_clients: int,
        opened_by: str,
        wait_seconds: int | None = None,
        opener_mac: str | None = None,
        roster: list[str] | None = None,
        kind: str = MULTICAST,
        replaces: str | None = None,
    ) -> str:
        """`roster` — בחירת מחשבים מתוך הקבוצה. None = כל הקבוצה.
        רק מי שברשימה מוער ומצטרף; השאר עולים מהדיסק (עיקרון 1).

        `kind` — הזרם. מולטיקאסט תופס את החריץ היחיד; יוניקאסט לא (#60),
        והוא נפתח כבר רץ: אין למי לחכות, התחנה מושכת בעצמה.

        `replaces` — הסבב שהפתיחה הזו באה **במקומו**. סגירה ואז פתיחה
        הן שתי כתיבות, ובין שתיהן החריץ פנוי — וזה שורש #177 בשני
        מובנים. שני תהליכונים שסגרו את אותו גל ניסו שניהם לפתוח את הבא,
        והשני קיבל `TAKEN`: החריגה ברחה מ-`tick` ומסלול ה-hello שהריץ
        אותו החזיר 500. **הגל הבא כן נפתח** באותו מקרה — הפותח שניצח
        פתח אותו — אבל אותו חלון פתוח גם ל**פותח שלישי**: סבב כיתה
        שנפתח מהקונסולה בין הסגירה לפתיחה תופס את החריץ, ואז הגל הבא
        באמת אינו נפתח והחדר נתקע. כאן שתי הכתיבות הן טרנזאקציה אחת,
        ולכן אין רגע כזה בכלל.

        הסגירה מותנית במצב, והיא **התביעה**: סבב שכבר אינו פעיל פירושו
        שתהליכון אחר הקדים אותנו והוא זה שפותח את הבא — `SessionSuperseded`.
        ראיה חיובית מי ממשיך, ולא היעדר סימן (עיקרון 5).
        """
        if kind not in KINDS:
            raise SessionError(f"סוג זרם לא מוכר: {kind}")
        if replaces is not None and kind != MULTICAST:
            raise SessionError("החלפת סבב קיימת בשידור בלבד")
        if kind == MULTICAST and replaces is None:
            self._claim_broadcast_slot(opened_by)
        if roster is not None:
            registered = {
                row["mac"] for row in self.conn.execute(
                    "SELECT mac FROM machines WHERE group_id = ?", (group_id,)
                )
            }
            roster = sorted(set(roster))
            unknown = [m for m in roster if m not in registered]
            if unknown:
                raise SessionError(f"מחשב שאינו רשום בקבוצה: {unknown[0]}")
            if not roster:
                raise SessionError("לא נבחר אף מחשב")
        if expected_clients < 1:
            raise SessionError("מספר המחשבים חייב להיות חיובי")
        prefix = prefix.strip().upper()
        if not prefix:
            raise SessionError("קידומת ריקה")
        # הבדיקה כאן ולא ב-`hostname.sh` בלבד: זה המקום היחיד שכל
        # הפותחים עוברים דרכו, והוא **לפני** שהאימג' נכתב. בקצה הכשל
        # שקט — `name_this_machine` אינה קטלנית, דיווח ההתקדמות אינו
        # נושא את תוצאת השם, והקונסולה מציגה `done` על כיתה שלמה
        # שעלתה עם השם שהיה באימג' (#98, אותה צורה כמו #62 ו-#89).
        if not _PREFIX_OK.fullmatch(prefix):
            raise SessionError(
                f"קידומת {prefix!r} אינה יכולה להיות שם מחשב — "
                "מותרים אותיות אנגליות, ספרות ומקף בלבד")
        longest = self.conn.execute(
            "SELECT MAX(LENGTH(suffix)) AS n FROM machines WHERE group_id = ?",
            (group_id,),
        ).fetchone()["n"]
        # קבוצה ריקה: אין מה למדוד, ולכן נמדדת הרצפה. זה אינו "לא בדקנו
        # ולכן עבר" — קידומת שאינה מותירה מקום גם לסיומת הקצרה ביותר
        # פסולה בכל מקרה, וארוכה יותר תיתפס כשהמכונה תירשם.
        room = longest if longest else SUFFIX_MIN
        if len(prefix) + 1 + room > HOSTNAME_MAX:
            raise SessionError(
                f"קידומת {prefix!r} ארוכה מדי: עם מקף וסיומת בת {room} תווים "
                f"היא חורגת מ-{HOSTNAME_MAX} התווים של שם מחשב")
        if wait_seconds is None:
            wait_seconds = int(get_setting(self.conn, "session_wait_seconds") or 300)
        session_id = "ses_" + secrets.token_hex(4)
        state, started = ("open", None) if kind == MULTICAST else ("running", now_iso())
        try:
            if replaces is not None:
                # ‏BEGIN IMMEDIATE תופס את נעילת הכתיבה **לפני** הקריאה־
                # כתיבה, ולכן הסגירה וה-INSERT הם פעולה אחת מבחינת כל
                # תהליכון אחר — ומי שהפסיד ממתין ב-`busy_timeout` ואז
                # מגלה שהגל כבר נסגר, במקום לפתוח גל שני.
                self.conn.execute("BEGIN IMMEDIATE")
                # ‏`update_one` הוא אותו דפוס בדיוק כמו ב-`_transition`,
                # והוא גם זה שמשחרר את נעילת הכתיבה כשלא תאמה שורה (#54).
                if not update_one(
                    self.conn,
                    "UPDATE sessions SET state = 'closed', closed_at = ?"
                    " WHERE id = ? AND state != 'closed'",
                    (now_iso(), replaces),
                ):
                    raise SessionSuperseded(
                        f"הסבב {replaces} כבר נסגר — תהליכון אחר ממשיך אותו")
            self.conn.execute(
                "INSERT INTO sessions (id, group_id, image_id, prefix,"
                " expected_clients, wait_seconds, state, opened_by, created_at,"
                " last_join_at, roster_json, kind, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, group_id, image_id, prefix, expected_clients,
                    wait_seconds, state, opened_by, now_iso(), self.now(),
                    json.dumps(roster) if roster is not None else None, kind, started,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            # ההכרעה האמיתית על החריץ היא כאן ולא בבדיקה שלמעלה: בין
            # הקריאה לכתיבה רצות עוד שאילתות, ותהליכון אחר יכול לכתוב
            # בתוכן (‏#103, ‏#104). האינדקס החלקי הייחודי ב-`db.py` הוא
            # שמכריע, וה-rollback משחרר את נעילת הכתיבה של ה-INSERT
            # שנדחה — בדיוק מהסיבה של `db.update_one` (#54).
            self.conn.rollback()
            raise SessionError(TAKEN[kind]) from exc
        except Exception:     # noqa: BLE001 — לא בולעים, רק משחררים
            # כל כשל אחר בתוך הטרנזאקציה המפורשת היה משאיר **נעילת
            # כתיבה יתומה**, וזה בדיוק #54: השאר ממתינים `busy_timeout`
            # שלם ומקבלים "database is locked", כשל שנראה כמו עומס.
            # השחרור כאן, ולא כשורה שצריך לזכור בכל אתר קריאה.
            self.conn.rollback()
            raise
        if replaces is not None:
            # אחרי ה-commit, ובאותו נוסח של `close`: הסגירה נרשמת ביומן
            # ומנוע השידור מקבל את ההודעה. ‏`replaces` הוא מולטיקאסט
            # בהגדרה, ולכן אין כאן את התנאי של `close`.
            journal(self.conn, "session_close", replaces, opened_by)
            self.on_closed(replaces)
        if kind == UNICAST:
            journal(self.conn, "pull_open",
                    f"{session_id} {group_id} {image_id} mac={roster[0]}", opened_by)
            return session_id
        journal(self.conn, "session_open",
                f"{session_id} {group_id} {image_id} prefix={prefix}"
                + (f" machines={len(roster)}" if roster is not None else ""), opened_by)
        # ההערה נשלחת בפתיחה ולא בהתחלה: המחשבים צריכים לעלות ולהצטרף
        # לפני שהשידור מתחיל, לא אחריו. משיכה בודדת מעירה רק את עצמה,
        # והיא כבר ערה — היא זו שביקשה.
        self.on_opened(group_id, opener_mac, roster)
        return session_id

    def _claim_broadcast_slot(self, opened_by: str) -> None:
        """החריץ היחיד במערכת: יש `udp-sender` אחד, ולכן שידור אחד.

        משיכת יוניקאסט אינה עוברת כאן — היא לא נוגעת בו (#60).

        זו אינה האכיפה אלא הבדיקה המקדימה: היא מפנה סבב גמור ונותנת
        הודעה מסודרת. מי שמגיע לכאן בו-זמנית עם פותח אחר נעצר ב-INSERT,
        על האינדקס הייחודי (#103).
        """
        current = self.active_broadcast()
        if current is None:
            return
        if not self._spent(current):
            raise SessionError(TAKEN[MULTICAST])
        # סבב גמור נשאר "רץ" רק כדי שהסיכום יוצג — הוא לא חוסם את
        # הבא לנצח (#35); הפתיחה החדשה היא הרגע הטבעי לפנות אותו.
        self.close(current["id"], opened_by, event="session_autoclose")

    @staticmethod
    def in_roster(session: sqlite3.Row, mac: str) -> bool:
        """האם הסבב מיועד למכונה הזו. סבב בלי רשימה מיועד לכל הקבוצה."""
        raw = session["roster_json"]
        return raw is None or mac in json.loads(raw)

    def start_now(self, session_id: str, user: str,
                  event: str = "session_start_manual") -> None:
        self._transition(session_id, "open", "running", user, event)

    def start_auto(self, session_id: str) -> None:
        """התחלה אוטומטית — מ-hello, ממבט-על של הקונסולה או מ-tick של
        חדר השיכפולים. שלושת המסלולים חיים בתהליכונים שונים ויכולים
        להגיע לאותו סבב באותו רגע; המעבר עצמו הוא UPDATE מותנה, ולכן
        אחד מנצח והשני מקבל rowcount=0.

        המפסיד אינו כישלון — אבל גם לא מניחים לו. הוא קורא את השורה
        וממשיך רק אם היא באמת running: ראיה חיובית שהמעבר קרה, ולא
        היעדר סימן. מעבר שלא קרה, ושהסבב עדיין אינו רץ, נכשל כרגיל.
        """
        try:
            self._transition(session_id, "open", "running", "", "session_start_auto")
        except SessionError:
            row = self.conn.execute(
                "SELECT state FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None or row["state"] != "running":
                raise

    def close(self, session_id: str, user: str,
              event: str = "session_close") -> bool:
        """סוגר סבב, ומחזיר **האם הקריאה הזו היא שסגרה אותו**.

        ה-UPDATE מותנה במצב, ולכן שני תהליכונים שהגיעו לאותו סבב באותו
        רגע מקבלים תשובות שונות: אחד `True` והשני `False`. זו ראיה
        חיובית מי מהם ממשיך — ובחדר השיכפולים היא מה שמונע שני סיומי
        סבב שכל אחד מהם כותב את השורה התחתונה (#177). מי שרק רוצה
        שהסבב ייסגר מתעלם מהערך, כמו קודם.
        """
        row = self.conn.execute(
            "SELECT state, kind FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise SessionError("סבב לא קיים")
        if not update_one(
            self.conn,
            "UPDATE sessions SET state = 'closed', closed_at = ?"
            " WHERE id = ? AND state != 'closed'",
            (now_iso(), session_id),
        ):
            return False
        self.conn.commit()
        journal(self.conn, event, session_id, user)
        # רק שידור מודיע למנוע. ‏`SenderEngine.stop` מתעלם ממזהה הסבב
        # שהוא מקבל, ולכן סגירת משיכת יוניקאסט הייתה הורגת את השידור
        # של הכיתה — סבב שאין לו שום קשר אליה.
        if row["kind"] == MULTICAST:
            self.on_closed(session_id)
        return True

    def _spent(self, session: sqlite3.Row) -> bool:
        """סבב שהתחיל וכל מי שהצטרף סיים או נכשל — תצוגת סיכום בלבד (#35).
        גלי חדר השיכפולים מוחרגים: ‏room.py מנהל את מחזורם בעצמו, ופינוי
        מבחוץ היה שומט את חשבון הכוננים של הסבב המצטבר."""
        if session["state"] != "running":
            return False
        group = self.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (session["group_id"],)
        ).fetchone()
        if group is not None and group["role"] == "cloner":
            return False
        members = self.members(session["id"])
        return bool(members) and all(
            m["done"] or m["state"] == "failed" for m in members
        )

    def _transition(self, session_id: str, from_state: str, to_state: str, user: str, event: str) -> None:
        changed = update_one(
            self.conn,
            "UPDATE sessions SET state = ?, started_at = ? WHERE id = ? AND state = ?",
            (to_state, now_iso(), session_id, from_state),
        )
        if not changed:
            raise SessionError(f"הסבב אינו במצב {from_state}")
        self.conn.commit()
        journal(self.conn, event, session_id, user)
        if to_state == "running":
            row = self.conn.execute(
                "SELECT id, image_id, kind FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row["kind"] != MULTICAST:
                return          # משיכת יוניקאסט אינה מפעילה udp-sender
            self.on_running({
                "id": row["id"],
                "image_id": row["image_id"],
                "joined": self.joined_count(session_id),
            })

    # --- שליפות --------------------------------------------------------------

    def active_broadcast(self) -> sqlite3.Row | None:
        """השידור הפעיל — החריץ היחיד במערכת (open/running), אם יש."""
        return self.conn.execute(
            "SELECT * FROM sessions WHERE state IN ('open', 'running')"
            " AND kind = ? ORDER BY created_at LIMIT 1", (MULTICAST,)
        ).fetchone()

    def active(self) -> sqlite3.Row | None:
        """"הסבב" שהמסכים מציגים ושמכונה מצטרפת אליו — זה השידור.

        משיכת יוניקאסט אינה "הסבב" של אף אחד: היא של תחנה אחת, ואין
        למה להצטרף אליה. מי שרוצה אותה שואל `active_pulls` במפורש.
        """
        return self.active_broadcast()

    def active_pulls(self) -> list[sqlite3.Row]:
        """המשיכות שרצות עכשיו. אין תקרה — ואם תהיה, היא תוצהר (#60)."""
        return self.conn.execute(
            "SELECT * FROM sessions WHERE state IN ('open', 'running')"
            " AND kind = ? ORDER BY created_at", (UNICAST,)
        ).fetchall()

    def active_for_group(self, group_id: str) -> sqlite3.Row | None:
        row = self.active()
        if row is not None and row["group_id"] == group_id:
            return row
        return None

    def joined_count(self, session_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) AS n FROM session_members WHERE session_id = ?",
            (session_id,),
        ).fetchone()["n"]

    def is_member(self, session_id: str, mac: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM session_members WHERE session_id = ? AND mac = ?",
                (session_id, mac),
            ).fetchone()
            is not None
        )

    def member_done(self, session_id: str, mac: str) -> bool:
        row = self.conn.execute(
            "SELECT done FROM session_members WHERE session_id = ? AND mac = ?",
            (session_id, mac),
        ).fetchone()
        return bool(row and row["done"])

    def members(self, session_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM session_members WHERE session_id = ? ORDER BY mac",
            (session_id,),
        ).fetchall()

    # --- הצטרפות והבשלה ------------------------------------------------------

    def record_hello(self, session: sqlite3.Row, mac: str) -> None:
        """hello בזמן סבב פתוח = הצטרפות. מאפס את טיימר ההמתנה."""
        if session["state"] != "open" or self.is_member(session["id"], mac):
            return
        # שתי הכתיבות הן טרנזאקציה אחת: הצטרפות שנרשמה בלי לאפס את
        # הטיימר היא סבב שיוצא מוקדם מדי. ‏`writing` מוודא שכישלון של
        # השנייה אינו משאיר את הנעילה של הראשונה יתומה (#290).
        with writing(self.conn):
            self.conn.execute(
                "INSERT INTO session_members (session_id, mac, updated_at)"
                " VALUES (?, ?, ?)",
                (session["id"], mac, now_iso()),
            )
            self.conn.execute(
                "UPDATE sessions SET last_join_at = ? WHERE id = ?",
                (self.now(), session["id"]),
            )

    def starts_in_seconds(self, session: sqlite3.Row) -> int:
        deadline = session["last_join_at"] + session["wait_seconds"]
        return max(0, int(deadline - self.now()))

    def view(self, session: sqlite3.Row, library) -> dict:
        """הסבב כפי שהמסך מציג אותו. המימוש ב-`session_view.py`."""
        from . import session_view      # noqa: PLC0415 — נמנע ממעגל ייבוא
        return session_view.build(self, session, library)

    def maybe_start(self, session: sqlite3.Row) -> sqlite3.Row:
        """מיישם את תנאי ההתחלה הכפול. מוחזרת השורה העדכנית.

        המספר שהוצהר הושג, או שהטיימר מהמצטרף האחרון פקע (ובתנאי שיש
        לפחות מצטרף אחד — סבב ריק לא מתחיל מעצמו, הוא נסגר מהקונסולה).
        """
        if session["state"] != "open":
            return session
        group = self.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (session["group_id"],)
        ).fetchone()
        if group is not None and group["role"] == "cloner":
            # גל של חדר השיכפולים: המוכנות נמדדת בכוננים, לא במחשבים,
            # ואין טיימר. room.py מחליט מתי הוא יוצא.
            return session
        joined = self.joined_count(session["id"])
        ripe = joined >= session["expected_clients"] or (
            joined > 0 and self.starts_in_seconds(session) == 0
        )
        if ripe:
            self.start_auto(session["id"])
            return self.conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session["id"],)
            ).fetchone()
        return session
