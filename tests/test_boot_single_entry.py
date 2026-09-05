"""‏#144 — ערך ImageCtl **אחד** בתפריט, והוא הנכון לתפקיד המכונה.

עד כאן קיבלה מכונה רשומה בלי משימה שלושה ערכים: הדיסק, `imagectl`
ו-`imagectl-recovery` — **ואין על המסך שום דבר שמסביר את ההבדל**. ב-30/08
נדב אתחל את הלנובו (`role=classroom`), בחר `ImageCtl`, והמכונה אתחלה בלי
לעשות דבר: `agent/lib/decide.sh` על `classroom` בלי משימה מחזיר `local`
ו-`die_local` מאתחל. הערך שהיה שימושי לו הוא דווקא השחזור. ומאז #140
התפריט אינו נעלם אחרי טיימר, ולכן המכונה חוזרת לאותו תפריט ונשארת שם.

המיפוי כאן הוא טבלת ההחלטה של הסוכן קרואה מהצד השני, לא העדפה:

    classroom (וכל תפקיד לא מוכר) → recovery — כי "רגיל" מחזיר local
    build                          → רגיל     — כי הוא מחזיר build_console

הקובץ בודק גם את מה ש**לא** זז: מכונה לא מזוהה, מחשב שיכפול, ומכונה עם
משימה. כמו ב-test_visible_boot_menu.py, הייבוא כאן הוא של שמות שהיו
קיימים לפני השינוי בלבד — כדי שהרצה מול הקוד הישן תיפול על **התנהגות**
ולא על ImportError.
"""

from __future__ import annotations

import pytest

from boot.grub_menu import AGENT, LOCAL, GrubConfig, decide, render

CFG = GrubConfig(server_base="http://10.44.12.10:8080")


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
    return {
        line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("set ") and "=" in line
    }


def agent_entry(text: str) -> str:
    """גוף הערך היחיד שאינו הדיסק המקומי."""
    assert "--id imagectl {" in text, "אין ערך ImageCtl בקובץ"
    return text.split("--id imagectl {", 1)[1].split("}", 1)[0]


# --- ערך אחד, והוא הנכון ------------------------------------------------------


@pytest.mark.parametrize("role", ["classroom", "build", "unknown", "teacher"])
def test_a_registered_machine_gets_exactly_one_imagectl_entry(role):
    """שני ערכים בסך הכל: הדיסק, ו-ImageCtl אחד. זו הבקשה של נדב —
    "רק אפשרות אחת בנוסף לעלות מהדיסק"."""
    text = render(answer(role=role), CFG)
    assert text.count("menuentry ") == 2
    assert text.count("--id imagectl") == 1
    assert "--id local {" in text
    assert "--id imagectl-recovery" not in text


def test_the_classroom_entry_is_the_one_that_actually_does_something():
    """‏`decide.sh` על classroom בלי משימה: מצב רגיל מחזיר `local`
    ומאתחל, ‏recovery מגיע למסך הכניסה והשחזור. זה הבאג של #144."""
    decision = decide(answer(role="classroom"))
    assert decision.action == LOCAL and decision.show_menu is True
    assert decision.offer_recovery is True
    assert decision.offer_agent is False, "ערך שמאתחל בלי לעשות דבר"

    assert "imagectl.mode=recovery" in agent_entry(render(answer(), CFG))


def test_the_build_machine_entry_reaches_the_build_console():
    """מחשב הבנייה הוא ההפך הגמור: ‏`decide.sh` מחזיר `build_console`
    במצב רגיל, ו-recovery היה מסתיר את מסך הקליטה/ההפצה (#29)."""
    decision = decide(answer(role="build"))
    assert decision.offer_agent is True
    assert decision.offer_recovery is False

    assert "imagectl.mode=recovery" not in agent_entry(
        render(answer(role="build"), CFG))


@pytest.mark.parametrize("role", ["unknown", "teacher", ""])
def test_a_role_we_do_not_recognise_is_treated_like_a_classroom_station(role):
    """ברירת המחדל של `decide.sh` לכל תפקיד שאינו build/cloner היא
    `local` — בדיוק כמו classroom, ולכן אותו ערך."""
    assert decide(answer(role=role)).offer_recovery is True
    assert "imagectl.mode=recovery" in agent_entry(render(answer(role=role), CFG))


def test_exactly_one_of_the_two_offers_is_lit_for_every_role():
    """השומר מפני חזרה לשניים: לא "שניהם" ולא "אף אחד"."""
    for role in ["classroom", "build", "unknown", "teacher", "", "guest"]:
        decision = decide(answer(role=role))
        assert decision.offer_agent != decision.offer_recovery, role


def test_the_single_entry_is_called_plainly_imagectl():
    """"ImageCtl - recovery / imaging" היה מובן רק ליד "ImageCtl". עם
    ערך אחד הוא רק שואל את המפעיל שאלה שאין לו דרך לענות עליה."""
    for role in ["classroom", "build"]:
        text = render(answer(role=role), CFG)
        assert 'menuentry "ImageCtl" --id imagectl {' in text
        assert "recovery / imaging" not in text
        text.encode("ascii")                    # פלט GRUB תמיד ASCII


def test_the_menu_still_waits_for_a_human_with_no_time_limit():
    """‏#140 לא זז: מה שהשתנה הוא כמה ערכים, לא אם יש תפריט."""
    for role in ["classroom", "build"]:
        conf = settings(render(answer(role=role), CFG))
        assert conf["set timeout_style"] == "menu"
        assert conf["set timeout"] == "-1"
        assert conf["set default"] == "local"


def test_the_entry_carries_nothing_but_the_server_and_the_mode():
    """עיקרון 2 — שורת הקרנל נקייה. ‏`imagectl.mode=recovery` הוא החריג
    היחיד המותר, וצמצום התפריט אינו תירוץ להוסיף עוד."""
    body = agent_entry(render(answer(), CFG))
    line = next(part for part in body.splitlines() if part.strip().startswith("linux "))
    for leak in ["classroom", "grp_LAB1", "img_7f3a91", "LAB1", "role=", "task"]:
        assert leak not in line
    flags = [word for word in line.split() if word.startswith("imagectl.")]
    assert flags == ["imagectl.server=http://10.44.12.10:8080",
                     "imagectl.mode=recovery"]


# --- מה שאסור לשבור -----------------------------------------------------------


@pytest.mark.parametrize("a", [answer(known=False), answer(schema=2)])
def test_a_machine_we_do_not_serve_gets_no_menu_and_no_imagectl(a):
    """עיקרון 1, ושלב 7 בהדגמה: ערך אחד בקובץ, `timeout=0`, בלי תפריט,
    בלי ImageCtl ובלי שחזור."""
    conf = settings(render(a, CFG))
    assert conf["set timeout"] == "0"
    assert conf["set timeout_style"] == "hidden"
    text = render(a, CFG)
    assert text.count("menuentry ") == 1
    assert "--id imagectl" not in text and "vmlinuz" not in text


def test_the_cloning_machine_still_goes_straight_to_the_agent():
    """"לא מדבר על מחשבי שיכפול" (נדב, #144). אין לו מערכת מקומית, ומסך
    שממתין לאדם ליד מכונה חסרת מסך היה משאיר אותו תקוע (#17)."""
    decision = decide(answer(role="cloner"))
    assert decision.action == AGENT and decision.code == "cloner-wait"
    assert decision.show_menu is False

    conf = settings(render(answer(role="cloner"), CFG))
    assert conf["set timeout_style"] == "hidden"
    assert conf["set timeout"] == "0"
    assert conf["set default"] == "imagectl"


def test_a_machine_with_work_still_goes_straight_to_the_agent():
    """מסלול המשימה והסבב לא נגענו בו — הוא AGENT, מסלול אחר לגמרי."""
    for a in [answer(task={"id": "tsk_0091"}),
              answer(session={"id": "ses_a91f", "state": "open"})]:
        conf = settings(render(a, CFG))
        assert conf["set timeout"] == "0"
        assert conf["set default"] == "imagectl"
        assert "imagectl.mode=recovery" not in render(a, CFG)


def test_the_boot_loop_guard_still_leaves_a_way_into_recovery():
    """‏#75: דווקא המכונה שהשומר החזיר לדיסק היא זו שטכנאי צריך להיכנס
    אליה. עם ערך אחד — הערך הזה הוא השחזור, וזה עדיין הפתח היחיד לתקן
    מכונה תקועה בשטח."""
    a = answer(task={"id": "tsk_0091"}, boot_guard="exhausted")
    decision = decide(a)
    assert decision.code == "boot-loop-guard" and decision.show_menu is True

    text = render(a, CFG)
    assert settings(text)["set timeout_style"] == "menu"
    assert "imagectl.mode=recovery" in agent_entry(text)


def test_the_guarded_build_machine_keeps_its_capture_screen():
    """אותו שומר על מחשב הבנייה: מה שהדגל מוריד הוא המשימה, לא הדרך
    למסך הקליטה — וזו עדיין הדרך היחידה אליו מאתחול קר (#29)."""
    a = answer(role="build", task={"id": "tsk_4b1e"}, boot_guard="exhausted")
    assert decide(a).offer_agent is True
    assert "imagectl.mode=recovery" not in agent_entry(render(a, CFG))
