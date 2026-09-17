"""‏#649 שלב 1: `server/tools_catalog.json` נגזר מהמסמכים, לא נכתב ביד.

הבדיקה מריצה את המחולל (`tools/tools-catalog-from-docs.py`) על
`docs/tools/TOOLS-CHOICE.md` + `CATALOG.md` ומשווה בייט-בבייט ל-JSON
המקומט — עריכה ידנית של אחד מהשלושה בלי השני נופלת כאן. ומעליה:
הכרעות נדב 17/09 (שלוש קבוצות — "המחשב לא עולה" התפרקה ל-#1049/#433, ארבעה
כלים שעברו ל"שחזור קבצים", שתי שורות שיצאו — #1049/#671)
כמספרים שנגזרים **מהמסמך**, לא ממספר קשיח מהבריף.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = ROOT / "server" / "tools_catalog.json"
CHOICE = ROOT / "docs" / "tools" / "TOOLS-CHOICE.md"


def _generator():
    spec = importlib.util.spec_from_file_location("tools_catalog_from_docs", ROOT / "tools" / "tools-catalog-from-docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog() -> dict:
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def _doc_group_counts() -> dict[str, int]:
    """כמה ☐ בכל `##` של TOOLS-CHOICE — נספר מהמסמך, לא מטבלת הסיכום שלו."""
    counts, name = Counter(), None
    for line in CHOICE.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
        elif line.startswith("☐ ") and name:
            counts[name] += 1
    return counts


def test_the_json_is_exactly_what_the_docs_generate():
    gen = _generator()
    assert JSON_PATH.read_text(encoding="utf-8") == gen.render(gen.build()), \
        "server/tools_catalog.json סוטה מהמסמכים — הרץ python tools/tools-catalog-from-docs.py"


def test_only_the_three_groups_minus_exclusions_plus_the_moved_tools():
    gen, cat = _generator(), _catalog()
    doc = _doc_group_counts()
    assert cat["groups"] == list(gen.GROUPS) == ["מחיקה לפני מסירה/מכירה", "ווינדוס נעול / משתמש נעול", "שחזור קבצים"]
    assert "המחשב לא עולה" not in cat["groups"], "הקבוצה התפרקה (#1049 / #433)"
    by_group = Counter(t["group"] for t in cat["tools"])
    assert set(by_group) == set(gen.GROUPS), "קבוצה שאינה משלוש הקבוצות"
    expected = {g: doc[g] for g in gen.GROUPS}
    for binary, (src, dst) in gen.MOVED.items():
        expected[dst] += 1
    excluded_by_group = Counter(t["group"] for t in _all_rows_including_excluded(gen) if t["binary"] in gen.EXCLUDED)
    for g in gen.GROUPS:
        expected[g] -= excluded_by_group[g]
    assert dict(by_group) == expected, (dict(by_group), expected)
    assert len(cat["tools"]) == sum(expected.values())
    binaries = {t["binary"] for t in cat["tools"]}
    assert not binaries & set(gen.EXCLUDED), "שורה שיצאה (#1049/#671) עדיין בקטלוג"
    for binary, (src, dst) in gen.MOVED.items():
        row = next(t for t in cat["tools"] if t["binary"] == binary)
        assert row["group"] == dst and row["moved_from"] == src


def _all_rows_including_excluded(gen) -> list[dict]:
    """שורות ארבע הקבוצות כפי שהן במסמך (לפני EXCLUDED) — לספירת ההחרגות לפי קבוצה."""
    rows = []
    for name, group_rows, _advice in gen._choice_groups(CHOICE.read_text(encoding="utf-8")):
        if name in gen.GROUPS:
            rows += [dict(r, group=name) for r in group_rows]
    return rows


def test_ids_are_unique_slugs_and_risk_is_from_the_vocabulary():
    cat = _catalog()
    ids = [t["id"] for t in cat["tools"]]
    assert len(ids) == len(set(ids)), [i for i, n in Counter(ids).items() if n > 1]
    for t in cat["tools"]:
        assert re.fullmatch(r"[a-z0-9-]+", t["id"]), t["id"]
        assert t["risk"] in ("ro", "rw", "destroy"), t
        assert isinstance(t["packed"], bool)
        assert t["size_kb"] is None or (isinstance(t["size_kb"], int) and t["size_kb"] >= 0)
        assert t["size_source"], "מספר בלי מקור הוא ציטוט (עיקרון 5ב)"
        if t["packed"]:
            assert t["size_kb"] is None
        assert isinstance(t["recommended"], bool)
        assert t["title_he"] and t["what"] and t["binary"]


def test_every_tool_is_a_checkbox_line_in_tools_choice():
    """בלי להוסיף כלים: כל בינארי ב-JSON הוא שורת ☐ במסמך, עם אותו שם עברי."""
    text = CHOICE.read_text(encoding="utf-8")
    for t in _catalog()["tools"]:
        binary = " / ".join(f"`{b}`" for b in t["binary"].split(" / "))
        assert f"☐ **{t['title_he']}** {t['what']} ({binary}, " in text, t["binary"]


def test_module_ids_come_from_ours_then_ext_zip_then_slug():
    """ההכרעה על ה-id: ours-* קודם (`disk-blkdiscard`), אחרת ext-zip
    (`ntfs-recover-scan`), אחרת slug מהבינארי (`testdisk`)."""
    by_binary = {t["binary"]: t["id"] for t in _catalog()["tools"]}
    assert by_binary["blkdiscard"] == "disk-blkdiscard"          # ours-disk:disk-blkdiscard · ext-zip:disk:wipe-disk
    assert by_binary["fsck.vfat -a"] == "esp-fsck-repair"        # ours-boot:esp-fsck-repair
    assert by_binary["ntfsundelete"] == "ntfs-recover-scan"      # ext-zip:disk:ntfs-recover-scan בלבד
    assert by_binary["testdisk"] == "testdisk"                   # "—" → slug
    assert by_binary["dd zero / shred / wipe"] == "disk-dd-zero"


def test_recommended_tools_are_named_in_the_groups_advice_line():
    """`recommended` = שורת "ההמלצה שלי" של הקבוצה. הרשימה תומללה ביד —
    הבדיקה מוודאת שכל מומלץ נזכר בשורה (בבינארי או במילה מהכותרת)."""
    gen = _generator()
    advice = {name: line for name, _rows, line in gen._choice_groups(CHOICE.read_text(encoding="utf-8"))}
    for t in _catalog()["tools"]:
        if not t["recommended"]:
            continue
        line = advice[t["moved_from"] or t["group"]]
        words = [w for w in re.split(r"[\s,()`]+", t["title_he"]) if len(w) >= 3]
        assert t["binary"].split(" ")[0] in line or any(w in line for w in words), (t["binary"], line)
    # RECOMMENDED מכסה גם קבוצות-מקור שכליהן לא נכנסו (smartctl, efibootmgr…) —
    # הציפייה היא רק על מה שבקטלוג: מומלץ ⇔ הבינארי ברשימת קבוצת המקור שלו.
    for t in _catalog()["tools"]:
        assert t["recommended"] == (t["binary"] in gen.RECOMMENDED[t["moved_from"] or t["group"]]), t["binary"]
    assert sum(t["recommended"] for t in _catalog()["tools"]) == 9
