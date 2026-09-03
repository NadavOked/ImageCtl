"""מנוע השידור — מפעיל udp-sender כשסבב עובר ל"משדר".

מה שעובר בקו מוגדר בסעיף 7 בממשקים: שידור אחד לכל קובץ מחיצה, בייט-בייט
כפי שהוא שמור, בסדר שבמניפסט. המנוע לא נוגע בתוכן — הוא רק מזרים קבצים.

udpcast עוצר בין שידורים עד שכל המקבלים מוכנים, ולכן המחיצות רצות
בזו אחר זו ולא במקביל.

הרצת התהליכים מוזרקת (`runner`) כדי שהבדיקות יריצו את כל הלוגיקה בלי
udpcast מותקן.

שני הפורטים (`portbase` ו-`portbase+1`) נבדקים **לפני** השידור: תהליך
יתום שמחזיק אותם מפיל את ההפצה הבאה, והאבחון נראה כמו תקלת רשת (#156).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
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


#: הפלט של udp-sender מהשידור האחרון — הזנב שלו נכנס להודעת שגיאה.
SENDER_LOG = Path(tempfile.gettempdir()) / "imagectl-sender.log"


def run_process(cmd: list[str]) -> subprocess.Popen:
    # לא stdout=PIPE: אף אחד לא קורא את הצינור, udp-sender מדפיס התקדמות
    # בלי הפסקה, וכשהחוצץ (~64KB) מתמלא — באמצע המחיצה הגדולה — התהליך
    # נחסם על הפלט של עצמו והשידור קופא, והמקבלים מתים בזה אחר זה (#22).
    # אותו לקח בדיוק כמו שרת הסימולציה ב-#12: פלט של תהליך ארוך → קובץ.
    with SENDER_LOG.open("wb") as handle:
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


def sender_log_tail(limit: int = 400) -> str:
    try:
        text = SENDER_LOG.read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


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

    def stop(self, session_id: str | None = None) -> None:
        """עצירה מיידית — סגירת סבב, או כיבוי השרת."""
        self._stop.set()
        with self._lock:
            process = self._process
            if self._state is not None and self._state.state in ("starting", "sending"):
                self._state.state = "stopped"
        if process is not None and process.poll() is None:
            process.terminate()

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
                who = ", ".join(f"PID {pid}" for pid in holders
                                if pid != UNKNOWN_HOLDER)
                self._fail(f"פורט {port} תפוס על ידי "
                           f"{who or 'תהליך שאין הרשאה לזהות'}"
                           " — השידור לא יצא לדרך")
                return False
        return True

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
                _tail = sender_log_tail()
                self._fail(f"udp-sender נכשל על {part['file']} (קוד {code})"
                           + (f": {_tail}" if _tail else ""))
                return

        with self._lock:
            self._state.state = "done"
        self.on_event("send_done", session["id"])
