"""ערכת נושא לפי משתמש — נשמרת בשרת (#1093).

ברירת מחדל auto; PUT light/dark/auto חוזר ב-GET /me; ערך זר 422;
משתמש ב' אינו מושפע מבחירה של א'.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except ImportError:                                    # pragma: no cover
    TestClient = None


def test_me_theme_defaults_to_auto(server):
    assert server["admin"].get("/api/console/me").json()["theme"] == "auto"
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: להפצה אין קונסולה


@pytest.mark.parametrize("theme", ["light", "dark", "auto"])
def test_put_theme_is_returned_by_me(server, theme):
    admin = server["admin"]
    r = admin.put("/api/console/me/theme", json={"theme": theme})
    assert r.status_code == 200, r.text
    assert admin.get("/api/console/me").json()["theme"] == theme


def test_a_foreign_theme_is_422_and_does_not_stick(server):
    admin = server["admin"]
    admin.put("/api/console/me/theme", json={"theme": "dark"})
    r = admin.put("/api/console/me/theme", json={"theme": "blue"})
    assert r.status_code == 422, r.text
    assert admin.get("/api/console/me").json()["theme"] == "dark"


def test_theme_is_per_user(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "other", "password": "Other-pass-1!", "role": "admin",
    }).status_code == 200
    other = TestClient(server["app"])
    assert other.post("/api/console/login", json={
        "username": "other", "password": "Other-pass-1!",
    }).status_code == 200

    assert admin.put("/api/console/me/theme", json={"theme": "light"}).status_code == 200
    assert other.get("/api/console/me").json()["theme"] == "auto"
    assert other.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200
    assert admin.get("/api/console/me").json()["theme"] == "light"
    assert other.get("/api/console/me").json()["theme"] == "dark"
