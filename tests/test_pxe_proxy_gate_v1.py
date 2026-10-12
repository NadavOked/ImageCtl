"""הכרעת נדב 11/10: ‏PXE proxy מסורב **בשרת** כש-`capabilities.classrooms()` כבוי.

‏proxy (‏#36) קיים בשביל תחנות הכיתה שמקבלות כתובת מה-DHCP של המכללה —
כלומר הוא חלק מהכיתות, ו-v1 בלי כיתות. עד כאן רק הממשק הסתיר אותו, ו-
‏`PUT /api/console/net/interfaces/{n}` עם ‏proxy=true עדיין הדליק אותו
(ממצא סקר השאריות, 11/10).

‏409 עם סיבה כשהיכולת כבויה; כיבוי proxy (‏proxy=false) ו-DHCP מלא לא
מושפעים. בדיקות ‏#36 הקיימות מדליקות את היכולת ב-monkeypatch (v1.2).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server import capabilities

from test_server_dhcp import GOOD, dhcp_server  # noqa: F401 — fixture

PROXY = {"proxy": True, "server_ip": "10.44.1.10",
         "confirm": "eth1", "confirm_proxy_broken": True}


def test_proxy_is_refused_while_classrooms_are_off(dhcp_server):  # noqa: F811
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert capabilities.classrooms() is False, "v1 — הבדיקה מניחה כיתות כבויות"
    r = admin.put("/api/console/net/interfaces/eth1", json=PROXY)
    assert r.status_code == 409, r.text
    assert "proxy" in r.json()["detail"] and "כיתות" in r.json()["detail"]
    assert not fake["proxy_applied"], "ה-proxy הוחל למרות הסירוב"
    eth1 = [i for i in admin.get("/api/console/net/interfaces").json()
            if i["name"] == "eth1"][0]
    assert eth1["proxy"] is False


def test_full_dhcp_and_proxy_off_still_work_while_classrooms_are_off(dhcp_server):  # noqa: F811
    admin = dhcp_server["admin"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": False}).status_code == 200


def test_proxy_works_when_classrooms_are_on(dhcp_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth1", json=PROXY)
    assert r.status_code == 200, r.text
    assert dhcp_server["fake"]["proxy_applied"][-1][1] is True
