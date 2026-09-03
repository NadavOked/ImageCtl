"""שמות תיקיות ואימג'ים — רשימת-היתר ASCII, בשרת, ביצירה (#138).

**ההכרעה של נדב (30/08):** *"את השמות שלהם יהיו באנגלית או מספרים.
כנ"ל שמות האימג'ים"* — ובשאלה מי אוכף: *"ובשרת צריך לקרות שלא נותן לי
לכתוב בעברית"*.

**הראיה, לא הדעה.** מסך התחנה הוא קונסולת טקסט של Linux, ועברית אינה
ניתנת להצגה שם משתי סיבות בלתי תלויות: ב-initrd אין `setfont` ואין
קובץ פונט, **ולקונסולת Linux אין תמיכת RTL בכלל**. היפוך המחרוזת
מראש עובד על עברית טהורה ונשבר על מעורב — וכל שמות האימג'ים במעבדה
מעורבים (`tiny11 v2 — עם GUIDים`).

**ולמה בשרת ולא במסך:** הקונסולה היא דפדפן ומציגה עברית מצוין. בלי
אכיפה השם ייכתב **בהצלחה**, והכשל יתגלה חודשים אחר כך מול מכונה
בכיתה, כמסך של ריבועים. זה בדיוק #111 — שם משתמש בעברית עבר את היצירה
ואת בדיקת הסיסמה, והפיל את `set_cookie` ב-500 **על הסיסמה הנכונה**.

שש נקודות כניסה, ובדיקה אחת:

    [A-Za-z0-9][A-Za-z0-9 ._-]{0,47}
"""

from __future__ import annotations

import json

import pytest
pytest.importorskip("fastapi")

BAD = ["בדיקות", "Office — מעודכן", " leading", "-dash-first", "x" * 49, "a\nb", ""]
GOOD = ["LAB1", "Office 2024", "tiny11 v2", "linux_base", "a.b-c", "X"]


# --- 1+2: תיקיות ---------------------------------------------------------------


@pytest.mark.parametrize("name", BAD)
def test_a_new_folder_with_a_bad_name_is_refused(server, name):
    r = server["admin"].post("/api/console/folders", json={"name": name})
    assert r.status_code == 400, f"{name!r} התקבל"


@pytest.mark.parametrize("name", GOOD)
def test_a_new_folder_with_a_good_name_is_accepted(server, name):
    assert server["admin"].post(
        "/api/console/folders", json={"name": name}).status_code == 200, name


def test_renaming_a_folder_to_a_bad_name_is_refused(server):
    admin = server["admin"]
    assert admin.post("/api/console/folders", json={"name": "LAB1"}).status_code == 200
    r = admin.put("/api/console/folders/LAB1", json={"name": "בדיקות"})
    assert r.status_code == 400


# --- 3: עריכת אימג' ------------------------------------------------------------


@pytest.mark.parametrize("field", ["name", "folder"])
def test_editing_an_image_to_a_bad_name_is_refused(server, images_root, field):
    before = json.loads(
        (images_root / "img_7f3a91" / "manifest.json").read_text(encoding="utf-8"))
    r = server["admin"].put("/api/console/images/img_7f3a91", json={field: "בדיקות"})
    assert r.status_code == 400
    after = json.loads(
        (images_root / "img_7f3a91" / "manifest.json").read_text(encoding="utf-8"))
    assert after[field] == before[field], "המניפסט נכתב למרות הסירוב"


def test_editing_an_image_to_a_good_name_still_works(server, images_root):
    assert server["admin"].put(
        "/api/console/images/img_7f3a91",
        json={"name": "Office 2024", "folder": "Teaching"}).status_code == 200


# --- 5+6: קליטה ----------------------------------------------------------------


def capture(server, **fields):
    mac = "aa:bb:cc:00:00:41"
    admin = server["admin"]
    admin.post("/api/console/groups",
               json={"id": "grp_BUILD", "label": "BUILD", "role": "build"})
    admin.post("/api/console/machines",
               json={"mac": mac, "name": "b1", "group_id": "grp_BUILD"})
    body = {"mac": mac, "disk": "sda", "name": "Base", **fields}
    return admin.post("/api/console/tasks/capture", json=body)


@pytest.mark.parametrize("field", ["name", "folder"])
def test_a_capture_with_a_bad_name_is_refused(server, field):
    assert capture(server, **{field: "בדיקות"}).status_code == 400, field


def test_a_capture_with_good_names_is_accepted(server):
    assert capture(server, name="Base 2024", folder="Lab").status_code == 200
