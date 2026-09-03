"""‏#140 — תפריט בחירה גלוי, בלי ESC ובלי טיימר.

הכרעת נדב מ-2026-08-30: תחנת תלמיד ומחשב בנייה מגיעים ישר למסך בחירה בין
הדיסק המקומי ל-ImageCtl. המחיר הוצג ואושרר — "מחשב בלי משימה עולה מהדיסק
תוך שניות" כבר אינו נכון למכונה רשומה.

הקובץ הזה נפרד מ-test_grub_menu.py בכוונה: חציו הראשון מקבע את ההתנהגות
החדשה, וחציו השני הוא בקרה שלילית על שלושת המסלולים ש**לא** השתנו. שלושתם
שומרי עיקרון 1, והם הסיבה היחידה שהשינוי הזה בטוח: מכונה זרה, ‏schema לא
מוכר ומחשב שיכפול אינם מקבלים מסך בחירה, ובדיקה שתעבור עליהם בשקט תסתיר
בדיוק את מה שחשוב.

הבדיקות כאן מייבאות רק שמות שהיו קיימים לפני השינוי, כדי שהרצה מול הקוד
הישן תיפול על **התנהגות** ולא על ImportError.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import AGENT, LOCAL, GrubConfig, decide, render

CFG = GrubConfig(server_base="http://10.99.12.10:8080")


def answer(**overrides) -> dict:
    base = {
        "schema": 1,
        "known": True,
        "role": "classroom",
        "group": {"id": "grp_LAB1", "label": "כיתה LAB1 חומרה", "suffix": "05"},
        "task": None,
        "session": None,
        "allowed_images": ["img_7f3a91"],
        "ui": {"language": "he", "require_login": False},
    }
    base.update(overrides)
    return base


def settings(text: str) -> dict[str, str]:
    """שורות ה-‎set שבראש הקובץ, כפי ש-GRUB יקרא אותן."""
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("set ") and "=" in line
    }


# --- ההתנהגות החדשה ---------------------------------------------------------


def test_a_classroom_station_with_no_task_gets_a_visible_menu():
    """זה השינוי עצמו: עד היום show_menu=False וצוהר חבוי של שתי שניות.

    ‏#144 צמצם את התפריט לערך ImageCtl **אחד** — בתחנת כיתה זהו השחזור,
    כי הערך הרגיל שם מחזיר `local` ומאתחל. מה ש-#140 קבע נשאר: יש
    תפריט, הוא גלוי, והוא ממתין לאדם. הבחירה בין הערכים היא
    ‏test_boot_single_entry.py.
    """
    decision = decide(answer())
    assert decision.action == LOCAL
    assert decision.show_menu is True
    assert decision.offer_recovery is True


def test_the_classroom_menu_waits_for_a_human_with_no_time_limit():
    text = render(answer(), CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "menu", "המסך עדיין חבוי"
    assert conf["set timeout"] == "-1", "‏GRUB: שלילי = ללא הגבלת זמן"
    assert conf["set default"] == "local"


def test_the_classroom_menu_offers_imagectl_beside_the_local_disk():
    """"בחירה בין דיסק מקומי ל-ImageCtl" — בדיוק שני הערכים האלה, ולא
    יותר (#144)."""
    text = render(answer(), CFG)
    assert "--id local {" in text
    assert "--id imagectl {" in text
    assert text.count("menuentry ") == 2


def test_the_build_machine_menu_never_times_out():
    """מחשב הבנייה כבר קיבל תפריט גלוי (#29) — מה שהוסר הוא הטיימר."""
    text = render(answer(role="build"), CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "menu"
    assert conf["set timeout"] == "-1"
    assert "--id imagectl {" in text


@pytest.mark.parametrize("role", ["classroom", "build"])
def test_the_visible_menu_is_ascii_because_grub_cannot_render_hebrew(role):
    render(answer(role=role), CFG).encode("ascii")


# --- בקרה שלילית: שלושת השומרים שלא נגענו בהם --------------------------------


@pytest.mark.parametrize(
    "a, code",
    [
        (answer(schema=2), "bad-schema"),
        (answer(known=False), "unregistered"),
    ],
)
def test_a_machine_we_cannot_identify_still_gets_no_menu(a, code):
    """עיקרון 1: מצב לא ברור מסתיים באתחול רגיל. תפריט שם הוא ניחוש —
    ובמכונה זרה הוא גם מסך שאיש לא ביקש על מחשב שאינו שלנו."""
    decision = decide(a)
    assert decision.action == LOCAL
    assert decision.code == code
    assert decision.show_menu is False
    assert decision.offer_agent is False
    assert decision.offer_recovery is False

    text = render(a, CFG)
    conf = settings(text)
    assert conf["set timeout_style"] == "hidden"
    assert conf["set default"] == "local"
    assert "--id imagectl" not in text
    assert "vmlinuz" not in text


@pytest.mark.parametrize("a", [answer(schema=2), answer(known=False)])
def test_a_machine_we_cannot_identify_waits_for_nothing_at_all(a):
    """"אם אין MAC הוא לא אמור לקבל GRUB" (נדב, #140). הקובץ מכיל ערך
    אחד — הדיסק המקומי — ואין בו מה ללחוץ עליו: לא ImageCtl, לא שחזור,
    ואפילו לא רמז. שתי שניות המתנה שם היו עיכוב בלי תכלית על מחשב זר,
    ולכן הוא מקבל אותו טיפול כמו MAC פגום ב-render_local_only."""
    text = render(a, CFG)
    assert settings(text)["set timeout"] == "0"
    assert text.count("menuentry ") == 1
    assert "--id local {" in text


def test_the_cloning_machine_still_goes_straight_to_the_agent():
    """למחשב שיכפול אין מערכת מקומית — מסך בחירה שממתין לאדם היה מותיר
    אותו תקוע, ו"דיסק מקומי" הוא מבוי סתום שמכבה אותו (#17)."""
    decision = decide(answer(role="cloner"))
    assert decision.action == AGENT
    assert decision.code == "cloner-wait"
    assert decision.show_menu is False

    conf = settings(render(answer(role="cloner"), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagectl"


def test_a_machine_with_a_task_still_goes_straight_to_the_agent():
    """הבקרה על הצד השני: התפריט הגלוי לא נכנס למסלול שבו יש עבודה."""
    conf = settings(render(answer(task={"id": "tsk_0091"}), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagectl"
