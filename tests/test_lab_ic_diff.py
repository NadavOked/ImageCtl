"""‏`ic-diff` קיים פעמיים, ו**עותק אמיתי נסחף בשקט** (#604).

‏cloud-init מחייב תוכן משובץ ב-`user-data`, ולכן אין דרך להפנות משם
לקובץ בריפו. שני העותקים נשארים — ומשהו חייב לספור בייטים, בדיוק
כמו `.claude/skills` מול `.agents/skills` ב-`test_agent_skills.py`.

⚠️ **למה דווקא הכלי הזה.** ב-08/09 ריצה נהרגה ב-OOM ב-29%,
ו-`ic-diff` הישן הדפיס `בסיס: 0 ענף: 0` — הוא סופר שורות `FAILED`,
ובקובץ שנקטע אין אף אחת. **זה נראה בדיוק כמו "אין רגרסיות".** ‏VM
שייבנה מ-seed מיושן יקבל בחזרה את הכלי שמייצר את "הירוק שאינו
מעיד" שהוא נכתב כדי למנוע.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "lab" / "ic-diff"
SEED = REPO / "tools" / "lab" / "testrunner" / "user-data"

#: ההזחה של גוש `content: |` ב-cloud-init.
INDENT = "      "


def embedded_copy() -> str:
    """שולף את גוף `ic-diff` מתוך ה-seed, בלי ההזחה של cloud-init."""
    lines = SEED.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.strip() == "- path: /usr/local/bin/ic-diff")
    content = next(i for i in range(start, len(lines))
                   if lines[i].strip() == "content: |")
    out = []
    for line in lines[content + 1:]:
        if line.strip() and not line.startswith(INDENT):
            break
        out.append(line[len(INDENT):] if line.startswith(INDENT) else "")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


def test_the_seed_carries_the_same_ic_diff_as_the_repo():
    ours = TOOL.read_text(encoding="utf-8")
    theirs = embedded_copy()
    assert theirs == ours, (
        "‏`tools/lab/ic-diff` וה-עותק ב-`testrunner/user-data` נסחפו. "
        "מכונה שתיבנה מה-seed תקבל כלי אחר מזה שנבדק (#604)."
    )


def test_the_extractor_can_actually_fail():
    """בקרה שלילית: שולף שמחזיר מחרוזת ריקה היה עובר תמיד."""
    assert len(embedded_copy().splitlines()) >= 20, (
        "השולף החזיר פחות מ-20 שורות — הוא כנראה לא מצא את הגוש, "
        "והשוואה מול כלום ירוקה תמיד"
    )


def test_the_shipped_tool_refuses_a_run_with_no_summary_line():
    """הראיה שזו אכן הגרסה החדשה, ולא רק ששני העותקים זהים.

    שני עותקים **ישנים** זהים גם הם, והטסט למעלה היה ירוק.
    """
    assert "אין שורת סיכום" in TOOL.read_text(encoding="utf-8"), (
        "‏`ic-diff` אינו בודק שהריצה הסתיימה — זו הגרסה שהחזירה "
        "'בסיס: 0 ענף: 0' על ריצה שנהרגה ב-OOM"
    )
