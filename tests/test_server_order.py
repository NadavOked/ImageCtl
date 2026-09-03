"""סדר הקבוצות (נקבע בגרירה) ועצירת הסבב.

עצירת סבב הוצאה מפאנל המצב אל ההגדרות — הבדיקות כאן מוודאות שה-endpoint
נשאר עובד ומוגן, כי המסלול היחיד אליו עכשיו הוא מסך אחר.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom


def make_group(server, gid, label):
    assert server["admin"].post(
        "/api/console/groups", json={"id": gid, "label": label, "role": "classroom"}
    ).status_code == 200


def group_order(server, role="classroom"):
    return [g["id"] for g in server["admin"].get("/api/console/groups").json()
            if g["role"] == role]


def test_new_groups_land_at_the_end(server):
    for gid in ("grp_C", "grp_A", "grp_B"):
        make_group(server, gid, gid)
    # לפי סדר היצירה, לא לפי אלפבית.
    assert group_order(server) == ["grp_C", "grp_A", "grp_B"]


def test_dragging_persists_the_new_order(server):
    for gid in ("grp_A", "grp_B", "grp_C"):
        make_group(server, gid, gid)
    assert server["admin"].post(
        "/api/console/groups/order", json={"ids": ["grp_C", "grp_A", "grp_B"]}
    ).status_code == 200
    assert group_order(server) == ["grp_C", "grp_A", "grp_B"]

    # והסדר שורד יצירה של קבוצה נוספת — היא נכנסת בסוף.
    make_group(server, "grp_D", "grp_D")
    assert group_order(server) == ["grp_C", "grp_A", "grp_B", "grp_D"]


def test_reordering_rejects_unknown_ids(server):
    make_group(server, "grp_A", "A")
    r = server["admin"].post("/api/console/groups/order",
                             json={"ids": ["grp_A", "grp_GHOST"]})
    assert r.status_code == 400
    assert server["admin"].post("/api/console/groups/order",
                                json={"ids": "not a list"}).status_code == 400


@pytest.mark.parametrize("gid", ["grp_a b", "grp_../x", "grp_עברית"])
def test_an_explicit_identifier_must_be_ascii(server, gid):
    """מזהה שהוקלד במפורש ואינו חוקי נדחה בקול רם — לא מנוחש."""
    r = server["admin"].post("/api/console/groups",
                             json={"id": gid, "label": "כיתה", "role": "classroom"})
    assert r.status_code == 400


@pytest.mark.parametrize("gid", ["grp_", "", "grp_ "])
def test_a_blank_identifier_is_derived_from_the_label(server, gid):
    """טופס בלי מזהה כבר לא נכשל — המזהה נגזר מהשם (עברית מקבלת מזהה רץ).
    בעבר טופס כזה יצר קבוצה בשם "grp_" בלבד; היום הוא פשוט עובד."""
    r = server["admin"].post("/api/console/groups",
                             json={"id": gid, "label": "כיתה", "role": "classroom"})
    assert r.status_code == 200
    groups = server["admin"].get("/api/console/groups").json()
    created = [g for g in groups if g["label"] == "כיתה"]
    assert created and created[0]["id"].startswith("grp_CLASS")


def test_reordering_is_admin_only(server):
    assert server["deploy"].post(
        "/api/console/groups/order", json={"ids": []}
    ).status_code == 403


def test_the_sort_column_is_added_to_an_existing_database(tmp_path):
    """התקנה שכבר רצה בשטח לא מקבלת עמודה מ-CREATE TABLE IF NOT EXISTS."""
    import sqlite3

    from server.db import connect

    old = tmp_path / "old.db"
    raw = sqlite3.connect(old)
    raw.execute(
        "CREATE TABLE groups (id TEXT PRIMARY KEY, label TEXT NOT NULL,"
        " role TEXT NOT NULL CHECK (role IN ('build','cloner','classroom')))"
    )
    raw.execute("INSERT INTO groups VALUES ('grp_OLD', 'ותיקה', 'classroom')")
    raw.commit()
    raw.close()

    conn = connect(old)
    row = conn.execute("SELECT id, sort FROM groups WHERE id = 'grp_OLD'").fetchone()
    assert row["sort"] == 0            # הנתונים הישנים שרדו, עם ברירת מחדל


# --- עצירת סבב ---------------------------------------------------------------


def test_closing_a_round_still_works_from_its_new_home(server):
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))

    assert server["admin"].post(f"/api/console/sessions/{session}/close").status_code == 200
    assert server["admin"].get("/api/console/overview").json()["session"] is None
