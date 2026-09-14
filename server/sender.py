"""מנוע השידור — מפעיל udp-sender כשסבב עובר ל"משדר".

מה שעובר בקו מוגדר בסעיף 7 בממשקים: שידור אחד לכל קובץ מחיצה, בייט-בייט
כפי שהוא שמור, בסדר שבמניפסט. המנוע לא נוגע בתוכן — הוא רק מזרים קבצים.

udpcast עוצר בין שידורים עד שכל המקבלים מוכנים, ולכן המחיצות רצות
בזו אחר זו ולא במקביל.

הרצת התהליכים מוזרקת (`runner`) כדי שהבדיקות יריצו את כל הלוגיקה בלי
udpcast מותקן.

שני הפורטים (`portbase` ו-`portbase+1`) נבדקים **לפני** השידור: תהליך
יתום שמחזיק אותם מפיל את ההפצה הבאה, והאבחון נראה כמו תקלת רשת (#156).
הבדיקה המקדימה היא צילום רגע ולא ערובה — ולכן מי שנכנס בחלון שאחריה
נתפס בבדיקה שנייה **בנתיב הכישלון** (`_port_verdict`, ‏#202).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .images import streamed_partitions

log = logging.getLogger("imagectl.sender")

#: הפורטים שההפצה האמיתית מקצה בוילן ההפצה — `portbase` ו-`portbase+1`.
#: הערך הזה קבוע, והוא מה שחבילת הטסטים מוודאת שאינה נוגעת בו (#156).
PRODUCTION_PORTBASE = 9000
#: ברירת המחדל בפועל. חבילת הטסטים דורסת אותה ב-portbase גבוה ואקראי
#: (`hygiene.assign_test_portbase`), כדי שיתום של ריצה שנקטעה לא יוכל
#: להתנגש בהפצה אמיתית — גם אם איש לא ינקה אותו לעולם.
DEFAULT_PORTBASE = PRODUCTION_PORTBASE
#: כמה להמתין למקבלים שטרם התייצבו לפני שמשדרים בכל זאת. udpcast מחכה
#: לכל אחד בנפרד, ומכונה שנתקעה באתחול לא אמורה לעצור כיתה שלמה.
DEFAULT_MAX_WAIT = 120
#: כמה להמתין ל**מקבל הראשון** לפני שמוותרים. ‏`--max-wait` מתחיל לספור רק
#: מהמקבל הראשון, ולכן שידור שאיש לא הצטרף אליו אינו נכשל ואינו מסתיים —
#: הוא נתקע, והמסך מול הכיתה פשוט לא זז (#341). ‏`--start-timeout` הוא
#: התקרה על ההמתנה הזאת. שלוש דקות: מכונה שכבר אמרה hello צריכה פחות מזה
#: כדי להתייצב כמקבל, והמפעיל אינו נשאר מול מסך קפוא יותר מזה.
DEFAULT_START_TIMEOUT = 180
#: אחרי כמה בקשות ACK ללא מענה udpcast מוותר על מקבל וממשיך בלעדיו.
#: **בלי הדגל הזה udpcast מגדיר 200**.
#:
#: ‏11/09/2026 (הכרעת נדב): מאמצים את הגישה של FOG/Clonezilla —
#: **"dynamic bitrate": להאט למקבל האיטי ולא להפיל אותו.** אימג' גדול
#: (win-build 67GB דחוס) נפל ב-~90% בשידור, ואימג' קטן (20GB) הצליח:
#: ‏back-pressure בזנב גרם ל-udp-sender להפיל מקבלים איטיים-אך-חיים אחרי
#: ‏15 בקשות ACK, המקבל שהופל המתין לנצח, ‏`--receive-timeout` שלו נורה
#: והזרם נקטע. הפתרון הוא לא להפיל את המקבל האיטי — כלומר תקרת נטישה
#: גבוהה, שמשאירה אותו בקבוצה ומאיטה את כל הזרם אליו.
#:
#: **מחיר מודע (#437):** מקבל שבאמת *מת* (שותק) מקפיא כעת את החדר בערך
#: ‏190 שניות לפני שהוא נזרק, במקום כ-15 קודם — כי אין דרך ל-udpcast
#: להבחין בין "איטי-אך-חי" ל"מת". ההכרעה של נדב היא שכשל *מלא* של
#: אימג' הדגל עדיף שלא יקרה, גם במחיר קפיאה של דקות כשמכונה קורסת.
#: ‏**הערך ניתן לכוונון דרך `IMAGECTL_RETRIES_UNTIL_DROP`** כדי שהמפעיל
#: יוכל להחזיר אותו אם הקפיאה הזו תתברר כגרועה מהתקלה שהיא מונעת.
#: **לא `--async`** — ויתור על ACK פירושו דאטגרם אבוד שהופך לדיסק פגום,
#: וזה בדיוק מה שהפרוטוקול הזה קיים כדי למנוע (עיקרון 4).
DEFAULT_RETRIES_UNTIL_DROP = 200


def _env_retries_until_drop() -> int:
    """התקרה מ-`IMAGECTL_RETRIES_UNTIL_DROP`, או ברירת המחדל.

    ערך env לא-מספרי נופל אחורה עם אזהרה ולא מפיל את הפעלת השרת: מספר
    שגוי בהגדרה אינו סיבה שהשידור כולו לא יעלה.
    """
    raw = os.environ.get("IMAGECTL_RETRIES_UNTIL_DROP")
    if raw is None:
        return DEFAULT_RETRIES_UNTIL_DROP
    try:
        value = int(raw)
    except ValueError:
        log.warning("IMAGECTL_RETRIES_UNTIL_DROP=%r אינו מספר — נופלים ל-%d",
                    raw, DEFAULT_RETRIES_UNTIL_DROP)
        return DEFAULT_RETRIES_UNTIL_DROP
    # ‏udp-sender סופר את הערך כ-int חתום: `0`/שלילי אינם "אל תזרוק" אלא
    # **זריקה כמעט מיידית** של המקבל הראשון שמפספס ACK — כלומר בדיוק כשל
    # ה-90% שהערך הזה קיים כדי למנוע, מוסווה כ"כוונון". מספר קטן מ-1 הוא
    # הגדרה שגויה, לא בקשה — נופלים לברירת המחדל עם אזהרה (עיקרון 5).
    if value < 1:
        log.warning("IMAGECTL_RETRIES_UNTIL_DROP=%r < 1 — זורק מקבל מיד; נופלים ל-%d",
                    value, DEFAULT_RETRIES_UNTIL_DROP)
        return DEFAULT_RETRIES_UNTIL_DROP
    return value
#: udpcast חוסם SIGTERM לכל אורך doTransfer — המתנה ארוכה ל-TERM אינה
#: ראיה שהוא ימות, היא זמן שמתבזבז לפני SIGKILL (#439).
STOP_TERM_WAIT = 0.5
STOP_KILL_WAIT = 1.0


#: הפלט של udp-sender — הבסיס לשם הלוג. ‏Starting transfer: בכל הקובץ
#: היא הראיה שהשידור באמת התחיל (#438); הזנב נכנס להודעת שגיאה אחרת.
#: **לוג נפרד לכל מחיצה** (`partition_log`) ולא קובץ יחיד: עד כאן
#: ‏run_process פתח את הקובץ הזה ב-"wb" לכל מחיצה, וכל מחיצה מחקה את לוג
#: קודמתה — כשל במחיצה השנייה בלע את ה-Starting transfer של הראשונה,
#: והראיה מדוע נפל השידור הגדול נמחקה במחיצה שאחריה (באג ראיות).
SENDER_LOG = Path(tempfile.gettempdir()) / "imagectl-sender.log"
#: נמדד מול udpcast 20120424: בנתיב start-timeout השורה אינה נכתבת,
#: והתהליך יוצא 0 בכל זאת (#438).
TRANSFER_STARTED = "Starting transfer:"


def partition_log(part_file: str | Path) -> Path:
    """הלוג של מחיצה בודדת, נגזר מהדיסק ושם הקובץ, ליד `SENDER_LOG`.

    כל מחיצה כותבת לקובץ משלה, ולכן הראיה של מחיצה קודמת שורדת את
    השידור של הבאה. השם נגזר מ-`SENDER_LOG` כדי שבדיקה שמנתבת אותו
    מחדש (למשל ל-tmp) תנתב גם את הלוגים הפר-מחיצתיים איתו.
    """
    path = Path(part_file)
    name = "-".join(part for part in path.parts[-2:] if part not in ("/", "\\"))
    return SENDER_LOG.with_name(f"{SENDER_LOG.stem}-{name}{SENDER_LOG.suffix}")


def run_process(cmd: list[str]) -> subprocess.Popen:
    # לא stdout=PIPE: אף אחד לא קורא את הצינור, udp-sender מדפיס התקדמות
    # בלי הפסקה, וכשהחוצץ (~64KB) מתמלא — באמצע המחיצה הגדולה — התהליך
    # נחסם על הפלט של עצמו והשידור קופא, והמקבלים מתים בזה אחר זה (#22).
    # אותו לקח בדיוק כמו שרת הסימולציה ב-#12: פלט של תהליך ארוך → קובץ.
    # לוג פר-מחיצה נגזר מ---file, כדי שראיית מחיצה קודמת לא תימחק.
    with partition_log(cmd[cmd.index("--file") + 1]).open("wb") as handle:
        return subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)


#: טבלאות ה-UDP של הקרנל — מקור האמת למי מחזיק פורט בלינוקס.
UDP_TABLES = (Path("/proc/net/udp"), Path("/proc/net/udp6"))

#: פורט תפוס שלא הצלחנו לשייך לתהליך (סוקט של משתמש אחר). "לא ידוע מי"
#: אינו "אין אף אחד" — ולכן זה מחזיק, לא פנוי.
UNKNOWN_HOLDER = -1


def _inodes_on_port(port: int) -> set[str] | None:
    """ה-inodes של סוקטי UDP שקשורים ל-`port`; ‏None כשאין טבלה לקרוא."""
    inodes: set[str] = set()
    read_any = False
    for table in UDP_TABLES:
        try:
            rows = table.read_text().splitlines()[1:]
        except FileNotFoundError:        # אין /proc, או קרנל בלי IPv6
            continue
        except OSError as exc:
            # הטבלה קיימת ולא הצלחנו לקרוא אותה. דילוג עליה היה מחזיר
            # את התשובה החלקית של הטבלה השנייה — כלומר "פנוי" על סמך
            # חצי בדיקה (עיקרון 5).
            raise OSError(f"קריאת {table} נכשלה: {exc}") from exc
        read_any = True
        for row in rows:
            fields = row.split()
            if not fields:                   # שורה ריקה אינה שורת סוקט
                continue
            if len(fields) <= 9:
                # שורה קטועה היא שורה שלא בדקנו, בדיוק כמו שורה שלא
                # ידענו לפענח. "קצרה מכדי לעניין" הוא בדיוק הניחוש
                # שעיקרון 5 אוסר.
                raise OSError(f"שורה קטועה ב-{table}: {row!r}")
            try:
                local_port = int(fields[1].rsplit(":", 1)[-1], 16)
            except ValueError as exc:
                # שורה שאיננו יודעים לקרוא היא שורה שלא בדקנו, והיא
                # יכולה להיות בדיוק זו שמחזיקה את הפורט. "לא הבנו" אינו
                # "אין שם כלום" — ולכן חריגה, לא `continue` (עיקרון 5).
                raise OSError(f"שורה לא צפויה ב-{table}: {row!r}") from exc
            if local_port == port:
                inodes.add(fields[9])
    return inodes if read_any else None


def _pids_holding(inodes: set[str]) -> list[int]:
    """התהליכים שמחזיקים את ה-inodes האלה; ריק = לא הצלחנו לזהות אותם."""
    wanted = {f"socket:[{inode}]" for inode in inodes}
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            handles = list((entry / "fd").iterdir())
        except OSError:                  # תהליך של משתמש אחר, או שמת בינתיים
            continue
        for handle in handles:
            try:
                link = os.readlink(handle)
            except OSError:
                continue
            if link in wanted:
                pids.append(int(entry.name))
                break
    return sorted(pids)


def port_holders(port: int) -> list[int]:
    """מי מחזיק את `port`. רשימה ריקה = **נבדק ונמצא פנוי**.

    ‏`OSError` פירושו *לא הצלחנו לבדוק*, וזה אינו "פנוי" (עיקרון 5):
    מי שקורא חייב לטפל בשני המצבים בנפרד.

    שלוש תשובות ולא שתיים: פנוי, תפוס בידי PIDים ידועים, ותפוס בידי מי
    שלא הצלחנו לזהות (`UNKNOWN_HOLDER`) — סוקט של משתמש אחר, ש-`/proc`
    שלו סגור בפנינו. השלישית אינה "פנוי" ואינה שם של תהליך, והיא נאמרת
    ככזאת ולא מקופלת לאחת מהאחרות.

    **ובלי טבלת קרנל אין תשובה בכלל.** ‏bind מוצלח אינו תחליף: סוקט
    בדיקה חייב להיקשר לכתובת אחת, ולכן הוא מפספס מי שתפס את הפורט על
    כרטיס אחר — ו-bind לכל הכרטיסים הוא בדיוק מה שאסור לפתוח כאן. השרת
    רץ על דביאן, שם `/proc/net/udp` תמיד קיים; במקום שאין בו טבלה גם
    אין udpcast, ולכן "לא הצלחנו לבדוק" הוא התשובה הנכונה ולא מחיר.
    """
    inodes = _inodes_on_port(port)
    if inodes is None:
        raise OSError(
            f"אין טבלת UDP של הקרנל ({', '.join(str(t) for t in UDP_TABLES)}),"
            f" ואי אפשר לדעת מי מחזיק את פורט {port}"
        )
    if not inodes:
        return []
    return _pids_holding(inodes) or [UNKNOWN_HOLDER]


def holder_names(pids: list[int]) -> str:
    """"udp-sender (PID 1234)" — שם התהליך ולא רק מספרו.

    ‏PID לבדו אינו אומר למפעיל **מה לעשות**; השם אומר לו אם מולו סבב
    קודם שלא נסגר או כלי אחר לגמרי. ‏`UNKNOWN_HOLDER` אינו PID, ולכן
    אינו מקבל שם ואינו נספר כאן — מי שקורא אומר "לא זיהינו" בעצמו.
    """
    names = []
    for pid in pids:
        if pid == UNKNOWN_HOLDER:
            continue
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:            # התהליך מת בינתיים, או /proc סגור בפנינו
            comm = ""
        names.append(f"{comm} (PID {pid})" if comm else f"PID {pid}")
    return ", ".join(names)


def sender_log_tail(log_path: Path, limit: int = 400) -> str:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def transfer_started_in_log(log_path: Path) -> bool | None:
    """True התחיל · False נקרא ואין · None לא ניתן לקרוא.

    כל הקובץ, לא הזנב: Progress דוחף את השורה מחוץ ל-400 תווים.
    קובץ חסר אינו "איש לא הצטרף" (#438, עיקרון 5).
    """
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    return TRANSFER_STARTED in text


def _reaped(process, seconds: float) -> bool:
    """True רק כשיש ראיה שהילד מת. היעדר בדיקה אינו ראיה (#439)."""
    deadline = time.monotonic() + seconds
    while True:
        if process.poll() is not None:
            pid = getattr(process, "pid", None)
            root = Path("/proc")
            if pid is None or not root.is_dir():
                return True
            try:
                return not (root / str(pid)).is_dir()
            except OSError:
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


@dataclass
class SendState:
    session_id: str
    image_id: str
    total: int = 0
    index: int = 0                  # איזו מחיצה משודרת כרגע (1-based)
    file: str = ""
    state: str = "starting"         # starting / sending / done / failed / stopped
    error: str | None = None
    commands: list[list[str]] = field(default_factory=list)


class SenderEngine:
    """מריץ שידור אחד בכל רגע — כמו שהאפיון מבטיח על סבבים."""

    def __init__(
        self,
        library,
        runner: Callable[[list[str]], subprocess.Popen] = run_process,
        portbase: int | None = None,
        interface: str | None = None,
        max_wait: int = DEFAULT_MAX_WAIT,
        start_timeout: float = DEFAULT_START_TIMEOUT,
        retries_until_drop: int | None = None,
        max_bitrate: str | None = None,
        on_event: Callable[[str, str], None] | None = None,
    ):
        self.library = library
        self.runner = runner
        # ‏None ולא `DEFAULT_PORTBASE` כברירת מחדל בחתימה: ברירת מחדל
        # נקשרת בהגדרת הפונקציה, וחבילת הטסטים דורסת את המודול אחריה.
        self.portbase = DEFAULT_PORTBASE if portbase is None else portbase
        self.interface = interface
        self.max_wait = max_wait
        self.start_timeout = start_timeout
        # ‏None ולא הקבוע בחתימה: כך `IMAGECTL_RETRIES_UNTIL_DROP` נקרא
        # בזמן ההרצה (כמו max_bitrate), ומי שמעביר ערך מפורש עוקף אותו.
        self.retries_until_drop = (
            retries_until_drop if retries_until_drop is not None
            else _env_retries_until_drop()
        )
        # ריסון קצב: מקבל חסום על כתיבה לדיסק גם שותק בפרוטוקול, וה-sender
        # זורק אותו ("Dropped by server"). ברשת 1G הכבל מרסן מעצמו; ברשת
        # מהירה (מעבדת VM, ‏10G) חייבים רסן מפורש בקצב שהדיסקים מעכלים (#24).
        self.max_bitrate = max_bitrate or os.environ.get("IMAGECTL_MAX_BITRATE")
        self.on_event = on_event or (lambda event, detail: None)
        self._lock = threading.Lock()
        self._state: SendState | None = None
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- API ------------------------------------------------------------------

    def start(self, session: dict) -> None:
        """נקרא כשסבב עובר ל-running. חוזר מיד; השידור רץ ברקע."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                log.warning("sender already running; ignoring start of %s", session["id"])
                return
            self._stop.clear()
            self._state = SendState(
                session_id=session["id"], image_id=session["image_id"]
            )
            self._thread = threading.Thread(
                target=self._run, args=(session,), daemon=True,
                name=f"sender-{session['id']}",
            )
            self._thread.start()

    def stop(self, session_id: str | None = None) -> int | None:
        """הורג את udp-sender וקורא בחזרה שהוא מת (#439).

        ‏None = יש ראיה שהתהליך איננו. מספר = עדיין חי, ואינו הצלחה
        — גם כשלא הצלחנו לבדוק. TERM לבד משאיר יתום: udpcast חוסם
        אותו ב-doTransfer. SIGKILL ואז קריאה בחזרה.

        ‏`session_id` מגן מפני קריאת-סגירה ישנה (#773): thread שסוגר
        את session A ומתעכב לפני ה-callback אינו רשאי להרוג את השידור
        של session B שכבר התחיל. הסגירה פועלת רק כש-`session_id` הוא
        `None` (סגירה מפורשת) או תואם את ה-session הרץ; אחרת no-op.
        """
        with self._lock:
            # השוואה תחת אותה נעילה שמגינה על `_state`: בין קריאת המצב
            # להדלקת ה-stop אסור ש-`start` של session אחר יחליף אותו.
            current = self._state.session_id if self._state is not None else None
            if session_id is not None and session_id != current:
                log.info("sender: ignoring stale stop of %s (current is %s)",
                         session_id, current)
                return None
            self._stop.set()
            process = self._process
            if self._state is not None and self._state.state in ("starting", "sending"):
                self._state.state = "stopped"
        if process is None or process.poll() is not None:
            return None
        process.terminate()
        if _reaped(process, STOP_TERM_WAIT):
            return None
        try:
            process.kill()
        except OSError:
            pass
        if _reaped(process, STOP_KILL_WAIT):
            return None
        pid = getattr(process, "pid", None)
        log.error("sender: udp-sender still alive after SIGKILL (PID %s)", pid)
        return pid if pid is not None else -1

    def status(self) -> dict | None:
        with self._lock:
            if self._state is None:
                return None
            s = self._state
            return {
                "session_id": s.session_id, "image_id": s.image_id,
                "partition": s.index, "partitions": s.total,
                "file": s.file, "state": s.state, "error": s.error,
            }

    # --- הלולאה ---------------------------------------------------------------

    def command_for(self, path: Path, receivers: int) -> list[str]:
        cmd = [
            "udp-sender",
            "--portbase", str(self.portbase),
            "--min-receivers", str(max(1, receivers)),
            "--max-wait", str(self.max_wait),
            # בלי זה udpcast ממתין למקבל הראשון בלי גבול (#341).
            "--start-timeout", str(self.start_timeout),
            # מקבל שמת אינו שולח CMD_DISCONNECT — הוא פשוט מפסיק לענות,
            # וכל שאר החדר ממתין לו. זו התקרה על ההמתנה הזאת (#437).
            "--retries-until-drop", str(self.retries_until_drop),
            "--nokbd",
            "--file", str(path),
        ]
        if self.interface:
            cmd[1:1] = ["--interface", self.interface]
        if self.max_bitrate:
            cmd[1:1] = ["--max-bitrate", self.max_bitrate]
        return cmd

    def _fail(self, message: str) -> None:
        with self._lock:
            if self._state is not None:
                self._state.state = "failed"
                self._state.error = message
        log.error("sender: %s", message)
        self.on_event("send_failed", message)

    def _ports_are_free(self) -> bool:
        """שני הפורטים של udpcast פנויים — בראיה חיובית, לפני השידור.

        יתום שמחזיק את `portbase+1` מפיל את ההפצה הבאה, ו-udp-sender
        נכשל בהודעה אטומה שנראית כמו תקלת רשת (#156). כאן זה הופך
        לשורה אחת שנוקבת בפורט וב-PID. ובדיקה שלא הצליחה לרוץ אינה
        "הפורט פנוי" — היא כישלון בפני עצמה (עיקרון 5).

        **מה ש-udp-sender עושה בפועל (#342):** נמדד ב-`ss -ulnp`
        במעבדה — הוא נקשר רק ל-`portbase+1`, לא ל-`portbase`. מי
        שתופס את `portbase` כדי לשחזר התנגשות (למשל בעבודה על #202)
        לא יראה התנגשות בכלל; זה קרה בניסיון הראשון. **הבדיקה על
        שני הפורטים נשארת** — היא מגנה מפני שינוי עתידי ב-udpcast.
        לשחזור: לתפוס את `portbase+1`.

        **מה שהבדיקה אינה:** היא צילום רגע. הסוקט של הבדיקה נסגר לפני
        ש-`udp-sender` נקשר, ובחלון שביניהם תהליך אחר יכול לתפוס את
        הפורט — ולא ניתן לסגור את החלון בהחזקת סוקט, כי `udp-sender`
        צריך את הפורט לעצמו. היא מקטינה סיכון ומסבירה כישלון; היא אינה
        ערובה. וכשלא הצלחנו לזהות **מי** מחזיק — זה נאמר כך, ולא
        מוצג כשם של תהליך.
        """
        for port in (self.portbase, self.portbase + 1):
            try:
                holders = port_holders(port)
            except Exception as exc:              # noqa: BLE001 — ראו למטה
                # לא רק `OSError`: שורה לא צפויה ב-`/proc` היא `ValueError`,
                # ומניית `/proc` יכולה להפתיע אחרת. חריגה שתברח מכאן
                # תמות בתהליכון הרקע — והסבב יישאר תקוע ב-"starting",
                # כלומר "לא הצלחנו לבדוק" ייראה על המסך כמו "עדיין עובד".
                self._fail(f"לא הצלחנו לבדוק אם פורט {port} פנוי, ולכן"
                           f" לא משדרים: {exc}")
                return False
            if holders:
                self._fail(f"פורט {port} תפוס על ידי "
                           f"{holder_names(holders) or 'תהליך שאין הרשאה לזהות'}"
                           " — השידור לא יצא לדרך")
                return False
        return True

    def _port_verdict(self) -> str:
        """מי מחזיק את הפורטים **אחרי** ש-udp-sender נכשל (#202).

        הבדיקה המקדימה היא צילום רגע, והחלון שבינה לבין הקשירה של
        udp-sender אינו ניתן לסגירה — הוא צריך את הפורט לעצמו. מי
        שנכנס בחלון נשאר בלי אבחון, ודווקא הנדיר הוא מה שקורה מול
        כיתה. זו **הודעה בלבד**: לא ריפוי ולא ניסיון חוזר.

        שלוש תשובות ולא שתיים, ואף אחת אינה מקופלת לאחרת: נמצא מחזיק ·
        נבדק ונמצא פנוי, ולכן הסיבה אחרת ואינה ידועה · הבדיקה עצמה לא
        רצה (עיקרון 5). ‏udp-sender נכשל גם מסיבות אחרות, ולכן אסור
        להמציא מחזיק רק כדי שיהיה מה לכתוב.
        """
        busy, unchecked = [], []
        for port in (self.portbase, self.portbase + 1):
            try:
                holders = port_holders(port)
            except Exception as exc:          # noqa: BLE001 — כמו בבדיקה המקדימה
                unchecked.append(f"פורט {port}: {exc}")
                continue
            if holders:
                busy.append(f"פורט {port} תפוס על ידי "
                            f"{holder_names(holders) or 'תהליך שאין הרשאה לזהות'}")
        if busy:
            # מה שלא נבדק נאמר גם כאן: מחזיק שנמצא על פורט אחד אינו
            # תשובה על הפורט שאותו לא הצלחנו לבדוק.
            note = f" (ובנוסף: {'; '.join(unchecked)})" if unchecked else ""
            return "; ".join(busy) + note + " — יש לעצור אותו ולשדר שוב"
        if unchecked:
            return ("לא הצלחנו לבדוק מי מחזיק את הפורטים כעת ("
                    + "; ".join(unchecked) + ")")
        return ("הפורטים נבדקו כעת ושניהם פנויים — הכישלון אינו התנגשות"
                " על פורט, והסיבה אינה ידועה")

    def _run(self, session: dict) -> None:
        if not self._ports_are_free():
            return
        manifest = self.library.get(session["image_id"])
        if manifest is None:
            self._fail(f"אימג' {session['image_id']} לא נמצא בספרייה")
            return
        partitions = streamed_partitions(manifest)
        receivers = max(1, int(session.get("joined") or 1))
        with self._lock:
            self._state.total = len(partitions)
        self.on_event(
            "send_start",
            f'{session["id"]} {session["image_id"]} partitions={len(partitions)}',
        )

        for number, part in enumerate(partitions, start=1):
            if self._stop.is_set():
                self.on_event("send_stopped", session["id"])
                return
            path = Path(manifest["_dir"]) / part["file"]
            if not path.is_file():
                self._fail(f"קובץ מחיצה חסר: {part['file']}")
                return
            with self._lock:
                self._state.index = number
                self._state.file = part["file"]
                self._state.state = "sending"
            cmd = self.command_for(path, receivers)
            log_path = partition_log(path)
            log.info("sending partition %s/%s: %s", number, len(partitions), part["file"])
            try:
                process = self.runner(cmd)
            except OSError as exc:
                self._fail(f"udp-sender לא רץ: {exc}")
                return
            with self._lock:
                self._process = process
                self._state.commands.append(cmd)
            code = process.wait()
            with self._lock:
                self._process = None
            if self._stop.is_set():
                self.on_event("send_stopped", session["id"])
                return
            if code != 0:
                _tail = sender_log_tail(log_path)
                self._fail(f"udp-sender נכשל על {part['file']} (קוד {code})"
                           + (f": {_tail}" if _tail else "")
                           + f" · {self._port_verdict()}")
                return
            # קוד 0 אינו "השידור הצליח": udp-sender בנתיב start-timeout
            # יוצא 0 בלי Starting transfer ובלי מקבל אחד (#438). הראיה
            # החיובית היא השורה בלוג. בלי קובץ אין ראיה — וזה לא הצלחה.
            started = transfer_started_in_log(log_path)
            if started is None:
                self._fail(
                    f"לא הצלחנו לקרוא את יומן השידור אחרי {part['file']},"
                    " ולכן אין לדעת אם מישהו הצטרף — השידור לא נחשב תקין"
                )
                return
            if not started:
                self._fail(
                    f"אף מחשב לא הצטרף לשידור תוך {self.start_timeout}"
                    f" שניות — udp-sender סיים בלי לשדר את {part['file']}"
                    " (הלוג בלי Starting transfer). זה אינו אימג' פגום:"
                    " יש לבדוק כבל רשת, אתחול PXE ורישום MAC"
                )
                return

        with self._lock:
            self._state.state = "done"
        self.on_event("send_done", session["id"])
