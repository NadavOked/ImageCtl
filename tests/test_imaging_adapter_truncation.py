"""‏`imaging-adapter.sh` הכריז הצלחה על אימג' חתוך (‏#301).

הצינור המקורי היה שורה אחת:

    "$@" | dd of="$TARGET" bs=4M conv=fsync status=progress

**ב-POSIX sh אין `pipefail`** — קוד היציאה של pipeline הוא של הפקודה
האחרונה בלבד. מקור שמת באמצע הזרם (‏`curl`, ‏`udp-receiver`,
‏`zstd`, ‏`partclone`) סוגר את הצינור, ‏`dd` קורא EOF מוקדם, כותב את
מה שהספיק ויוצא **0**. ‏`set -e` לא נדלק, והסקריפט סיים בקוד 0 על
דיסק חתוך. זו ההפרה המלאה של עיקרון 4 ("יעד שאיבד בייטים = יעד
שנכשל, בגלוי") ושל עיקרון 6 (אימות sha256 לפני שאימג' נכנס לשימוש).

**הטסט מריץ את הסקריפט כתהליך אמיתי תחת מעטפת POSIX אמיתית**, ולא
קורא את הקוד שלו — בדיוק כמו `test_diskless_destructive_guard.py`.
באג שכולו סמנטיקת מעטפת חי בפער שבין "מה שכתוב" ל"מה שהמעטפת עושה
עם זה", וקריאת קוד אינה חוצה את הפער הזה. ‏`set -o pipefail` היה
"עובר" כאן תחת bash ונשבר בשקט ב-busybox ash של ה-initramfs, ולכן
יש למטה גם שומר סטטי נגד bashism-ים.

**היעד הוא קובץ רגיל ב-tmp** — הסקריפט הזה כותב לדיסק, ואין גרסה
"בטוחה" של הרצה מול `/dev/sda`. ‏`safety-check.sh` מוחלף בבדל שמקבל
קובץ רגיל: הוא נבדק במקום אחר, וכאן הוא רק שער בכניסה.

**וזה כבר החזיר תשואה:** הניסיון הראשון שלף את ספירת הבייטים מ-stderr
של `dd`. ‏GNU dd מדפיס שם `N bytes ... copied`, ‏**busybox dd מדפיס רק
`0+1 records in/out` ואין בו שורת בייטים בכלל** — כך שעל היעד האמיתי,
ובו בלבד, המתאם היה יוצא 8 ("dd reported no byte count") בכל ריצה.
וריאנטי `dash` עברו, וריאנטי `busybox ash` נפלו, וזה מה שגילה את זה.

שלושת מצבי הכישלון נבדקים בנפרד כי הם שלושה דברים שונים:
מקור שמת באמצע · מקור שיצא **0** אחרי זרם קצר (‏`curl` עם
‏`Content-Length` חתוך — קוד יציאה יפה ואימג' פגום) · זרם באורך
הנכון עם תוכן שגוי. השלישי הוא היחיד שרק sha256 תופס.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
IMAGING = REPO / "vendor" / "diskless-pxe" / "imaging"
ADAPTER = IMAGING / "imaging-adapter.sh"

#: המטען: 4 KiB, קטן מספיק לריצה מיידית וגדול מספיק כדי שחצי ממנו
#: יהיה חצי מזוהה. התוכן קבוע, ולכן ה-sha הצפוי מחושב ולא מוקלד.
FULL_BYTES = 4096
FULL_SHA = hashlib.sha256(b"A" * FULL_BYTES).hexdigest()

#: מקור מסונתז: פולט `$1` בייטים של התו `$3` ויוצא בקוד `$2`.
#: ‏`head -c` מ-`/dev/zero` ואז `tr` — שניהם ב-busybox, בלי תלות בכלי
#: שלא יהיה ב-initramfs.
EMITTER = """#!/bin/sh
head -c "$1" /dev/zero | tr '\\0' "$3"
exit "$2"
"""

#: בדל `safety-check.sh` שמקבל קובץ רגיל. הוא מחזיר את אותה מחרוזת
#: כמו המקורי, כדי שהמנגנון שנבדק כאן יראה בדיוק את מה שהוא רואה
#: בשדה.
SAFETY_STUB = """#!/bin/sh
set -eu
[ -f "${1:-}" ] || { echo "stub: target missing" >&2; exit 2; }
echo "OK $1"
"""


def _posix_shells() -> list[list[str]]:
    """‏המעטפות שהסקריפט ייבדק תחתן — לפחות אחת, אחרת הטסט נכשל.

    ‏`skipif` כאן היה מחזיר בדיוק את הכשל שהטסט בא לתפוס: ריצה ירוקה
    שלא הריצה כלום. ב-CI מותקנות `dash` ו-`busybox` (‏`tests.yml`),
    ובתחנת הפיתוח יש `dash` ו-`sh` של git-bash.
    """
    shells: list[list[str]] = []
    for name in ("dash", "sh", "ash"):
        found = shutil.which(name)
        if found:
            shells.append([found])
    busybox = shutil.which("busybox")
    if busybox:
        shells.append([busybox, "ash"])
    return shells


SHELLS = _posix_shells()
SHELL_IDS = [" ".join(Path(part).name for part in argv) for argv in SHELLS]


def test_a_posix_shell_is_available():
    """בלי מעטפת POSIX אין מה לבדוק — וזה כישלון, לא דילוג."""
    assert SHELLS, (
        "לא נמצאה אף מעטפת POSIX (dash/sh/ash/busybox). בלעדיה הטסטים "
        "למטה היו עוברים בלי להריץ את הסקריפט — כישלון שנראה כמו הצלחה"
    )


def _write_lf(path: Path, text: str) -> None:
    """כתיבה עם LF בלבד — ‏`Path.write_text` בווינדוס מתרגם ל-CRLF,
    ו-`dash` קורא את ה-CR כחלק מהפקודה."""
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _tree(root: Path) -> tuple[Path, Path, Path]:
    """עץ הרצה: המתאם האמיתי, בדל בטיחות, מקור מסונתז ויעד."""
    work = root / "imaging"
    work.mkdir(parents=True)
    _write_lf(work / ADAPTER.name, ADAPTER.read_text(encoding="utf-8"))
    _write_lf(work / "safety-check.sh", SAFETY_STUB)
    emitter = root / "emit.sh"
    _write_lf(emitter, EMITTER)
    target = root / "target.img"
    target.write_bytes(b"\xee" * FULL_BYTES)
    return work / ADAPTER.name, emitter, target


def _sh(path: Path) -> str:
    """נתיב בצורה שמעטפת POSIX מקבלת.

    ‏`dash` של git-bash מפרש רצפי `\\` ב-`echo` — ‏`...\\Temp\\...`
    הופך לטאב, והפלט שהטסט בודק מגיע משובש. זה ארטיפקט של ווינדוס
    בלבד (היעד האמיתי הוא `/dev/sda`), ולכן מיושר כאן ולא בסקריפט.
    """
    return str(path).replace("\\", "/")


def _run(shell: list[str], adapter: Path, args: list[str]):
    return subprocess.run(
        shell + [_sh(adapter)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )


def _receiver(shell: list[str], emitter: Path, *args: str) -> list[str]:
    """‏פקודת הקליטה כפי שהמתאם יריץ אותה — ‏`"$@"`.

    כל חלק עובר ב-`_sh`: המתאם מריץ את הנתיב דרך מעטפת POSIX, ונתיב
    ווינדוס עם `\\` מגיע לשם כ"not found". וזו הסיבה שזה **כל** ה-argv
    של המעטפת ולא רק `shell[0]` — ‏`busybox ash` הוא שני חלקים, ו-
    ‏`busybox emit.sh` היה נקרא כשם applet.
    """
    return [_sh(Path(part)) for part in shell] + [_sh(emitter), *args]


def _image(shell, tmp_path, emit_bytes: int, emit_rc: int, fill: str):
    adapter, emitter, target = _tree(tmp_path)
    proc = _run(shell, adapter, [
        _sh(target), str(FULL_BYTES), FULL_SHA,
        *_receiver(shell, emitter, str(emit_bytes), str(emit_rc), fill),
    ])
    return proc, proc.stdout + proc.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_severed_stream_fails_with_the_byte_counts(shell, tmp_path):
    """‏#301 עצמו: המקור מת באמצע, החוליה האחרונה מצליחה.

    זה המצב שהצינור המקורי הכריז עליו הצלחה. הכישלון חייב לומר **כמה
    נכתב מול כמה נדרש** — "נכשל" בלי מספרים אינו ראיה, הוא מילה.
    """
    proc, out = _image(shell, tmp_path, FULL_BYTES // 2, 1, "A")
    assert proc.returncode != 0, (
        f"זרם שנקטע באמצע הוכרז הצלחה — זה #301 בדיוק (rc=0).\n{out}"
    )
    assert str(FULL_BYTES // 2) in out and str(FULL_BYTES) in out, (
        f"הכישלון לא אמר כמה בייטים נכתבו מול כמה נדרשו:\n{out}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_short_stream_that_exited_zero_fails(shell, tmp_path):
    """מקור שסיים **יפה** על זרם קצר — ‏`curl` עם `Content-Length` חתוך.

    בדיקת קוד היציאה של המקור לבדה עוברת כאן. הראיה חייבת להיות על
    מה שנכתב, לא על מי שסיים יפה.
    """
    proc, out = _image(shell, tmp_path, FULL_BYTES // 2, 0, "A")
    assert proc.returncode != 0, (
        f"מקור שיצא 0 אחרי חצי זרם הוכרז הצלחה:\n{out}"
    )
    assert str(FULL_BYTES // 2) in out and str(FULL_BYTES) in out, out


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_full_length_stream_with_wrong_content_fails(shell, tmp_path):
    """אורך נכון, תוכן שגוי — רק sha256 תופס את זה (עיקרון 6).

    ספירת בייטים לבדה עוברת כאן, ולכן זה הטסט היחיד שמוכיח שהאימות
    בקריאה חוזרת מהיעד באמת נאכף.
    """
    proc, out = _image(shell, tmp_path, FULL_BYTES, 0, "B")
    assert proc.returncode != 0, (
        f"אימג' באורך הנכון עם תוכן שגוי הוכרז הצלחה:\n{out}"
    )
    assert FULL_SHA in out, f"הכישלון לא הראה את ה-sha הצפוי:\n{out}"
    # ‏`FULL_SHA in out` לבדו מסופק כבר בשורת ה-`Starting imaging`, ולכן
    # הטסט היה עובר גם אם הקריאה החוזרת מהיעד לא רצתה בכלל. הראיה היא
    # ה-sha ש**נקרא בחזרה**, ולכן היא זו שנבדקת.
    assert "sha256 read back" in out, (
        f"הכישלון לא הראה sha שנקרא בחזרה מהיעד — האימות אולי לא רץ:\n{out}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_complete_stream_passes_and_prints_the_numbers(shell, tmp_path):
    """ולא תיקון-יתר: זרם שלם עובר, וההצלחה מודפסת עם המספרים.

    "OK" כמילה אינו ראיה חיובית. הצלחה נמדדת בבייטים וב-sha שנקראו
    בחזרה מהיעד.
    """
    proc, out = _image(shell, tmp_path, FULL_BYTES, 0, "A")
    assert proc.returncode == 0, f"זרם שלם נכשל:\n{out}"
    assert str(FULL_BYTES) in out and FULL_SHA in out, (
        f"ההצלחה הודפסה בלי המספרים:\n{out}"
    )
    written = (tmp_path / "target.img").read_bytes()
    assert hashlib.sha256(written[:FULL_BYTES]).hexdigest() == FULL_SHA, (
        "הסקריפט הכריז הצלחה אבל היעד אינו האימג'"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_missing_expected_size_and_sha_is_a_refusal(shell, tmp_path):
    """בלי גודל ו-sha אין מה לאמת — והמסלול חייב לסרב, לא לכתוב בעיוורון.

    זו הצורה שבה #301 היה חוזר: אינטגרציה שקוראת למתאם בלי הארגומנטים,
    מקבלת כתיבה בלי אימות, ולא יודעת על כך.
    """
    adapter, emitter, target = _tree(tmp_path)
    proc = _run(shell, adapter, [
        _sh(target), *_receiver(shell, emitter, str(FULL_BYTES), "0", "A"),
    ])
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"המתאם כתב בלי גודל ו-sha צפויים:\n{out}"


def test_the_adapter_has_no_bashisms():
    """‏`set -o pipefail` היה "מתקן" את זה תחת bash ונשבר ב-busybox ash.

    השומר סטטי בכוונה: הוא תופס bashism גם בתחנה שאין בה busybox. במעבדה
    יש busybox, והווריאנטים `[busybox ash]` למעלה כן מריצים את המנגנון —
    ‏**הם** אלה שתפסו את הסתמכות ספירת הבייטים על stderr של GNU dd.
    """
    text = ADAPTER.read_text(encoding="utf-8")
    assert "pipefail" not in text, "‏pipefail אינו קיים ב-POSIX sh (busybox ash)"
    assert "PIPESTATUS" not in text, "‏PIPESTATUS הוא bashism"
    assert "function " not in text, "‏`function` הוא bashism"
    assert text.startswith("#!/bin/sh"), "המתאם חייב להישאר POSIX sh"
