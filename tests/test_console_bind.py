"""‏#770 (tracer-4 של #703): הקונסולה נקשרת פר-ממשק, לא ל-0.0.0.0.

בפריסת המעבדה שרת הקונסולה (8081) נקשר ל-``0.0.0.0`` ולכן (א) התנגש עם
שרת ה-preseed על ``10.44.3.1:8081`` ו-(ב) חשף את API הניהול על וילן
ההפצה/הכיתות — בדיוק מה שמודל ה-FW אוסר (#703: "הגבול הוא ה-socket").

מודל ה-FW: הקונסולה על ממשק המשרדים/שרתים **בלבד**. לכן הקונסולה מקבלת
דגל ``--console-host`` משלה, בנפרד מ-``--host`` (הסוכן/קיוסק על וילן
ההפצה). ברירת המחדל fail-closed — ‏loopback, לא ``0.0.0.0``: בלי כתובת
ניהול מפורשת הקונסולה אינה נגישה מאף כרטיס חיצוני, ואינה תופסת 8081
בממשקי ההפצה (עיקרון 5 — השומר עובד כברירת מחדל, לא רק אם זכרו דגל).

שרשרת הראיות שתי חוליות, כל אחת בטסט משלה: ‏``build_parser`` נותן
ברירת מחדל שאינה ``0.0.0.0``, ו-``main`` מוליך את הערך של ``--console-host``
דווקא למאזין הקונסולה — ולא לזה של הסוכן.
"""

from __future__ import annotations

from pathlib import Path

import server.app
import server.main


def test_console_host_defaults_to_loopback_not_all_interfaces() -> None:
    """הראיה הישירה: ברירת המחדל של ``--console-host`` היא loopback,
    ובוודאי לא ``0.0.0.0`` — הקונסולה לא עולה על כל הכרטיסים בלי שביקשו.

    ‏getattr עם ברירת מחדל ``0.0.0.0`` נותן כשל **התנהגותי** (AssertionError
    עם הערך בפועל) גם כשהדגל עדיין לא קיים, במקום AttributeError."""
    args = server.main.build_parser().parse_args(
        ["--server-url", "http://10.44.12.10:8080"])
    console_host = getattr(args, "console_host", "0.0.0.0")
    assert console_host == "127.0.0.1"
    assert console_host != "0.0.0.0"


def test_main_binds_console_to_console_host_not_agent_host(
        tmp_path: Path, monkeypatch) -> None:
    """‏main היא החוליה שאין לה טסט אחר: המאזין של הקונסולה חייב להיקשר
    ל-``--console-host`` (כרטיס הניהול), ולא ל-``--host`` (ההפצה). דגל
    שמתפרס ואינו מגיע דווקא למאזין הנכון נראה כמו תיקון שעובד.

    שאר החוליות (runtime, הרצת uvicorn בפועל) מנוטרלות — נבדק רק לאיזה
    ``host`` נבנה כל ``uvicorn.Config``. הקונסולה על כרטיס ניהול נפרד,
    הסוכן על 0.0.0.0 של ההפצה — ושני הערכים **שונים**."""
    import asyncio

    import uvicorn

    configs: dict = {}

    def fake_config(app, **kwargs):
        configs[app] = kwargs
        return object()

    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: "rt")
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_kiosk_app", lambda rt: "kiosk")
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    # ‏--host 0.0.0.0 (ההפצה) לצד ``--console-host`` מפורש (כרטיס הניהול):
    # הערך של ``--console-host`` חייב להגיע דווקא למאזין הקונסולה, והסוכן
    # נשאר על ``--host``.
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--host", "0.0.0.0", "--console-host", "10.44.12.10",
    ])

    server.main.main()

    assert configs["console"]["host"] == "10.44.12.10"
    assert configs["agent"]["host"] == "0.0.0.0"
    assert configs["console"]["host"] != configs["agent"]["host"]
