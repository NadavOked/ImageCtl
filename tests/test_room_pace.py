"""‏#410 — זמן שעבר, קצב והערכת סיום על מסך החדר.

הנתונים היו בשרת כל הזמן: `started_at` של הגל, ו-`bytes_written`/`moved_at`
לכל מגירה (#552). מה שלא היה הוא הגזירה — ולכן המסך הגרפי אמר בהערה
`/* bytes, no rate in the state */`. הקצב הוא ממוצע מאז שהגל יצא
(‏`bytes_written` חלקי `moved_at − started_at`), ‏ETA = היתרה חלקי הקצב.

⚠️ ‏`None` ≠ `0` (עיקרון 5): מגירה שלא נמדדה אינה "עצרה". כל דרך שבה
המדידה עצמה נכשלת — אין חותמת, אין בייטים, אפס שניות, לא כותבת — חייבת
לצאת `None`, ולא מספר שנראה כמו מדידה.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import room as room_mod  # noqa: E402
from test_server_room import (  # noqa: E402,F401
    CLONER1, CLONER2, cloner_hello, room, room_server,
)

T0 = "2026-09-19T10:00:00+00:00"
T100 = "2026-09-19T10:01:40+00:00"          # 100 שניות אחרי T0
GB = 1_000_000_000


def _pace(**target):
    base = {"state": "writing", "bytes_written": 5 * GB, "bytes_total": 15 * GB,
            "moved_at": T100}
    return room_mod._pace({**base, **target}, T0)


def test_rate_is_bytes_over_the_time_since_the_wave_started():
    pace = _pace()
    assert pace["rate_bps"] == 50_000_000          # 5GB / 100s
    assert pace["eta_s"] == 200                    # 10GB נשארו / 50MB/s


def test_eta_is_unknown_without_a_total_but_the_rate_still_reads():
    pace = _pace(bytes_total=0)
    assert pace["rate_bps"] == 50_000_000
    assert pace["eta_s"] is None


@pytest.mark.parametrize("broken", [
    {"moved_at": None}, {"moved_at": "yesterday"}, {"bytes_written": 0},
    {"moved_at": T0}, {"state": "done"}, {"state": "verifying"},
])
def test_a_measurement_that_cannot_be_made_is_none_not_zero(broken):
    """אין חותמת / חותמת פגומה / אפס בייטים / אפס שניות / לא כותבת —
    כולם "לא נמדד". ‏0 היה אומר "נמדד: עצרה"."""
    pace = _pace(**broken)
    assert pace == {"rate_bps": None, "eta_s": None}


def test_no_wave_start_means_no_rate_at_all():
    assert room_mod._pace({"state": "writing", "bytes_written": GB,
                           "bytes_total": 2 * GB, "moved_at": T100}, None) == \
        {"rate_bps": None, "eta_s": None}


def _machines(*drawers):
    return [{"drawer_list": list(drawers)}]


def test_the_wave_rate_is_the_slowest_drawer_and_the_eta_the_longest():
    """הגל נגמר כשהמגירה האחרונה נגמרת; ממוצע היה מסתיר מגירה מפגרת."""
    pace = room_mod._wave_pace(
        (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(timespec="seconds"),
        _machines({"state": "writing", "rate_bps": 80_000_000, "eta_s": 100},
                  {"state": "writing", "rate_bps": 20_000_000, "eta_s": 400},
                  {"state": "done", "rate_bps": None, "eta_s": None}))
    assert pace["rate_bps"] == 20_000_000
    assert pace["eta_s"] == 400
    assert 89 <= pace["elapsed_s"] <= 95


def test_a_wave_that_did_not_start_has_no_elapsed_and_an_unmeasured_drawer_does_not_vote():
    pace = room_mod._wave_pace(
        None, _machines({"state": "writing", "rate_bps": None, "eta_s": None}))
    assert pace == {"started_at": None, "elapsed_s": None,
                    "rate_bps": None, "eta_s": None}


def test_the_numbers_reach_the_room_screen_from_a_real_wave(room_server):
    """קצה-לקצה: גל רץ, מגירה דיווחה בייטים — ‏GET /room נושא זמן, קצב
    ו-ETA בראש ולכל מגירה. ‏`started_at` מוזז 100 שניות אחורה: בטסט הגל
    יוצא והדיווח מגיע באותה שנייה, ואפס שניות הוא בצדק "לא נמדד"."""
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    assert admin.post("/api/console/room",
                      json={"image_id": "img_7f3a91", "target_drives": 4}).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert admin.post("/api/console/room/start").status_code == 200
    wave = ctx.conn.execute("SELECT wave_session_id FROM room_rounds").fetchone()[0]
    ago = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat(timespec="seconds")
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (ago, wave))
    ctx.conn.commit()
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [{"dev": "sda", "bytes_written": 5 * GB, "bytes_total": 15 * GB,
                     "state": "writing"}],
    }).status_code == 200

    view = room(admin)
    rnd = view["round"]
    assert rnd["started_at"] == ago
    assert 99 <= rnd["elapsed_s"] <= 105
    assert rnd["rate_bps"] is not None and 45_000_000 <= rnd["rate_bps"] <= 50_000_000
    assert rnd["eta_s"] is not None and 200 <= rnd["eta_s"] <= 230
    m1 = next(m for m in view["machines"] if m["mac"] == CLONER1)
    sda = next(d for d in m1["drawer_list"] if d["dev"] == "sda")
    assert sda["rate_bps"] == rnd["rate_bps"] and sda["eta_s"] == rnd["eta_s"]
    sdb = next(d for d in m1["drawer_list"] if d["dev"] == "sdb")
    assert sdb["rate_bps"] is None and sdb["eta_s"] is None, "מגירה שלא דיווחה — לא נמדדה"
    m2 = next(m for m in view["machines"] if m["mac"] == CLONER2)
    assert all(d["rate_bps"] is None for d in m2["drawer_list"])


def test_before_the_wave_runs_nothing_is_measured(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post("/api/console/room",
                      json={"image_id": "img_7f3a91", "target_drives": 4}).status_code == 200
    cloner_hello(anon, CLONER1, ["S1"])
    rnd = room(admin)["round"]
    assert rnd["wave_state"] == "open"
    assert rnd["started_at"] is None and rnd["elapsed_s"] is None
    assert rnd["rate_bps"] is None and rnd["eta_s"] is None
