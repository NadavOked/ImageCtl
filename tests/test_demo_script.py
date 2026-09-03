"""ההדגמה למנהל מול השרת האמיתי — ‏tools/lab/demo.ps1, ‏Issue #70.

ההדגמה היא PowerShell, ולכן היא לא נבדקת כאן בהרצה. מה שכן נבדק כאן
הוא **החוזה** שהיא נשענת עליו, וזה בדיוק מה שנשבר בשקט: היא קוראת
נתיבים מהשרת, חותכת שלוש שורות מתוך תפריט האתחול לפי אורך תחילית, וקוראת
שמות שדות מתוך JSON. כל אחד משלושת אלה יכול להשתנות בצד ה-Python בלי
שאיש יריץ סקריפט PowerShell — והתוצאה הייתה מתגלה מול קהל, כשהשלב מדווח
"לא הצלחנו לבדוק".

הבדיקות כאן מריצות שרת אמיתי (אותן fixtures של שאר הבדיקות) ומוודאות
שכל מה שההדגמה מבקשת עונה, ושכל שדה שהיא קוראת קיים בתשובה.

נבדק כאן גם עיקרון 5 כטקסט: בהדגמה אין `-ErrorAction SilentlyContinue`.
זה בדיוק הדפוס ש-CLAUDE.md מציין כמי שהפך "אין הרשאה" ל"אין מכונות
רצות" — ובסקריפט שמכריז "עבר" מול מנהל הוא מסוכן במיוחד.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom      # noqa: E402

LAB = Path(__file__).resolve().parent.parent / "tools" / "lab"
DEMO_FILES = ["demo.ps1", "demo-ui.ps1", "demo-checks.ps1",
              "demo-actions.ps1", "demo-steps.ps1"]

#: מגבלת ~300 השורות לקובץ (CLAUDE.md, עיקרון 8). ההדגמה פוצלה לחמישה
#: קבצים בדיוק בגללה, וקל מאוד להחזיר אותה למקום אחד ענק.
MAX_LINES = 300


def read(name: str) -> str:
    # קבצי ה-PowerShell נשמרים UTF-8 עם BOM — בלעדיו Windows PowerShell
    # ‏5.1 קורא אותם כ-ANSI וכל שורת עברית הופכת לשגיאת תחביר.
    return (LAB / name).read_text(encoding="utf-8-sig")


def demo_source() -> str:
    return "\n".join(read(name) for name in DEMO_FILES)


# --- הקבצים עצמם ------------------------------------------------------------


def test_every_demo_file_exists_and_carries_a_bom():
    for name in DEMO_FILES:
        path = LAB / name
        assert path.is_file(), f"{name} חסר"
        assert path.read_bytes()[:3] == b"\xef\xbb\xbf", (
            f"{name} נשמר בלי BOM — Windows PowerShell 5.1 יקרא אותו כ-ANSI"
        )


def test_no_demo_file_grows_past_the_line_limit():
    for name in DEMO_FILES:
        lines = read(name).count("\n") + 1
        assert lines <= MAX_LINES, f"{name}: {lines} שורות"


def test_the_demo_never_swallows_an_error_into_a_pass():
    """עיקרון 5 כטקסט. ‏Get-Command הוא היוצא מן הכלל היחיד, ושם
    ההיעדר עצמו הוא מה שנבדק ומדווח כ-unknown."""
    for name in DEMO_FILES:
        for number, line in enumerate(read(name).splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue                       # הערה שמסבירה את האיסור
            assert "SilentlyContinue" not in line, f"{name}:{number}"
            assert "2>$null" not in line, f"{name}:{number}"
            if "-ErrorAction Ignore" in line:
                assert "Get-Command" in line, f"{name}:{number}"


# --- הנתיבים שההדגמה מבקשת מהשרת --------------------------------------------


def demo_paths() -> set[str]:
    """כל נתיב שההדגמה בונה מכתובת השרת."""
    return set(re.findall(r"(?:ServerUrl\)|\$BaseUrl)(/[A-Za-z0-9/_.\-]+)",
                          demo_source()))


def test_the_demo_asks_for_paths_that_exist(server):
    paths = demo_paths()
    # אם החיפוש לא מצא דבר, הבדיקה הייתה "עוברת" על ריק — בדיוק
    # ה-pytest שיוצא 0 כשחבילה שלמה דילגה (#52).
    assert paths >= {"/boot/menu", "/api/v1/agent/state",
                     "/api/v1/agent/sessions/active"}
    for path in paths:
        response = server["anon"].get(path)
        assert response.status_code != 404, f"{path} אינו קיים בשרת"


def test_the_liveness_probe_does_not_touch_anything(server):
    """הדופק של ההדגמה חייב להיות קריאה בלבד.

    ‏/boot/menu היה נראה מתאים, והוא רושם כל שאלה כמגע של המכונה: דופק
    כזה היה שותל מכונת רפאים בטבלת הרשת של מעבדה חיה. הבדיקה סוגרת את
    הדלת הזו — אם מישהו יחזיר את הדופק ל-/boot/menu, היא תיפול.
    """
    probe = re.search(r'\$script:DemoProbeMac\s*=\s*"([0-9a-f:]+)"',
                      read("demo-checks.ps1"))
    assert probe, "לא נמצא ה-MAC שהדופק משתמש בו"
    mac = probe.group(1)

    body = re.search(r"function Test-DemoServerAlive.*?\n}", read("demo-checks.ps1"),
                     re.S).group(0)
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "/api/v1/agent/state" in code
    assert "/boot/menu" not in code

    conn = server["ctx"].conn
    before = conn.execute("SELECT COUNT(*) AS n FROM net_devices").fetchone()["n"]
    assert server["anon"].get(f"/api/v1/agent/state?mac={mac}").status_code == 200
    after = conn.execute("SELECT COUNT(*) AS n FROM net_devices").fetchone()["n"]
    assert after == before, "הדופק שתל מכונה בטבלת הרשת"


def test_the_boot_menu_question_is_the_one_that_does_leave_a_mark(server):
    """התיעוד בשלב 7 אינו זהירות יתר — הבקשה באמת נרשמת.

    אם השרת יפסיק לרשום, הטקסט שההדגמה מציגה למנהל יהיה שקר, וזה
    המקום שבו זה יתגלה.
    """
    conn = server["ctx"].conn
    mac = "00:00:5e:07:1a:c9"
    assert conn.execute("SELECT COUNT(*) AS n FROM net_devices WHERE mac = ?",
                        (mac,)).fetchone()["n"] == 0
    assert server["anon"].get(f"/boot/menu?mac={mac}").status_code == 200
    assert conn.execute("SELECT COUNT(*) AS n FROM net_devices WHERE mac = ?",
                        (mac,)).fetchone()["n"] == 1


# --- שלוש השורות שההדגמה חותכת מתוך התפריט ----------------------------------


def menu_slices() -> list[tuple[str, int]]:
    """הזוגות (תחילית, אורך שנחתך) מתוך ConvertFrom-DemoBootMenu."""
    return [
        (prefix, int(offset))
        for prefix, offset in re.findall(
            r'-like\s+"([^"]+)\*"\s*\)?\s*\{[^}]*?\.Substring\((\d+)\)',
            read("demo-checks.ps1"),
        )
    ]


def test_the_menu_prefixes_are_sliced_at_the_right_length():
    slices = menu_slices()
    assert len(slices) == 3, f"נמצאו {len(slices)} חיתוכים במקום שלושה"
    for prefix, offset in slices:
        assert len(prefix) == offset, (
            f"'{prefix}' באורך {len(prefix)} נחתך ב-{offset} — "
            "ההדגמה תקרא ערך חתוך או תבלע תו"
        )


def test_the_menu_the_server_serves_still_has_those_three_lines(server):
    """אותן תחיליות, מול פלט אמיתי של המחולל — לא מול קבוע כתוב.

    שלב 7 (בדיקה 3.1) עומד על שלוש השורות האלה: החלטה, מאיפה עולים,
    ואם מוצג מסך. ‏MAC לא רשום = ברירת המחדל של עיקרון 1.
    """
    text = server["anon"].get("/boot/menu?mac=00:00:5e:07:1a:cf").text
    lines = [line.strip() for line in text.split("\n")]
    for prefix, offset in menu_slices():
        matching = [line for line in lines if line.startswith(prefix)]
        assert matching, f"התפריט אינו מכיל שורה שמתחילה ב-'{prefix}'"
        # מה שההדגמה תקרא בפועל, אחרי החיתוך.
        assert matching[0][offset:].strip(), f"'{prefix}' נחתך לערך ריק"

    parsed = {line.split("=", 1)[0]: line.split("=", 1)[1]
              for line in lines if line.startswith("set ")}
    assert parsed["set default"] == "local"
    assert parsed["set timeout_style"] == "hidden"


# --- שמות השדות שההדגמה קוראת מ-JSON ----------------------------------------


BUILD_MAC = "00:00:5e:07:1a:b0"


def setup_build_machine(server) -> str:
    # ‏grp_BUILD היא אחת הקבוצות שהשרת יוצר לעצמו בהתקנה — ההדגמה
    # נשענת על כך שמחשב הבנייה כבר במקום, ולא על קבוצה שמישהו הקים.
    imported = server["admin"].post(
        "/api/console/machines/import",
        json={"group_id": "grp_BUILD", "text": f"{BUILD_MAC} 01\n"},
    ).json()
    assert imported["saved"] == 1 and not imported["rejected"]
    return BUILD_MAC


def test_the_machine_state_the_demo_reads_is_all_there(server):
    """שלב 3 קורא את העבודה של מחשב הבנייה מ-/api/v1/agent/state."""
    mac = setup_build_machine(server)
    assert server["anon"].post("/api/v1/agent/hello",
                               json=hello_body(mac)).status_code == 200
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    # ‏Get-DemoMachineState עובר על כתובות המכונה עד שהשרת מכיר אחת;
    # ‏`known` הוא השדה שעליו הוא נשען, ו-‏mac הוא הראיה שזו התשובה
    # של השרת הזה (הדופק של ההדגמה נשען עליו).
    assert state["known"] is True
    assert state["mac"] == mac

    created = server["admin"].post(
        "/api/console/tasks/capture",
        json={"mac": mac, "disk": "sda", "name": "Lab 2026"},
    )
    assert created.status_code == 200, created.text
    task = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()["task"]
    assert task is not None
    # ‏id הוא מה שמבדיל בין הקליטה שההדגמה הרגע עשתה לבין קליטה ישנה
    # שהסתיימה מזמן ועדיין רשומה למכונה (Test-DemoIsNew).
    for field in ("id", "state", "name", "error", "bytes_written"):
        assert field in task, f"שלב 3 קורא את {field} ואין אותו בתשובה"
    assert task["name"] == "Lab 2026"


def test_a_finished_old_capture_is_still_the_answer_for_that_machine(server):
    """למה שלב 3 חייב בסיס השוואה, ולא רק "יש עבודה במצב done".

    ‏/api/v1/agent/state מחזיר את **העבודה האחרונה** של המכונה, גם אחרי
    שהיא נגמרה מזמן. במעבדה החיה מחשב הבנייה נושא קליטה גמורה מסבב
    קודם, ובלי `before_buildtask` שלב 3 היה מכריז "עבר" לפני שהמנהל
    נגע בכלום — היעדר שינוי שנקרא כהצלחה (עיקרון 5).
    """
    mac = setup_build_machine(server)
    assert server["anon"].post("/api/v1/agent/hello",
                               json=hello_body(mac)).status_code == 200
    first = server["admin"].post(
        "/api/console/tasks/capture",
        json={"mac": mac, "disk": "sda", "name": "Old image"},
    )
    assert first.status_code == 200, first.text
    task_id = first.json().get("id") or \
        server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()["task"]["id"]
    server["ctx"].conn.execute(
        "UPDATE tasks SET state = 'done', bytes_written = 999 WHERE id = ?", (task_id,)
    )
    server["ctx"].conn.commit()

    later = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()["task"]
    assert later["id"] == task_id and later["state"] == "done", (
        "עבודה שנגמרה ממשיכה להיות התשובה — ולכן הבסיס בהפעלת השלב הכרחי"
    )
    assert "Save-DemoBaseline" in read("demo-steps.ps1")
    assert "Test-DemoIsNew" in read("demo-steps.ps1")


def test_the_round_the_demo_watches_is_all_there(server):
    """שלבים 4 ו-6 קוראים את ההפצה מ-/api/v1/agent/sessions/active."""
    ids = setup_classroom(server)
    opened = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91", "prefix": "LAB1"},
    )
    assert opened.status_code == 200, opened.text
    assert server["anon"].post("/api/v1/agent/hello",
                               json=hello_body(ids["mac1"])).status_code == 200

    view = server["anon"].get("/api/v1/agent/sessions/active").json()["session"]
    assert view is not None
    for field in ("id", "group_role", "group_label", "image_name", "prefix", "members"):
        assert field in view, f"ההדגמה קוראת את {field} ואין אותו בתשובה"
    assert view["group_role"] == "classroom"      # מה ש-Test-DemoSessionData משווה
    assert view["prefix"] == "LAB1"

    assert view["members"], "המכונה שאמרה שלום אינה ברשימה"
    for field in ("done", "state", "name", "error", "hostname"):
        assert field in view["members"][0], f"ההדגמה קוראת את {field} בכל מחשב"
    # שם המחשב הוא מה שנשאל עליו הצופה בשלב 6 — הוא חייב להיות אמיתי.
    assert view["members"][0]["hostname"] == "LAB1-05"


def test_the_cloner_role_the_demo_expects_is_a_real_role(server):
    """שלב 4 מוודא שההפצה שרצה היא של חדר השיכפולים ולא של כיתה.

    ההשוואה היא מול המחרוזת `cloner` בדיוק, ולכן שינוי שם התפקיד היה
    הופך את שלב 4 ל"ההפצה שרצה עכשיו אינה לחדר השיכפולים" — כישלון
    שנראה כמו תקלה בהפצה ולא כמו שינוי בקוד.
    """
    groups = {g["id"]: g["role"]
              for g in server["admin"].get("/api/console/groups").json()}
    assert groups.get("grp_CLONERS") == "cloner"
    assert groups.get("grp_BUILD") == "build"
    # שני התפקידים שההדגמה מזכירה בשמם, ואין שלישי שהיא מכנה אחרת.
    assert set(re.findall(r'-Role\s+"(\w+)"', demo_source())) == {"cloner", "classroom"}
