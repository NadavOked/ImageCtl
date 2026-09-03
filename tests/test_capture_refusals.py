"""‏#106: סירוב מבני בקליטה מגיע לקונסולה **עם סיבה**, לא כ-failed ריק.

‏`capture_disk` ידע לצרף סיבה לכשל של מחיצה בלבד (שורות 211-218 לפני
התיקון). ארבעת הסירובים שקורים *לפני* הזרם — אין דיסק כזה, הדיסק אינו
GPT, אין מחיצות, לא הצלחנו לחשב את הפריסה — היו `log` ו-`return 1`
בלבד. ‏`log` כותב ל-`$LOG_FILE` שיושב ב-tmpfs ונמחק באתחול, ומהדהד
ל-tty שנמחק מיד אחר כך ב-`ui_clear`. הסיבה קיימת לרגע ואז נעלמת.

מה שהמפעיל ראה: משימת קליטה במצב `failed`, בלי שורת סיבה. מה שהוא היה
צריך לדעת — "הכונן שחיברת אינו GPT" — נכתב ונעלם. זו שיחת התמיכה
הגרועה ביותר: הטכנאי מחליף כבל, מחליף כונן, מנסה שוב.

לכן כל בדיקה כאן נמדדת ב**מצב שהקונסולה קוראת** — ‏`targets/<dev>/error`
ומה ש-`build_progress` מרכיב ממנו (ממשק 4) — ולא ביומן. בדיקה שמסתפקת
ביומן הייתה עוברת גם על הקוד השבור.

ובקרה שלילית משלה: הסיבות חייבות להיות **נבדלות זו מזו**. עיקרון 5
אוסר לקפל "אינו GPT" ו"אין מחיצות" ו"לא הצלחנו לחשב" למחרוזת אחת.
ל-#87 נוספו כאן שני שערים — פריסה שאינה נכנסת לכונן היעד, ורצפת יעד
שאינה מספר בייטים — והם נספרים באותה בקרה.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

MAC = "aa:bb:cc:00:00:10"
TASK = "tsk_106"

#: כותרת GPT אמיתית כפי ש-`disk_scheme` מחפש אותה: "EFI PART" ב-LBA1.
GPT_DISK = bytes(512) + b"EFI PART" + bytes(504)
#: ‏MBR ותו לא — החתימה ב-510, ואין "EFI PART" אחריה.
MBR_DISK = bytes(510) + b"\x55\xaa" + bytes(512)
#: לא זה ולא זה. דיסק ריק שזה עתה יצא מהאריזה.
BLANK_DISK = bytes(1024)

#: ‏sgdisk שמדפיס טבלה בלי אף מחיצה — דיסק עם כותרת GPT ותו לא.
EMPTY_TABLE = (
    '#!/bin/sh\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
)

#: ‏sgdisk עם מחיצה אחת — מספיק כדי לעבור את שער "אין מחיצות".
ONE_PARTITION = (
    '#!/bin/sh\n'
    'if [ "$1" = "-i" ]; then\n'
    '  echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI)"\n'
    '  echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002"\n'
    '  echo "First sector: 2048 (at 1024 KiB)"\n'
    '  echo "Partition size: 204800 sectors (100.0 MiB)"\n'
    '  exit 0\n'
    'fi\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    'echo "   1            2048          206847   100.0 MiB   EF00  EFI system"\n'
)

#: ‏awk אמיתי בכל קריאה — חוץ מזו שמחשבת את הפריסה, שמזוהה לפי `-v disk=`
#: והיא היחידה בקובץ. השער הרביעי הוא "‏awk לא הניב ערך", וזה המצב היחיד
#: שמייצר אותו: כל שאר המסלולים מדפיסים מספר. הזיוף מדויק בכוונה — הוא
#: לא מפיל את פרסור ה-sgdisk שרץ באותו קובץ שורה קודם.
AWK_WITHOUT_AN_ANSWER = (
    '#!/bin/sh\n'
    'for a in "$@"; do\n'
    '  case "$a" in disk=*) exit 0 ;; esac\n'
    'done\n'
    'real=$(PATH=/usr/bin:/bin command -v awk)\n'
    'exec "$real" "$@"\n'
)


def capture_run(tmp_path, *, present=True, image=GPT_DISK, stubs=None,
                disk="sda", env=None) -> tuple[Path, Path, str]:
    """מריץ את `capture_disk` האמיתי מול דיסק מזויף, ומחזיר (box, run, out).

    ‏`node_is_block` מוחלף כמו בכל שאר בדיקות הסוכן: אין דרך ליצור התקן
    בלוקים בלי root, ובדיקה שמדולגת בחצי מהסביבות היא ירוק בלי ראיה.
    ‏`disk_scheme` לעומת זאת רץ באמת — `dd` על קובץ רגיל קורא בדיוק כמו
    על צומת, ולכן המחרוזת שמגיעה להודעה היא זו שהכלי באמת החזיר.
    """
    box = tmp_path / "box"
    dev = box / "dev"
    run = box / "run"
    dev.mkdir(parents=True)
    run.mkdir(parents=True)
    (dev / disk).write_bytes(image)
    # ‏`disk_scheme` גוזר את מיקום כותרת ה-GPT מגודל הסקטור הלוגי (#126),
    # ולכן הקופסה חייבת לחשוף אותו כמו כל כונן אמיתי. בלעדיו התשובה היא
    # `unknown` — לא ידענו — וזה מצב אחר מכל ארבעת הסירובים שנבדקים כאן.
    queue = box / "sys/block" / disk / "queue"
    queue.mkdir(parents=True)
    (queue / "logical_block_size").write_text("512\n", encoding="utf-8")
    nodes = box / "nodes"
    nodes.write_text(f"{posix(dev)}/{disk}\n" if present else "", encoding="utf-8")

    # ‏env נכתב **לפני** ה-`.` של capture.sh: כל ה-`${X:-ברירת מחדל}` שבו
    # נגזרים בטעינה, וייצוא שמגיע אחריה אינו משנה דבר.
    extra_env = "".join(f"export {name}={value!r}; "
                        for name, value in (env or {}).items())
    out = run_sh(
        make_stubs(box / "stubs", stubs or {})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} "
        f"SYSROOT={posix(box)!r} SERVER=http://s; " + extra_env
        + f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; "
        f". {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/progress.sh; "
        f". {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/capture.sh; "
        f'node_is_block() {{ grep -qxF "$1" {posix(nodes)!r} 2>/dev/null; }}; '
        f"capture_disk {TASK} {disk} > {posix(box)}/capture.out 2>&1; "
        'echo "rc=$?"; '
        f"build_progress '' {MAC} {TASK} > {posix(box)}/progress.json"
    )
    return box, run, out


def refusal_reason(box: Path, run: Path, out: str, disk="sda") -> str:
    """הסיבה **כפי שהקונסולה מקבלת אותה**: משדה `error` של היעד בדיווח
    ההתקדמות, ולא מהיומן. זו כל הנקודה של #106 — היומן נמחק באתחול."""
    assert out.strip().endswith("rc=1"), out
    report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
    assert report["state"] == "failed", report
    targets = report["targets"]
    assert len(targets) == 1, f"היעד היחיד — דיסק המקור — חייב להיות בדיווח: {report}"
    assert targets[0]["dev"] == disk, report
    assert targets[0]["state"] == "failed", report
    reason = targets[0].get("error", "")
    assert reason, (
        "היעד נכשל בלי סיבה — בדיוק ה-failed הריק של #106. "
        f"קובץ השגיאה: {(run / 'targets' / disk / 'error').exists()}"
    )
    return reason


# --- ארבעת הסירובים המבניים ---------------------------------------------------


def test_a_disk_that_is_not_there_says_so(tmp_path):
    """השער הראשון, והיחיד שרץ עוד לפני שנגענו בדיסק."""
    box, run, out = capture_run(tmp_path, present=False)
    reason = refusal_reason(box, run, out)
    assert "sda" in reason, reason
    assert "אין דיסק" in reason, reason


@pytest.mark.parametrize("scheme", ["mbr", "none"])
def test_a_source_disk_that_is_not_gpt_names_what_was_found(tmp_path, scheme):
    """הגדרת ה"גמור" של #106 מילה במילה: לא "capture failed" אלא
    "הכונן אינו GPT (נמצא: mbr)". המגבלה עצמה נשארת — ‏GPT בלבד היא
    החלטת אפיון (‏`docs/imagectl-spec.md:52`) — הבאג הוא שהיא הגיעה
    ליעד בלי סיבה."""
    box, run, out = capture_run(
        tmp_path, image={"mbr": MBR_DISK, "none": BLANK_DISK}[scheme])
    reason = refusal_reason(box, run, out)
    assert "GPT" in reason, reason
    assert scheme in reason, f"מה שנמצא בפועל אינו בהודעה: {reason}"


def test_a_gpt_disk_without_partitions_says_that_and_not_something_else(tmp_path):
    """כותרת GPT תקינה וטבלה ריקה. זה מצב אחר לגמרי מ"אינו GPT",
    והטכנאי שמקבל את שתי ההודעות עושה שני דברים שונים."""
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": EMPTY_TABLE})
    reason = refusal_reason(box, run, out)
    assert "מחיצות" in reason, reason
    assert "GPT" not in reason, f"הודעת המחיצות מאשימה את הסכימה: {reason}"


def test_a_layout_that_could_not_be_sized_says_it_could_not_be_sized(tmp_path):
    """השער הרביעי: יש מחיצות, והחישוב לא הניב ערך.

    ‏"לא הצלחנו לחשב" אינו "אין מחיצות" ואינו "המחיצה נכשלה" — עיקרון 5
    בדיוק. הסירוב עצמו נכון (בלי רצפת גודל אי אפשר לדעת לאיזה כונן
    האימג' נכנס), ומה שחסר לו היה רק המילה.
    """
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION, "awk": AWK_WITHOUT_AN_ANSWER})
    reason = refusal_reason(box, run, out)
    assert "פריסת" in reason or "לחשב" in reason, reason
    assert "מחיצות" not in reason or "לחשב" in reason, reason


# --- הבקרה השלילית: ארבע סיבות, לא אחת ---------------------------------------


def test_the_refusals_do_not_collapse_into_one_message(tmp_path):
    """מה שאסור לפי ה-Issue במפורש: לקפל אותם למחרוזת אחת.

    טסט שבודק כל שער לחוד היה עובר גם אם כולם היו מחזירים "הקליטה
    נדחתה" — ואז הקונסולה הייתה מציגה סיבה, והמפעיל עדיין לא היה יודע
    מה לעשות. כאן הם נמדדים זה מול זה. שני השערים של #87 נספרים כאן
    מאותה סיבה בדיוק: "הפריסה גדולה מהיעד" ו"הרצפה שנמסרה אינה מספר"
    הם שני מצבים, והשני הוא בדיוק זה שאסור לו להיראות כמו "לא נבדק".
    """
    cases = {
        "missing": dict(present=False),
        "not_gpt": dict(image=MBR_DISK),
        "no_parts": dict(stubs={"sgdisk": EMPTY_TABLE}),
        "no_size": dict(stubs={"sgdisk": ONE_PARTITION,
                               "awk": AWK_WITHOUT_AN_ANSWER}),
        "too_big": dict(stubs={"sgdisk": ONE_PARTITION},
                        env={"CAPTURE_TARGET_BYTES": "1048576"}),
        "bad_floor": dict(stubs={"sgdisk": ONE_PARTITION},
                          env={"CAPTURE_TARGET_BYTES": "256GB"}),
    }
    reasons = {}
    for name, kwargs in cases.items():
        box, run, out = capture_run(tmp_path / name, **kwargs)
        reasons[name] = refusal_reason(box, run, out)

    assert len(set(reasons.values())) == len(cases), reasons


def test_the_reason_also_stays_in_the_log(tmp_path):
    """הדיווח הוא היעד, והיומן נשאר — הוא מה שיש לטכנאי שיושב מול
    המכונה בזמן אמת. ‏#106 אינו "להעביר את הסיבה", הוא "גם לשם"."""
    box, run, out = capture_run(tmp_path, image=MBR_DISK)
    refusal_reason(box, run, out)
    assert "mbr" in log_of(run), log_of(run)


# --- הנעילה: אין `return 1` מבני שאינו עובר דרך היעד --------------------------


def capture_body() -> list[str]:
    """גוף `capture_disk` כ**קוד**: בלי הערות, והמשכי שורה מאוחדים.

    הנעילות שלמטה נשענות על סדר וצמידות, ושתיהן היו נופלות או עוברות
    בטעות על טקסט בהערה או על `\\` בסוף שורה. בדיקת נעילה ששוברת אותה
    הערה חדשה אינה נעילה, היא רעש."""
    source = (AGENT / "lib" / "capture.sh").read_text(encoding="utf-8")
    body = source[source.index("capture_disk() {"):]
    body = body[:body.index("\n}\n")]
    lines, buf = [], ""
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        buf = f"{buf} {line}".strip()
        if line.endswith("\\"):
            continue
        cleaned = buf.replace("\\", " ").strip()
        if cleaned:
            lines.append(cleaned)
        buf = ""
    return lines


def first_index(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"לא נמצא בקוד: {needle}")


def test_no_structural_refusal_returns_without_writing_a_reason():
    """הדפוס עצמו, ולא רק ארבעת המופעים: כל יציאה מ-`capture_disk` לפני
    הזרם חייבת לעבור דרך פונקציית הכשל המשותפת. בלי הנעילה הזו, השער
    החמישי שייכתב מחר יחזור בדיוק לבאג הזה — וכל ארבעת הטסטים למעלה
    ימשיכו לעבור."""
    lines = capture_body()
    bare = [line for i, line in enumerate(lines)
            if "return 1" in line and "_capture_failed" not in line
            and "_capture_failed" not in (lines[i - 1] if i else "")]
    assert not bare, f"סירוב שלא כותב סיבה ליעד: {bare}"


def test_the_target_is_registered_before_the_first_check():
    """‏`target_set` על יעד שלא נרשם אינו כותב לשום מקום שהקונסולה
    קוראת. ‏`target_init` חייב להיות השורה הראשונה בפועל — שני השערים
    הראשונים רצים לפני שהיה בכלל מה לדווח עליו."""
    lines = capture_body()
    init = first_index(lines, 'target_init "$_disk"')
    assert init < first_index(lines, "node_is_block"), \
        "היעד נרשם אחרי הבדיקה הראשונה — הסיבה של השער הזה תיפול לרצפה"
    assert init < first_index(lines, "disk_scheme"), "היעד נרשם אחרי בדיקת הסכימה"


def test_the_interfaces_document_says_when_the_capture_target_appears():
    """‏`docs/interfaces.md` הוא מקור האמת למה שעובר בין רכיבים. השדה
    `error` לא השתנה — אבל הוא מתמלא עכשיו במצבים חדשים, לפני שנשלח
    בייט אחד, וזה בדיוק מה שהמסמך צריך לומר."""
    doc = Path(__file__).resolve().parent.parent / "docs" / "interfaces.md"
    text = doc.read_text(encoding="utf-8")
    assert "#106" in text, "השינוי בהתנהגות השדה אינו מתועד"
