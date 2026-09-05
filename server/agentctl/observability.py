"""יומן אירועים מרכזי — שורת JSON לאירוע, וסיכום שנקרא ממנו.

הועבר מהחבילה החיצונית כמעט ללא שינוי. שני דברים שכן:
‏`from __future__ import annotations` הוסר (‏`CLAUDE.md` — הוא שבר
FastAPI כאן פעמיים), ו-`summarize` מבחין בין "אין יומן" לבין "יומן
ריק" בשדה `exists`. הישן החזיר `events: 0` לשניהם, וזה בדיוק קיפול
"לא הצלחנו לבדוק" לתוך "בדקנו, אין אירועים" (עיקרון 5).

שורה פגומה ביומן **זורקת** ואינה מדולגת, מאותה סיבה.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def emit_event(
    path: str | Path, event_type: str, *, task_id: str, data: dict[str, Any]
) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "task_id": task_id,
        "data": data,
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"exists": False, "events": 0, "by_type": {}, "tasks": 0}

    events = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))

    return {
        "exists": True,
        "events": len(events),
        "by_type": dict(Counter(item["event_type"] for item in events)),
        "tasks": len({item["task_id"] for item in events}),
    }
