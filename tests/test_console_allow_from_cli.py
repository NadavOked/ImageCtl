"""#151: ‏``--console-allow-from`` — הרשימה המותרת אינה מקודדת קשיח.

שלוש חוליות: ברירת המחדל היא ``None`` (השומר כבוי, כמו היום), ‏CIDR שגוי
נדחה לפני שהשרת עולה בכלל (עיקרון 5), וכשהדגל תקין — main מעביר את
הרשתות המפוענחות דווקא ל-``create_runtime`` (לא נבלע באמצע).
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import pytest

import server.app
import server.main


def test_console_allow_from_defaults_to_none():
    args = server.main.build_parser().parse_args(
        ["--server-url", "http://10.44.12.10:8080"])
    assert args.console_allow_from is None


def test_console_allow_from_collects_multiple_values():
    args = server.main.build_parser().parse_args([
        "--server-url", "http://10.44.12.10:8080",
        "--console-allow-from", "10.10.10.1",
        "--console-allow-from", "10.44.0.0/24",
    ])
    assert args.console_allow_from == ["10.10.10.1", "10.44.0.0/24"]


def test_invalid_cidr_is_rejected_before_server_starts(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-allow-from", "not-an-ip",
    ])
    with pytest.raises(SystemExit):
        server.main.main()


def test_main_passes_parsed_networks_to_create_runtime(
        tmp_path: Path, monkeypatch):
    """הראיה הישירה: הרשתות המפוענחות (לא המחרוזות הגולמיות) מגיעות
    דווקא לפרמטר ``console_allowed_networks`` של ``create_runtime``."""
    import asyncio

    import uvicorn

    captured: dict = {}

    def fake_create_runtime(*args, **kwargs):
        captured["console_allowed_networks"] = kwargs.get("console_allowed_networks")
        return "rt"

    monkeypatch.setattr(server.app, "create_runtime", fake_create_runtime)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_kiosk_app", lambda rt: "kiosk")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kwargs: object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-allow-from", "10.10.10.1",
        "--console-allow-from", "10.44.0.0/24",
    ])

    server.main.main()

    assert captured["console_allowed_networks"] == (
        ipaddress.ip_network("10.10.10.1/32"),
        ipaddress.ip_network("10.44.0.0/24"),
    )
