"""ImageCtl — מחולל grub.cfg.

הרכיב הזה הוא נתב, לא תפריט. הוא מקבל את תשובת השרת (ממשק 3) ומחזיר טקסט
grub.cfg שעושה אחד משני דברים: מעלה את הסוכן, או עושה chain לדיסק המקומי.

ברירת המחדל היא תמיד הדיסק המקומי. כל מצב שאינו חד-משמעי — MAC לא מוכר,
schema לא מוכר, JSON פגום, חריגה בקוד — נופל לשם.

המודול הזה טהור: אין בו גישה לרשת, לדיסק או ל-DB. הוא מקבל dict ומחזיר str.
מי שמארח אותו (השרת) אחראי לעשות את ה-lookup ולהגיש את התוצאה ב-HTTP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

__all__ = [
    "GrubConfig",
    "Decision",
    "LOCAL",
    "AGENT",
    "LOCAL_BOOT_PATHS",
    "SUPPORTED_SCHEMA",
    "MENU_NO_TIMEOUT",
    "ROLES_WITH_NORMAL_ENTRY",
    "normalize_mac",
    "decide",
    "render",
    "render_local_only",
    "render_bootstrap",
]

# ---------------------------------------------------------------------------
# קבועים
# ---------------------------------------------------------------------------

#: הגרסה היחידה של ממשק 3 שהמחולל יודע לפרש. schema גבוה יותר = שינוי שובר,
#: ולכן מצב לא ברור, ולכן דיסק מקומי.
SUPPORTED_SCHEMA = 1

#: ‏GRUB: ערך שלילי ב-timeout = התפריט ממתין לאדם ללא הגבלת זמן. זה מה
#: שנדב הכריע ב-#140 עבור כל מכונה רשומה שאין לה משימה — ראו decide().
MENU_NO_TIMEOUT = -1

#: מצבי סבב שבהם יש למה להעלות את הסוכן.
JOINABLE_SESSION_STATES = frozenset({"open", "running"})

#: התפקידים שערך ה-ImageCtl היחיד שלהם הוא הסוכן במצב **רגיל** (#144).
#: כל תפקיד אחר מקבל את הערך במצב `recovery`, וזה לא שרירותי — זו טבלת
#: ההחלטה של הסוכן עצמו (`agent/lib/decide.sh`) קרואה מהצד השני:
#:
#:   build      → `build_console`  — מסך הקליטה/ההפצה, הדרך היחידה אליו
#:                מאתחול קר (#29). ‏recovery היה מסתיר אותו.
#:   classroom  → `local`, כלומר `die_local` ו-`reboot -f`. ערך "רגיל"
#:                בתחנת כיתה בלי משימה הוא **הערך היחיד שאינו עושה דבר**,
#:                ומאז #140 (תפריט בלי טיימר) הוא גם לולאה: המכונה
#:                מאתחלת וחוזרת לאותו תפריט. זה מה שקרה ללנובו ב-#144.
#:   כל השאר    → `local` באותה שורת ברירת מחדל, ולכן אותו טיפול.
#:
#: מכונה עם משימה או סבב פתוח אינה מגיעה לכאן בכלל — היא במסלול AGENT.
ROLES_WITH_NORMAL_ENTRY = frozenset({"build"})

_MAC_CANONICAL = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_MAC_STRIP = re.compile(r"[^0-9a-fA-F]")

# GRUB לא מרנדר עברית — אין RTL ואין פונט מלא. כל טקסט שמוצג במסך האתחול
# חייב להיות אנגלית ASCII בלבד.
# הראשון לערכים בשורה אחת (מוחק גם שורות חדשות), השני לקובץ שלם.
_ASCII_SAFE = re.compile(r"[^\x20-\x7e]")
_ASCII_SAFE_MULTILINE = re.compile(r"[^\x20-\x7e\n]")

# ---------------------------------------------------------------------------
# תצורה
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrubConfig:
    """כתובות ותזמונים. לא חלק מאף ממשק — תצורת פריסה מקומית.

    server_base חייב להיות http (לא https): ה-GRUB החתום של דביאן נבנה בלי
    תמיכת TLS, וה-http module שלו מדבר HTTP פשוט בלבד.
    """

    server_base: str
    kernel_path: str = "/boot/vmlinuz"
    initrd_path: str = "/boot/initrd.img"

    #: אין כאן יותר שדות תזמון. ‏hidden_timeout (2 שניות ל-ESC) ו-menu_timeout
    #: (‏15 שניות בתפריט הגלוי) הוסרו ב-#140: מכונה רשומה מקבלת תפריט גלוי
    #: שממתין לאדם ללא הגבלה, ומכונה שאיננו משרתים עולה מהדיסק מיד. שני
    #: הערכים נגזרים מההחלטה עצמה ואין מה לכוונן בהם.
    #: פרמטרים נוספים לשורת הפקודה של הקרנל, אם צריך לחומרה חריגה.
    extra_cmdline: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        parts = urlsplit(self.server_base)
        if parts.scheme != "http":
            raise ValueError(
                f"server_base must be http:// (GRUB has no TLS), got {self.server_base!r}"
            )
        if not parts.netloc:
            raise ValueError(f"server_base has no host: {self.server_base!r}")
        if _ASCII_SAFE.search(self.server_base):
            raise ValueError("server_base must be plain ASCII")

    @property
    def grub_host(self) -> str:
        """ה-netloc בתחביר ההתקן של GRUB: ‎(http,10.99.0.10:8080)."""
        return urlsplit(self.server_base).netloc

    @property
    def base(self) -> str:
        """כתובת הבסיס בלי / בסוף — נמסרת לסוכן ב-imagectl.server."""
        return self.server_base.rstrip("/")


# ---------------------------------------------------------------------------
# החלטה
# ---------------------------------------------------------------------------

LOCAL = "local"
AGENT = "agent"


@dataclass(frozen=True)
class Decision:
    """מה יוגש, ולמה.

    יש כאן שני שדות הסבר במכוון. code הוא אסימון קבוע מרשימה סגורה, והוא
    היחיד שנכתב לתוך הקובץ. reason הוא טקסט חופשי ליומן השרת בלבד ויכול
    להכיל ערכים מתשובת השרת. הפרדה זו היא שמונעת מפרטי משימה לזלוג למסך
    האתחול — ראו את הבדיקה test_agent_config_leaks_nothing_from_the_answer.
    """

    action: str
    code: str
    reason: str = ""
    show_menu: bool = False
    offer_recovery: bool = False
    offer_agent: bool = False
    recovery_mode: bool = False
    extra: dict = field(default_factory=dict)


def normalize_mac(raw: object) -> str | None:
    """מנרמל MAC לתבנית הקנונית של סעיף 6 בממשקים: lowercase עם נקודתיים.

    מקבל את כל הווריאציות שמופיעות באפיון (‎00:00:5E / 00-00-5e / 00005e).
    מחזיר None אם זה לא MAC תקין — והקורא חייב להתייחס לזה כמצב לא ברור.
    """
    if not isinstance(raw, str):
        return None
    hex_only = _MAC_STRIP.sub("", raw).lower()
    if len(hex_only) != 12:
        return None
    canonical = ":".join(hex_only[i : i + 2] for i in range(0, 12, 2))
    return canonical if _MAC_CANONICAL.match(canonical) else None


def decide(answer: object) -> Decision:
    """טבלת ההחלטה. מקבלת תשובת שרת (ממשק 3), מחזירה מה להגיש.

    הסדר קובע — כל בדיקה שנכשלת מפילה לדיסק המקומי ולא ממשיכה הלאה.
    """
    if not isinstance(answer, dict):
        return Decision(LOCAL, "bad-answer", "answer is not an object")

    schema = answer.get("schema", SUPPORTED_SCHEMA)
    if not isinstance(schema, int) or schema > SUPPORTED_SCHEMA:
        return Decision(LOCAL, "bad-schema", f"unsupported contract schema {schema!r}")

    # known: false → כלום. המחשב עולה מהדיסק כאילו לא קרה דבר.
    # ההתראה על MAC לא מוכר עולה בקונסולה, לא במסך של התלמיד.
    if answer.get("known") is not True:
        return Decision(LOCAL, "unregistered", "mac not registered")

    role = answer.get("role")
    if not isinstance(role, str):
        role = "unknown"

    # שומר לולאת האתחול (‏#75). השרת ספר שהוא כבר שלח את המכונה הזו
    # לסוכן שוב ושוב עבור אותה עבודה, והיא חזרה בכל פעם — כלומר היא
    # נכשלת לפני שהיא מספיקה לדווח. הסבב אינו מוסר לה יותר, והיא
    # עולה מהדיסק המקומי כמו כל מצב לא ברור אחר. הדגל אינו קופץ מעל
    # התפקיד: מחשב הבנייה עדיין מקבל את התפריט הגלוי שלו (‏#29), ותחנה
    # עדיין מקבלת בו את ערך השחזור — היא בדיוק המכונה שטכנאי צריך
    # להיכנס אליה, וזו הדרך היחידה לתקן מכונה תקועה בשטח.
    guarded = answer.get("boot_guard") == "exhausted"

    if not guarded and _is_actionable_task(answer.get("task")):
        return Decision(AGENT, "task-assigned", f"task assigned (role={role})")

    if not guarded and _is_joinable_session(answer.get("session")):
        state = answer["session"].get("state")
        return Decision(AGENT, "session-joinable", f"session {state} (role={role})")

    if role == "cloner":
        # למחשב שיכפול אין לעולם מערכת מקומית — המגירות שלו הן הסחורה,
        # לא דיסק אתחול. ברירת המחדל שלו היא מסך ההמתנה של הסוכן
        # (טבלת ההחלטות: cloner בלי סבב = wait_poll). בלי זה, מכונה
        # שעולה קר כשהמגירות שלה לא "טריות" נשלחת לדיסק ריק וכבה (#17).
        # מאותה סיבה גם השומר של #75 אינו חל כאן: אין לאן להפיל אותו,
        # ולכן השרת אינו סופר אותו מלכתחילה (ראו server/bootguard.py).
        return Decision(AGENT, "cloner-wait", f"cloner waits on the agent (role={role})")

    # task: null וגם session: null — ברירת המחדל של הממשק.
    #
    # ‏#140 (הכרעת נדב, 2026-08-30): כל מכונה רשומה שאין לה משימה מגיעה
    # למסך בחירה **גלוי** בין הדיסק המקומי ל-ImageCtl — תחנת כיתה בדיוק
    # כמו מחשב הבנייה, בלי ESC ובלי טיימר. עד כאן היה הבדל *הצגה* בין
    # התפקידים (‏ROLES_WITH_VISIBLE_MENU); הוא בוטל, והתפקיד אינו קובע
    # יותר **אם** יש תפריט. מ-#144 הוא קובע **מה** יש בו: אותם שני ערכים
    # בדיוק לכל תפקיד, כשהשני נבחר לפי מה ששימושי למכונה הזו.
    #
    # המחיר הוצג לנדב ואושרר: אתחול רגיל של תחנה כבר **אינו** עולה מהדיסק
    # תוך שניות — הוא ממתין לאדם. ‏set default=local נשאר, אבל בלי טיימר
    # הוא רק הערך המסומן ולא נבחר לעולם. מה שעדיין מגן על עיקרון 1 הוא
    # שני השומרים שלמעלה (‏unregistered ו-bad-schema), ששניהם עדיין עולים
    # מהדיסק בלי תפריט, וכיבוי השרת — שם PXE נכשל בקושחה והמכונה לא
    # מגיעה לקובץ הזה בכלל.
    #
    # ‏#144: **ערך ImageCtl אחד** בנוסף לדיסק, ולא שניים. עד כאן הוצגו גם
    # `imagectl` וגם `imagectl-recovery` זה לצד זה, בלי שום דבר על המסך
    # שמסביר את ההבדל — ונדב בחר בלנובו (`role=classroom`) דווקא את זה
    # שמחזיר `local` ומאתחל. איזה מהשניים שימושי נקבע בידי התפקיד, וזה
    # ידוע לשרת; ראו ROLES_WITH_NORMAL_ENTRY. בדיוק אחד מהדגלים דלוק.
    normal = role in ROLES_WITH_NORMAL_ENTRY
    code = "boot-loop-guard" if guarded else "no-task"
    return Decision(
        LOCAL,
        code,
        f"no task (role={role}, menu shown, entry={'normal' if normal else 'recovery'})",
        show_menu=True,
        offer_recovery=not normal,
        offer_agent=normal,
    )


def _is_actionable_task(task: object) -> bool:
    """משימה נחשבת קיימת אם היא אובייקט לא ריק. אין הצצה לשדות שלה —
    הסוכן מקבל את הפרטים מ-hello, לא מ-GRUB."""
    return isinstance(task, dict) and bool(task)


def _is_joinable_session(session: object) -> bool:
    if not isinstance(session, dict):
        return False
    return session.get("state") in JOINABLE_SESSION_STATES


# ---------------------------------------------------------------------------
# רינדור
# ---------------------------------------------------------------------------


def render(answer: object, config: GrubConfig, *, recovery: bool = False) -> str:
    """הפונקציה היחידה שהשרת צריך לקרוא לה.

    לעולם לא זורקת. כל תקלה מחזירה קובץ שעושה chain לדיסק המקומי — כי מסך
    אתחול שנכשל חייב להשאיר מחשב שעובד, לא מחשב תקוע.
    """
    try:
        decision = decide(answer)
        if recovery:
            # לא בשימוש היום: ה-ESC נבחר במחשב עצמו, לא בשרת. קיים כדי
            # שאפשר יהיה בעתיד לפתוח שחזור מהקונסולה בלי מקש.
            decision = Decision(AGENT, "recovery-requested", recovery_mode=True)
        return _render_decision(decision, config)
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return render_local_only("generator-error")


def render_local_only(code: str) -> str:
    """קובץ המילוט. עולה מהדיסק המקומי, בלי תפריט ובלי הודעה."""
    return _assemble(
        code=code,
        timeout=0,
        timeout_style="hidden",
        entries=[_entry_local()],
    )


def _render_decision(decision: Decision, config: GrubConfig) -> str:
    if decision.action == AGENT:
        return _assemble(
            code=decision.code,
            timeout=0,
            timeout_style="hidden",
            default="imagectl",
            entries=[
                _entry_agent(config, recovery=decision.recovery_mode),
                _entry_local(),
            ],
        )

    # ערך אחד בלבד בנוסף לדיסק (#144). הדגלים אינם נדלקים יחד ב-decide,
    # ו-`or` כאן אינו מציג שניים גם אם מישהו ידליק בטעות: המצב נקבע לפי
    # offer_recovery, והערך תמיד נקרא `imagectl`.
    entries = [_entry_local()]
    if decision.offer_agent or decision.offer_recovery:
        entries.append(_entry_agent(config, recovery=decision.offer_recovery))

    # שני מצבים בלבד, ושניהם נגזרים מההחלטה — לא מהתצורה:
    #
    # תפריט גלוי → ‏MENU_NO_TIMEOUT, ממתין לאדם ללא הגבלה (#140).
    #
    # בלי תפריט → אפס. מי שמגיע לכאן בלי תפריט הוא מי שאין לו גם צוהר
    # שחזור (‏unregistered ו-bad-schema, שניהם עם offer_recovery=False),
    # כלומר קובץ עם ערך אחד ואין בו מה ללחוץ עליו. שתי שניות שם היו המתנה
    # חסרת תכלית על מחשב שאיננו משרתים — ולכן הוא מקבל בדיוק את מה
    # ש-render_local_only נותן ל-MAC פגום: חיבור מיידי לדיסק המקומי.
    return _assemble(
        code=decision.code,
        timeout=(MENU_NO_TIMEOUT if decision.show_menu else 0),
        timeout_style=("menu" if decision.show_menu else "hidden"),
        default="local",
        entries=entries,
    )


def _assemble(
    *,
    code: str,
    timeout: int,
    timeout_style: str,
    entries: list[str],
    default: str = "local",
) -> str:
    head = [
        "# Generated by ImageCtl. Do not edit - regenerated on every boot.",
        f"# decision: {_ascii(code)}",
        "",
        "set pager=0",
        f"set default={default}",
        f"set timeout={int(timeout)}",
        f"set timeout_style={timeout_style}",
    ]
    head += [
        "",
        _FUNC_CHAIN_LOCAL,
        "",
    ]
    # שומר אחרון: שום תו שאינו ASCII לא יוצא מכאן, גם אם מישהו יוסיף
    # מחר מקף עברי או אמוג'י לאחת המחרוזות הקבועות למעלה.
    return _strip_non_ascii("\n".join(head + entries) + "\n")


# --- מקטעים קבועים -------------------------------------------------------

#: נתיבי ה-bootloader שמנסים על הדיסק המקומי, לפי הסדר (אפיון סעיף 9).
#: Windows קודם, אחר כך הפצות לינוקס, ובסוף הנתיב הגנרי. בלינוקס מעדיפים
#: את shim (חתום Microsoft, ולכן עובר אימות תחת כל shim) על grubx64.efi
#: (חתום בידי ההפצה — shim של דביאן לא בהכרח מכיר את המפתח של אובונטו).
#: הפצה שחסרה כאן נופלת ל-bootx64.efi, שרוב המתקינים משאירים גם אותו.
LOCAL_BOOT_PATHS: tuple[str, ...] = (
    "/EFI/Microsoft/Boot/bootmgfw.efi",
    "/EFI/ubuntu/shimx64.efi",
    "/EFI/ubuntu/grubx64.efi",
    "/EFI/debian/shimx64.efi",
    "/EFI/debian/grubx64.efi",
    "/EFI/fedora/shimx64.efi",
    "/EFI/fedora/grubx64.efi",
    "/EFI/centos/shimx64.efi",
    "/EFI/rocky/shimx64.efi",
    "/EFI/almalinux/shimx64.efi",
    "/EFI/opensuse/shim.efi",
    "/EFI/BOOT/bootx64.efi",
)

# חיפוש מערכת ההפעלה המקומית. ה-ESP הוא FAT ולכן fat+part_gpt מספיקים;
# מכוון: לא טוענים ntfs, ואז search מדלג על מחיצות הנתונים ומסיים מהר.
# מחשב לינוקס שלא נמצא לו bootloader הוא כשל שקט שקשה לאבחן בשטח, ולכן
# הנתיבים הם רשימה אחת שמנסים אותה עד הסוף — לא if אחד ל-Windows.
# אם אין מערכת בכלל — עוצרים עם הודעה במקום exit, כי exit מחזיר לקושחה
# ש-PXE ראשון בסדר האתחול שלה, וזה ייצור לולאה אינסופית.
_FUNC_CHAIN_LOCAL = """function try_chain {
    unset espdev
    search --no-floppy --file --set=espdev "$1"
    if [ -n "$espdev" ]; then
        set root=$espdev
        chainloader "$1"
        boot
    fi
}

function chain_local {
    insmod part_gpt
    insmod fat
    insmod chain
    insmod search_fs_file

    for path in """ + " ".join(LOCAL_BOOT_PATHS) + """; do
        try_chain "$path"
    done

    echo "No operating system found on the local disk."
    echo "Contact IT. This computer will stay powered on."
    sleep --interruptible 60
    halt
}"""


def _entry_local() -> str:
    return """menuentry "Boot from local disk" --id local {
    chain_local
}"""


def _entry_agent(config: GrubConfig, *, recovery: bool) -> str:
    """ערך ה-ImageCtl. יש בדיוק אחד כזה בכל קובץ, ולכן שמו פשוט (#144).

    השם "ImageCtl - recovery / imaging" היה מובן רק כשהוא הופיע ליד
    "ImageCtl": בלעדיו הוא רק שואל את המפעיל שאלה שהוא לא אמור לענות
    עליה. ‏recovery משנה את שורת הפקודה, לא את מה שכתוב על המסך.
    """
    cmdline = _kernel_cmdline(config, recovery=recovery)
    host = config.grub_host
    return f"""menuentry "ImageCtl" --id imagectl {{
    echo "Loading ImageCtl..."
    insmod http
    if [ "$grub_platform" = "efi" ]; then insmod efinet; fi
    linux (http,{host}){config.kernel_path} {cmdline}
    initrd (http,{host}){config.initrd_path}
}}"""


def _kernel_cmdline(config: GrubConfig, *, recovery: bool) -> str:
    """שורת הפקודה של הקרנל.

    מכוון: לא מועברים כאן role, task, session, image_id או allowed_images.
    כל אלה מגיעים לסוכן מתשובת ה-hello (ממשק 3). אם היינו מעתיקים אותם
    לשורת הפקודה היינו יוצרים ממשק שני, לא מתועד, שיכול לסתור את הראשון.
    מה שכן עובר כאן הוא רק מה שהסוכן לא יכול לדעת לפני שהוא מדבר עם השרת:
    לאן לפנות, ואם המפעיל ביקש שחזור בלחיצת ESC.
    """
    parts = [
        "ip=dhcp",
        f"imagectl.server={config.base}",
        "console=tty0",
        "quiet",
        "loglevel=3",
    ]
    if recovery:
        parts.append("imagectl.mode=recovery")
    parts.extend(config.extra_cmdline)
    return " ".join(_ascii(p) for p in parts)


def _ascii(text: str) -> str:
    """לערך בודד שמוטמע בקובץ: מסיר תווים שאינם ASCII וגם מנטרל את
    התווים שיכולים לשבור את התחביר או להזריק פקודה — מירכאות, לוכסן
    הפוך, וסימן הדולר שגורם ל-GRUB להרחיב משתנה."""
    cleaned = _ASCII_SAFE.sub("", str(text))
    return cleaned.replace('"', "'").replace("\\", "/").replace("$", "")


def _strip_non_ascii(text: str) -> str:
    """לקובץ שלם: מסיר רק תווים שאינם ASCII, ומשאיר את התחביר של GRUB
    ‏(‎$‎ ומירכאות) שלם."""
    return _ASCII_SAFE_MULTILINE.sub("", text)


# ---------------------------------------------------------------------------
# הקובץ הסטטי שיושב על ה-TFTP
# ---------------------------------------------------------------------------


def render_bootstrap(config: GrubConfig) -> str:
    """מייצר את grub.cfg הקבוע שה-GRUB החתום טוען מה-TFTP.

    זה הקובץ היחיד בשרשרת שיושב על דיסק ולא נוצר בכל אתחול. תפקידו אחד:
    למשוך את התפריט הדינמי. הוא מיוצר מכאן ולא נכתב ביד כדי ש-chain_local
    יהיה מוגדר במקום אחד בלבד ולא יסטה בין שני הקבצים.
    """
    # ‏netloc בלי פורט = 80, כמו בכל URL של http.
    port = urlsplit(f"//{config.grub_host}").port or 80
    return _strip_non_ascii(f"""# ImageCtl bootstrap - generated by boot/grub_menu.py:render_bootstrap().
# Lives on the TFTP root as grub/grub.cfg. Regenerate, do not hand-edit.

set pager=0
set timeout=0
set timeout_style=hidden

# efinet exists only on UEFI; the i386-pc netboot core (the cloning
# machines, #38) has its driver built in, and insmod-ing a module that
# does not exist prints an error on the boot screen.
if [ "$grub_platform" = "efi" ]; then insmod efinet; fi
insmod http
insmod tftp

{_FUNC_CHAIN_LOCAL}

# GRUB fills net_default_mac from the interface that PXE-booted, already in
# lowercase-with-colons form -- the canonical format from the interface spec.
if [ -z "$net_default_mac" ]; then
    echo "ImageCtl: no network interface reported. Booting locally."
    sleep --interruptible 3
    chain_local
fi

# Deployment address. net_default_server -- the DHCP or PXE-proxy server that
# actually answered this station -- comes first, because it is the only address
# that is right on every network: a station booted by a proxy on a foreign VLAN
# (spec scenario 3) often cannot reach the deployment VLAN address at all. The
# port is not in DHCP, so it comes from the configured address.
# The explicit address stays as a fallback, for debugging and for the case
# where next-server arrives empty or points at a machine without ImageCtl.
set imagectl_fallback={config.grub_host}
if [ -n "$net_default_server" ]; then
    set imagectl_server=${{net_default_server}}:{port}
else
    set imagectl_server=$imagectl_fallback
fi

# Three attempts: GRUB's EFI http stack sometimes fails its very first
# connection on a cold boot while TFTP already works (#31). A retry a
# second later succeeds; only a server that is really down falls through.
# Each attempt also tries the fallback, unless it is the same address.
for attempt in 1 2 3; do
    configfile (http,$imagectl_server){_menu_path()}?mac=$net_default_mac
    if [ "$imagectl_server" != "$imagectl_fallback" ]; then
        configfile (http,$imagectl_fallback){_menu_path()}?mac=$net_default_mac
    fi
    sleep 1
done

# Reached only if every fetch failed -- server down, cable out, DHCP wrong.
echo "ImageCtl: server $imagectl_server unreachable. Booting from local disk."
sleep --interruptible 3
chain_local
""")


def _menu_path() -> str:
    return "/boot/menu"
