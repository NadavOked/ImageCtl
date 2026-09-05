"""שביל הפירורים של האתחול (#400) — הרשימה הסגורה של הצעדים.

מחשב שיכפול הוא חסר-ראש **בהגדרה** (#17, ואושרר 2026-09-05: "לא יהיה
להם מסך, מקלדת או עכבר"), ולכן מכונה שנעצרת בין ‏`GET /boot/menu` לבין
‏`POST /api/v1/agent/hello` אינה משאירה שום עקבה. זה מה שקרה ל-HP הפיזי
ב-#400: התפריט נמסר, ואז שבע דקות שקט — בלי `linux.mod` ובלי `vmlinuz`.

**היעדר צעד הוא המידע**, ולכן הרשימה כאן **מנויה מראש**: "לא הגיע
ל-pre-initrd" יכול להיות אמירה רק כשידוע שהיה אמור להגיע. צעד שאינו
ברשימה נדחה — שביל שמקבל כל מחרוזת אינו שביל, הוא ערימה.

המודול טהור, כמו `grub_menu`: אין בו רשת, דיסק או DB. הוא מחזיר
מחרוזות. האחסון והתצוגה יושבים ב-`server/boottrace.py`, ששואב את
הרשימה מכאן — מקור אמת אחד לשני הצדדים.
"""

from __future__ import annotations

__all__ = [
    "STEP_PATH",
    "STEPS",
    "STEP_INDEX",
    "GRUB_STEPS",
    "AGENT_STEPS",
    "TINY_BODY",
    "GRUB_FUNCTION_NAME",
    "is_step",
    "grub_function",
    "grub_call",
]

#: הנתיב שהצעד נרשם בו. יחסית ל-mount של ‎/boot, כמו ‎/menu.
STEP_PATH = "/boot/step"

#: הגוף שחוזר. בייט אחד: ‏GRUB מדפיס אותו למסך (אין הפניית פלט בתסריט
#: של GRUB), ולכן ערך האתחול מייצר שורת נקודות ולא דף טקסט. גוף באורך
#: אפס נמנע במכוון — ‏`Content-Length: 0` הוא בדיוק סוג הקצה שה-HTTP
#: של GRUB כבר הכשיל אותנו בו פעם (#12).
TINY_BODY = b"."

#: כל הצעדים, לפי הסדר שבו מכונה תקינה עוברת אותם.
#:
#:   menu         — השרת מסר את התפריט. נרשם בשרת עצמו, ולכן הוא הצעד
#:                  היחיד שאינו תלוי בכך שהמכונה עוד מדברת.
#:   entry        — ‏GRUB נכנס לערך ImageCtl.
#:   http-ok      — ‏`insmod http` חזר.
#:   pre-linux    — לפני משיכת הקרנל.
#:   pre-initrd   — הקרנל נטען; לפני משיכת ה-initramfs.
#:   pre-boot     — ה-initramfs נטען; סוף הערך, כלומר לפני `boot`.
#:   agent-net    — ‏`init` ב-initramfs הרים רשת וקיבל כתובת.
#:   agent-start  — ‏`imagectl-agent` התחיל וקרא את שורת הפקודה.
#:   agent-hello  — לפני ה-hello הראשון.
#:
#: אין כאן צעד "‏`init` התחיל" לפני `agent-net`, ולא במקרה: לפני ש-DHCP
#: ענה אין רשת, ואין דרך לדווח. ‏`agent-net` הוא הראיה החיובית שגם
#: ה-initramfs נפרק וגם `init` רץ עד הסוף — ואם הוא חסר בזמן ש-`pre-boot`
#: הגיע, זה בדיוק המקטע שנשבר.
STEPS: tuple[str, ...] = (
    "menu",
    "entry",
    "http-ok",
    "pre-linux",
    "pre-initrd",
    "pre-boot",
    "agent-net",
    "agent-start",
    "agent-hello",
)

STEP_INDEX: dict[str, int] = {name: i for i, name in enumerate(STEPS)}

#: מי מדווח מה. ‏`menu` אינו באף אחת מהן — הוא נרשם בצד השרת.
GRUB_STEPS: tuple[str, ...] = STEPS[1:6]
AGENT_STEPS: tuple[str, ...] = STEPS[6:]

#: שם הפונקציה בקובץ ה-GRUB. מוקדם בכוונה — ‏`trace` לבדו קרוב מדי
#: לשמות שגרסת GRUB עתידית עלולה לתפוס.
GRUB_FUNCTION_NAME = "imagectl_trace"


def is_step(name: object) -> bool:
    """האם זה אחד מהצעדים המנויים. כל דבר אחר אינו נרשם."""
    return isinstance(name, str) and name in STEP_INDEX


def grub_function(host: str) -> str:
    """הפונקציה שמשאירה פירור אחד, לראש קובץ ה-GRUB.

    ‏`cat` ולא פקודה מתוחכמת יותר: הוא קיים גם ב-i386-pc וגם ב-EFI,
    ‏`grub-mknetdir` מעתיק את כל המודולים ל-TFTP, ו-GRUB טוען מודול של
    פקודה לפי הצורך — בדיוק כפי שהוא כבר מושך `configfile.mod` בשרשרת
    הזאת. המחשב שנתקע ב-#400 הוא i386-pc, ולכן זו לא שאלה תיאורטית.

    ה-URL כולו בתוך מירכאות כפולות: ‏`&` הוא תו מיוחד בלקסר של GRUB
    ("&&"), ומחוץ למירכאות הוא שובר את השורה. במירכאות כפולות GRUB
    עדיין מרחיב משתנים, ולכן `$net_default_mac` ו-`$1` עובדים.

    **כישלון כאן אינו מפיל את ערך האתחול.** בתסריט של GRUB פקודה
    שנכשלה בתוך `menuentry` מדפיסה שגיאה וההרצה ממשיכה לפקודה הבאה;
    מה שמפיל ערך הוא `linux`/`initrd`/`boot` שנכשלים. לכן הקריאה
    עומדת בשורה משל עצמה, לא בתוך `if` ולא לפני `&&` — כלי אבחון
    שמונע אתחול הוא נזק.
    """
    return f"""function {GRUB_FUNCTION_NAME} {{
    cat "(http,{host}){STEP_PATH}?mac=${{net_default_mac}}&s=$1"
}}"""


def grub_call(step: str) -> str:
    """שורת קריאה אחת. זורקת על צעד שאינו ברשימה — טעות כתיב במחולל
    לא תיצור צעד שאיש לא יחכה לו."""
    if not is_step(step):
        raise ValueError(f"unknown boot step {step!r}")
    return f"{GRUB_FUNCTION_NAME} {step}"
