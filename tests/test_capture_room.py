"""‏#411: קליטה בודקת מקום — סירוב לפני הבייט הראשון, וכשל גלוי אם הדיסק מתמלא.

שני חורים אומתו בקוד ב-#411. זה הקובץ של הראשון: ‏`server/capture.py`
אינו בודק מקום פנוי לפני שהוא כותב מחיצה. בפועל (05/09) הקליטה רצה עד
שהדיסק התמלא ב-13GB, והשרת החזיר `500` באמצע — עם קובץ מחיצה חלקי בתוך
אזור ה-`.capture-<task>` ומשימה שנשארה `running`.

עיקרון 4 ("אין זריקת בלוק בשקט — יעד שאיבד בייטים נכשל בגלוי") ועיקרון 5
("פעולה שלא הצליחה לבדוק — נכשלת, לא מוותרת") דורשים שני דברים שנבדקים כאן:

1. **סירוב לפני הבייט הראשון** — אם ההעלאה מצהירה על גודל (Content-Length)
   שאין לו מקום פנוי, הקליטה נדחית **לפני** שנפתח קובץ, בדיוק כמו הסירוב
   על "דיסק קטן מדי" עובד היום: הסיבה נוקבת בשני המספרים (נדרש, פנוי).
2. **דיסק שמתמלא תוך כדי זרם נכשל בגלוי, לא ב-500** — ‏ENOSPC נתפס,
   הקובץ החלקי מוסר, המשימה מסומנת `failed` עם סיבה, והתשובה היא `507`
   ולא קריסת שרת.

הסוכן מזרים ב-chunked (‏`curl -T` על fifo, כדי לא להיחנק ב-OOM — ראה
`agent/lib/capture.sh`), ולכן אין תמיד גודל מוצהר; המסלול השני הוא מה
שמגן על הזרם עצמו.
"""

from __future__ import annotations

import errno
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.tasks import TOKEN_HEADER
from test_capture import (
    PART_A, make_task, setup_build_machine, task_token,
)


def _put_partition(server, task_id, data=PART_A, name="p1.esp.pcl.zst"):
    return server["anon"].put(
        f"/api/v1/capture/{task_id}/files/{name}",
        content=data,
        headers={TOKEN_HEADER: task_token(server, task_id)},
    )


def test_capture_is_refused_before_the_first_byte_when_there_is_no_room(
        server, images_root, monkeypatch):
    """הסירוב הראשון: העלאה שמצהירה על גודל גדול מהמקום הפנוי נדחית לפני
    שנכתב בייט, והסיבה נוקבת בשני המספרים — נדרש ופנוי (עיקרון 5ב)."""
    import server.capture as capmod

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    free = 4  # פחות מ-len(PART_A)
    fake = types.SimpleNamespace(total=10 ** 12, used=10 ** 12 - free, free=free)
    monkeypatch.setattr(capmod.shutil, "disk_usage", lambda path: fake)

    response = _put_partition(server, created["id"])

    assert response.status_code == 507, response.text
    # שום קובץ מחיצה לא נכתב לאזור הביניים.
    assert not list(images_root.glob(".capture-*/p1.esp.pcl.zst"))
    # המשימה סומנה failed עם סיבה שנושאת את שני המספרים.
    task = server["admin"].get("/api/console/tasks").json()[0]
    assert task["state"] == "failed", task
    assert str(len(PART_A)) in task["error"], task["error"]
    assert str(free) in task["error"], task["error"]


def test_a_disk_that_fills_mid_capture_fails_loudly_not_500(
        server, images_root, monkeypatch):
    """הסירוב השני: ‏ENOSPC תוך כדי זרם. הדיסק התמלא אחרי שהזרם התחיל,
    והתשובה חייבת להיות כשל גלוי (507, ‏failed עם סיבה, קובץ חלקי מוסר) —
    לא ה-500 שהשרת החזיר בפועל ב-05/09."""
    orig_open = Path.open

    class _FullDisk:
        def __init__(self):
            self.written = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, chunk):
            self.written += len(chunk)
            if self.written > 4:
                raise OSError(errno.ENOSPC, "No space left on device")
            return len(chunk)

    def fake_open(self, *args, **kwargs):
        if str(self).endswith(".pcl.zst"):
            return _FullDisk()
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)

    mac = setup_build_machine(server)
    created = make_task(server, mac).json()

    response = _put_partition(server, created["id"])

    assert response.status_code == 507, response.text
    assert response.status_code != 500
    # הקובץ החלקי הוסר מאזור הביניים.
    staging = list(images_root.glob(".capture-*"))
    assert not any((d / "p1.esp.pcl.zst").exists() for d in staging), staging
    task = server["admin"].get("/api/console/tasks").json()[0]
    assert task["state"] == "failed", task
    assert task["error"], task
