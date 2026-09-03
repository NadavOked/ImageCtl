"""קליטת אימג' — זרימה 13.1, מהקונסולה אל מחשב הבנייה ובחזרה.

הבדיקות משחקות את מחשב הבנייה: יוצרות משימה, מקבלות אותה ב-hello,
מעלות קבצים ומניפסט, ומוודאות שהאימג' נכנס לספרייה — ובעיקר, שאימג'
פגום *לא* נכנס.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("fastapi")

from conftest import (
    ESP_GUID, LINUX_GUID, RECOVERY_GUID, SWAP_GUID, WINDOWS_GUID, hello_body,
)

PART_A = b"partition-one-bytes"
PART_B = b"partition-three-bytes"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def setup_build_machine(server, mac="aa:bb:cc:00:00:10"):
    admin = server["admin"]
    admin.post("/api/console/machines",
               json={"mac": mac, "name": "מחשב בנייה", "group_id": "grp_BUILD"})
    return mac


def make_task(server, mac, **overrides):
    body = {"mac": mac, "disk": "sda", "name": "Windows 11 Base",
            "description": "נקלט בבדיקה", "folder": "Office"}
    body.update(overrides)
    return server["admin"].post("/api/console/tasks/capture", json=body)


def manifest_for(parts=None):
    return {
        "schema": 1, "family": 256,
        "source_disk_bytes": 256060514304, "min_target_bytes": 256060514304,
        "scheme": "gpt", "sector_size": 512,
        "partitions": parts if parts is not None else [
            {"index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
             "start_sector": 2048, "size_bytes": 104857600, "used_bytes": 31457280,
             "file": "p1.esp.pcl.zst", "sha256": sha(PART_A), "expandable": False},
            {"index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
             "start_sector": 1085440, "size_bytes": 254803968000,
             "used_bytes": 84509376512,
             "file": "p3.windows.pcl.zst", "sha256": sha(PART_B), "expandable": True},
        ],
        "total_compressed_bytes": len(PART_A) + len(PART_B),
        "compression": "zstd-9",
    }


def do_capture(server, task_id, manifest=None, files=None):
    anon = server["anon"]
    for name, data in (files or {"p1.esp.pcl.zst": PART_A,
                                 "p3.windows.pcl.zst": PART_B}).items():
        anon.put(f"/api/v1/capture/{task_id}/files/{name}", content=data)
    return anon.put(f"/api/v1/capture/{task_id}/manifest",
                    content=json.dumps(manifest or manifest_for()).encode())


# --- יצירת המשימה -----------------------------------------------------------


def test_capture_is_only_offered_to_a_build_machine(server):
    from conftest import setup_classroom
    ids = setup_classroom(server)
    r = make_task(server, ids["mac1"])
    assert r.status_code == 400
    assert "בניית אימג'ים" in r.json()["detail"]


def test_an_unregistered_machine_cannot_be_targeted(server):
    r = make_task(server, "ff:ff:ff:ff:ff:ff")
    assert r.status_code == 400


def test_one_open_task_per_machine(server):
    mac = setup_build_machine(server)
    assert make_task(server, mac).status_code == 200
    assert make_task(server, mac).status_code == 409


def test_creating_a_task_is_admin_only(server):
    mac = setup_build_machine(server)
    assert server["deploy"].post(
        "/api/console/tasks/capture",
        json={"mac": mac, "disk": "sda", "name": "x"}).status_code == 403


# --- המשימה מגיעה לסוכן ------------------------------------------------------


def test_the_build_machine_receives_the_task_in_hello(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    task = answer["task"]
    assert task["id"] == created["id"]
    assert task["type"] == "capture"
    assert task["disk"] == "sda"
    assert task["image_id"] == created["image_id"]
    assert task["name"] == "Windows 11 Base"
    assert task["folder"] == "Office"


def test_a_task_beats_a_session(server):
    """משימה מופנית למכונה; סבב לקבוצה. המכונה לא תצטרף לסבב."""
    mac = setup_build_machine(server)
    server["admin"].post("/api/console/groups",
                         json={"id": "grp_B2", "label": "בנייה 2", "role": "build"})
    make_task(server, mac)
    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    assert answer["task"] is not None
    assert answer["session"] is None


def test_a_machine_without_a_task_still_boots_locally(server):
    mac = setup_build_machine(server)
    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    assert answer["task"] is None and answer["session"] is None


# --- העלאה ואימות ------------------------------------------------------------


def test_a_full_capture_lands_in_the_library(server, images_root):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    response = do_capture(server, created["id"])
    assert response.status_code == 200

    folder = images_root / created["image_id"]
    assert (folder / "p1.esp.pcl.zst").read_bytes() == PART_A
    raw = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    # שמות התצוגה נקבעים בקונסולה, לא במחשב הבנייה.
    assert raw["id"] == created["image_id"]
    assert raw["name"] == "Windows 11 Base"
    assert raw["folder"] == "Office"
    assert raw["created_by"] == "noc"

    listed = {m["id"]: m for m in server["admin"].get("/api/console/images").json()}
    assert created["image_id"] in listed

    tasks = server["admin"].get("/api/console/tasks").json()
    assert tasks[0]["state"] == "done"


def test_intake_stores_the_real_requirement_not_the_source_disk_size(server, images_root):
    """‏#82: הסוכן שולח `min_target_bytes` = גודל דיסק המקור, ומה שמונח
    בספרייה הוא הדרישה האמיתית — סוף הפריסה.

    הנרמול כאן, ולא רק בקריאה, כי הדיסק הוא מקור האמת (עיקרון 3): אסור
    שיישב בתיקייה ערך שאף קורא לא מתכוון לכבד. סוכן ישן שממשיך לשלוח
    את גודל המקור נקלט כרגיל — אין כאן שינוי שובר, ולכן `schema` נשאר 1.
    """
    from server.images import required_bytes

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    sent = manifest_for()
    assert do_capture(server, created["id"], manifest=sent).status_code == 200

    raw = json.loads((images_root / created["image_id"] / "manifest.json")
                     .read_text(encoding="utf-8"))
    assert raw["min_target_bytes"] == required_bytes(sent)
    assert raw["min_target_bytes"] < sent["min_target_bytes"]
    # גודל דיסק המקור עצמו נשמר כפי שהיה — הוא תיעוד, לא דרישה.
    assert raw["source_disk_bytes"] == sent["source_disk_bytes"]


def test_a_manifest_whose_size_requirement_is_unknowable_is_refused(server):
    """בלי גיאומטריה ובלי ערך מוצהר תקין אין החלטה "נכנס / לא נכנס",
    והאימג' היה נכנס לספרייה כדי להידלג שם בשקט בכל סריקה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    bad = manifest_for()
    for part in bad["partitions"]:
        part.pop("size_bytes")
    bad.pop("min_target_bytes")

    response = do_capture(server, created["id"], manifest=bad)
    assert response.status_code == 400
    assert "how much room" in response.json()["detail"]


def test_a_tampered_partition_never_enters_the_library(server, images_root):
    """האימות הזה הוא כל הסיבה שהשרת נוגע במניפסט: אימג' פגום נתפס
    בקליטה ולא מול כיתה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    response = do_capture(server, created["id"],
                          files={"p1.esp.pcl.zst": PART_A,
                                 "p3.windows.pcl.zst": b"corrupted"})
    assert response.status_code == 400
    assert "sha256" in response.json()["detail"]
    assert not (images_root / created["image_id"]).exists()
    assert not list(images_root.glob(".capture-*"))          # השטחה נוקתה

    tasks = server["admin"].get("/api/console/tasks").json()
    assert tasks[0]["state"] == "failed"


def test_a_missing_partition_file_is_caught(server, images_root):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    response = do_capture(server, created["id"], files={"p1.esp.pcl.zst": PART_A})
    assert response.status_code == 400
    assert "never uploaded" in response.json()["detail"]
    assert not (images_root / created["image_id"]).exists()


def test_a_task_whose_image_id_is_not_an_id_never_becomes_a_folder(server, images_root):
    """‏#110: המזהה הוא שם התיקייה, והכלל נאכף **גם כאן** ולא רק בייבוא.

    המזהה נוצר בשרת, ולכן זה מצב שאינו אמור לקרות — וזה בדיוק מה שהופך
    אותו למסוכן: אם השורה בבסיס הנתונים אינה מה שחשבנו, ‏`root / image_id`
    הוא נתיב שיוצא מהשורש, ואף אחד לא היה מגלה.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    conn = server["ctx"].conn
    conn.execute("UPDATE tasks SET image_id = ? WHERE id = ?",
                 ("../evil", created["id"]))
    conn.commit()

    response = do_capture(server, created["id"])
    assert response.status_code == 500
    assert "malformed image id" in response.json()["detail"]
    assert not (images_root.parent / "evil").exists()
    # מה שמונח בספרייה הוא מה שהיה בה לפני — אזורי עבודה (נקודה) בצד.
    assert sorted(p.name for p in images_root.iterdir()
                  if not p.name.startswith(".")) == ["img_2c8e04", "img_7f3a91"]
    assert server["admin"].get("/api/console/tasks").json()[0]["state"] == "failed"


@pytest.mark.parametrize("name", ["../escape", "manifest.json", "p1.esp.pcl", "evil.sh"])
def test_partition_file_names_are_whitelisted(server, name):
    """שם הקובץ מגיע ממכונה ברשת הלימודית — רשימה לבנה, לא סינון."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].put(f"/api/v1/capture/{created['id']}/files/{name}",
                           content=b"x")
    assert r.status_code in (400, 404)


def test_only_the_last_partition_may_be_expandable(server):
    """סעיף 1: הרחבה רק במחיצה האחרונה — אחרת recovery יתנפח במקום Windows."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    parts = manifest_for()["partitions"]
    parts[0]["expandable"] = True
    response = do_capture(server, created["id"], manifest=manifest_for(parts))
    assert response.status_code == 400
    assert "expandable" in response.json()["detail"]


def test_a_root_followed_only_by_swap_is_still_expandable(server):
    """‏#46: ה-swap של מתקין דביאן יושבת בזנב, וה-root שלפניה מורחבת.

    ‏swap אינה משודרת ונוצרת מחדש מהמניפסט, ולכן הסוכן מעתיק אותה לזנב
    הדיסק ומרחיב את מה שלפניה — והשרת חייב לקבל מניפסט כזה. הכלל הישן
    ("רק ממש האחרונה") היה אומר שאימג' Linux טיפוסי לא מורחב לעולם.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    parts = manifest_for()["partitions"]
    parts.append({"index": 4, "type_guid": SWAP_GUID, "role": "swap",
                  "fs": "swap", "start_sector": 498888704,
                  "size_bytes": 4294967296, "used_bytes": 0,
                  "file": None, "sha256": None, "expandable": False})
    response = do_capture(server, created["id"], manifest=manifest_for(parts))
    assert response.status_code == 200, response.text


def test_a_recovery_partition_after_the_expandable_one_is_accepted(server):
    """‏#58 — **היפוך מכוון** של הכלל הקודם ("רק swap אחרי המורחבת").

    זו הפריסה של Windows 11: ‏esp · msr · windows · recovery. הכלל הישן
    אמר שאף אימג' Windows לא יורחב לעולם. ‏recovery אינה נבראת מחדש כמו
    swap — היא נכתבת מקובץ הזרם שלה, במיקום החדש שבזנב, לפי האינדקס.
    """
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    parts = manifest_for()["partitions"]
    parts.append({"index": 4, "type_guid": RECOVERY_GUID, "role": "recovery",
                  "fs": "ntfs", "start_sector": 498888704,
                  "size_bytes": 1073741824, "used_bytes": 524288000,
                  "file": "p4.recovery.pcl.zst", "sha256": sha(PART_A),
                  "expandable": False})
    response = do_capture(
        server, created["id"], manifest=manifest_for(parts),
        files={"p1.esp.pcl.zst": PART_A, "p3.windows.pcl.zst": PART_B,
               "p4.recovery.pcl.zst": PART_A},
    )
    assert response.status_code == 200, response.text


def test_a_system_partition_after_the_expandable_one_is_still_refused(server):
    """הקו לא נמחק, הוא זז: מחיצת windows/linux אחרי המורחבת פירושה
    שהמועמד פשוט אינו האחרון על הדיסק — ולמתוח אותו היה דורס אותה."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    parts = manifest_for()["partitions"]
    parts.append({"index": 4, "type_guid": WINDOWS_GUID, "role": "windows",
                  "fs": "ntfs", "start_sector": 498888704,
                  "size_bytes": 1073741824, "used_bytes": 524288000,
                  "file": "p4.windows.pcl.zst", "sha256": sha(PART_A),
                  "expandable": False})
    response = do_capture(server, created["id"], manifest=manifest_for(parts))
    assert response.status_code == 400
    assert "expandable" in response.json()["detail"]


def test_the_line_is_drawn_by_start_sector_not_by_list_order(server):
    """אימג' הענן של דביאן (#58): השורש הוא מחיצה 1 והוא ראשון ברשימה,
    אבל אחרון על הדיסק. הכלל נמדד לפי `start_sector` — בדיוק כמו בסוכן —
    ולכן המחיצות שרשומות אחריו, ויושבות לפניו, אינן פוסלות אותו."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    parts = [
        {"index": 1, "type_guid": LINUX_GUID, "role": "linux", "fs": "ext4",
         "start_sector": 2099200, "size_bytes": 45000687616,
         "used_bytes": 2147483648, "file": "p1.linux.pcl.zst",
         "sha256": sha(PART_A), "expandable": True},
        {"index": 15, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
         "start_sector": 10240, "size_bytes": 111149056, "used_bytes": 8388608,
         "file": "p15.esp.pcl.zst", "sha256": sha(PART_B), "expandable": False},
    ]
    response = do_capture(
        server, created["id"], manifest=manifest_for(parts),
        files={"p1.linux.pcl.zst": PART_A, "p15.esp.pcl.zst": PART_B},
    )
    assert response.status_code == 200, response.text


def test_an_unknown_family_is_refused(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    bad = manifest_for()
    bad["family"] = 1024
    assert do_capture(server, created["id"], manifest=bad).status_code == 400


def test_uploading_to_a_finished_task_is_refused(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    do_capture(server, created["id"])
    r = server["anon"].put(f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
                           content=PART_A)
    assert r.status_code == 404


# --- דיווח התקדמות והתקדמות בקונסולה ----------------------------------------


def test_capture_progress_uses_task_id(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    report = {
        "task_id": created["id"], "mac": mac, "state": "capturing",
        "targets": [{"dev": "sda", "bytes_written": 4096,
                     "bytes_total": 100000, "state": "capturing"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    task = server["admin"].get("/api/console/tasks").json()[0]
    assert task["state"] == "running"
    assert task["bytes_written"] == 4096


def test_progress_from_the_wrong_machine_is_rejected(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    r = server["anon"].post("/api/v1/agent/progress", json={
        "task_id": created["id"], "mac": "ff:ff:ff:ff:ff:ff",
        "state": "capturing", "targets": []})
    assert r.status_code == 400
    assert r.json()["code"] == "not_member"


def test_cancelling_clears_the_staging_area(server, images_root):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    server["anon"].put(f"/api/v1/capture/{created['id']}/files/p1.esp.pcl.zst",
                       content=PART_A)
    assert list(images_root.glob(".capture-*"))
    assert server["admin"].post(
        f"/api/console/tasks/{created['id']}/cancel").status_code == 200
    assert not list(images_root.glob(".capture-*"))
    # והמכונה כבר לא מקבלת אותה ב-hello.
    answer = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac)).json()
    assert answer["task"] is None


def test_the_journal_tells_the_capture_story_in_hebrew(server):
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    do_capture(server, created["id"])
    rows = {r["event"]: r for r in server["admin"].get("/api/console/journal").json()}
    assert rows["capture_start"]["label"] == "קליטת אימג' הוזמנה"
    assert rows["capture_done"]["label"] == "אימג' נקלט"
    assert "Windows 11 Base" in rows["capture_done"]["text"]
