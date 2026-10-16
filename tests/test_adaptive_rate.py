"""קצב מולטיקאסט אוטומטי לגמרי — הכרעת נדב 11/10: "אני לא נוגע בזה,
והשרת לבד מחליט".

על גבי #447 (‏`server/drive_rates.py`, שעד כאן רק הוריד מתחת לתקרה):

* **פתיחה ב-600m** — נמדד נקי, ‏re-xmits=0 (מ.19, ‏06/09). ‏`IMAGECTL_MAX_BITRATE`,
  כשהוגדר, הוא הגג; כשלא — הגג הוא 900m.
* **למעלה** — רק על ראיה חיובית לגל נקי: אפס שידורים חוזרים **וגם** כל
  כונן בגל עמד בקצב. מדידה שחסרה = נשאר במקום (עיקרון 5).
* **למטה** — שידורים חוזרים מעל סף קטן (‏‎-25%), או כונן איטי מוכר (#447).
* **רצפה** — רבע מקצב הפתיחה; **גג** — לעולם לא מעליו.
* **זיכרון** — הגל האחרון עם אותם כוננים הוא נקודת המוצא.
"""

from __future__ import annotations

import json

import pytest

from server import db, drive_rates

MB = 1_000_000
SENT = 10 ** 10                     # ‏10GB בגל — ‏6.87 מיליון בלוקים של 1456
CLEAN = 0
FEW = 10                            # מעל אפס, מתחת לסף — "לא נקי, לא רע"
MANY = 100_000                      # ‏~1.5% מהבלוקים — מעל הסף


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def _drive(serial, model="EVO 870", mac="aa:bb:cc:00:00:21", port=1):
    return {"serial": serial, "model": model, "mac": mac, "port": port}


def _kept(conn, session, serial, rate_bps, cap_bps, model="EVO 870"):
    conn.execute(
        "INSERT INTO drive_rates (serial, model, session_id, rate_bps, cap_bps,"
        " bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
        (serial, model, session, rate_bps, cap_bps, rate_bps * 1000, 1000,
         "2026-10-11T10:00:00+00:00"))
    conn.commit()


STREAM_FULL = 0.95                  # ‏udp-sender מגיע ל-95.7% מהתקרה (06/09)


def _wave(conn, session, serials=("S1", "S2"), ceiling=None, loss=CLEAN,
          sent=SENT, kept_up=True, measured=True, stream=STREAM_FULL,
          stream_s=600):
    """גל שלם: השרת בחר קצב ויצא, כל כונן השאיר דגימה, והשולח דיווח.
    ‏`stream` — הקצב הממוצע של הזרם עצמו, כחלק מקצב הגל (‏None = לא נמדד)."""
    rate = drive_rates.decide(conn, session, lambda: [_drive(s) for s in serials],
                              ceiling)
    cap = drive_rates.parse_bitrate(rate) // 8
    for serial in serials:
        _kept(conn, session, serial, cap if kept_up else cap // 2, cap)
    if measured:
        drive_rates.record_stream(conn, session, {
            "sent_bytes": sent, "loss_blocks": loss,
            "stream_bps": int(cap * stream) if stream is not None else None,
            "stream_s": stream_s})
    return rate


def _next(conn, serials=("S1", "S2"), ceiling=None):
    return drive_rates.choose(conn, [_drive(s) for s in serials], ceiling)


# --- הפתיחה -----------------------------------------------------------------


def test_without_a_configured_cap_the_wave_opens_at_600m(conn):
    """שרת שהותקן מה-ISO אינו מגדיר `IMAGECTL_MAX_BITRATE`. עד היום — אין
    `--max-bitrate` בכלל; מעכשיו — קצב הפתיחה, והגג הוא 900m."""
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start"
    assert got["ceiling"] == "900m"
    assert got["previous"] is None


def test_a_configured_cap_below_600m_is_where_it_opens(conn):
    got = _next(conn, ceiling="200m")
    assert got["max_bitrate"] == "200m"
    assert got["basis"] == "start"
    assert got["ceiling"] == "200m"


def test_a_configured_cap_above_900m_raises_the_roof_not_the_start(conn):
    # ‏15/10 (ב): מחשב שיכפול שמהירותו לא ידועה מונח 1Gb/s — ואז הגג 900m גם
    # מתחת ל-"1g". כשהמקבל הוגדר 10G — הגג המוגדר הוא שקובע, כמו קודם.
    from server import nic_speed                                # noqa: PLC0415
    assert _next(conn, ceiling="1g")["ceiling"] == "900m"
    nic_speed.set_manual(conn, _drive("S1")["mac"], 10000)
    got = _next(conn, ceiling="1g")
    assert got["max_bitrate"] == "600m"
    assert got["ceiling"] == "1000m" and got["cap_by"] == "configured"


# --- למעלה: רק על ראיה --------------------------------------------------------


def test_a_clean_wave_raises_the_next_one_a_step(conn):
    assert _wave(conn, "ses_1") == "600m"
    got = _next(conn)
    assert got["max_bitrate"] == "700m"
    assert got["basis"] == "up"
    assert got["previous"]["session_id"] == "ses_1"
    assert got["previous"]["max_bitrate"] == "600m"
    assert got["previous"]["loss_blocks"] == 0


def test_clean_waves_climb_one_step_at_a_time_to_the_roof(conn):
    seen = [_wave(conn, f"ses_{n}") for n in range(6)]
    assert seen == ["600m", "700m", "800m", "900m", "900m", "900m"]
    assert _next(conn)["basis"] == "ceiling"


def test_an_unmeasured_wave_does_not_raise(conn):
    """בקרה שלילית — עלייה בלי ראיה: השולח לא דיווח (לוג שלא נקרא, סימולציה,
    גל שנעצר לפני הסוף). "לא נמדד" אינו "נקי" — נשארים במקום."""
    _wave(conn, "ses_1", measured=False)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "unmeasured"


@pytest.mark.parametrize("stream", [
    {"sent_bytes": None, "loss_blocks": None},
    {"sent_bytes": SENT, "loss_blocks": None},
    {"sent_bytes": 0, "loss_blocks": 0},          # לא שודר כלום — אין ממה ללמוד
    {"sent_bytes": None, "loss_blocks": 0},
])
def test_a_missing_count_is_not_zero_retransmissions(conn, stream):
    _wave(conn, "ses_1", measured=False)
    drive_rates.record_stream(conn, "ses_1", stream)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "unmeasured"


def test_a_few_retransmissions_hold_the_rate(conn):
    _wave(conn, "ses_1", loss=FEW)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "held"


def test_a_clean_stream_with_a_drive_that_did_not_keep_up_does_not_raise(conn):
    """בקרה שלילית — עלייה בלי ראיה: הזרם נקי, אבל כונן אחד כתב חצי מקצב
    הגל. הזרם נקי כי הוא חיכה לו — זו אינה רשות לעלות."""
    _wave(conn, "ses_1", kept_up=False)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "held"


def test_a_drive_of_the_wave_with_no_sample_is_no_evidence(conn):
    """כונן שנכשל, או גל קצר מ-60 שניות — אין לו דגימה, ולכן אין ראיה
    שהוא עמד בקצב."""
    drive_rates.decide(conn, "ses_1", lambda: [_drive("S1"), _drive("S2")], None)
    _kept(conn, "ses_1", "S1", 75 * MB, 75 * MB)
    drive_rates.record_stream(conn, "ses_1", {"sent_bytes": SENT, "loss_blocks": 0})
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "held"


def test_a_clean_wave_whose_stream_ran_slow_does_not_raise(conn):
    """בקרה שלילית — התנאי השני (המתאם, 11/10): ‏`bytes_written` של כונן נספר
    אחרי פריסת הדחיסה, ולכן כונן שהאט את הזרם לחצי עדיין "עמד בקצב". הזרם
    עצמו — מה ש-udp-sender ספר — רץ ב-50% מקצב הגל: אין רשות לעלות."""
    _wave(conn, "ses_1", stream=0.5)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "held"
    assert got["previous"]["stream_bps"] == 37_500_000


def test_a_stream_at_80_percent_of_the_wave_rate_is_enough(conn):
    _wave(conn, "ses_1", stream=0.8)
    assert _next(conn)["max_bitrate"] == "700m"


@pytest.mark.parametrize("kw", [{"stream": None}, {"stream_s": None}, {"stream_s": 30}])
def test_an_unmeasured_stream_rate_does_not_raise(conn, kw):
    """עיקרון 5: קצב זרם שלא נמדד (או נמדד פחות מדקה) אינו "הזרם רץ בקצב"."""
    _wave(conn, "ses_1", **kw)
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "unmeasured"


def test_a_slow_stream_does_not_stop_a_drop(conn):
    """הזרם האיטי רק חוסם עלייה; שידורים חוזרים עדיין מורידים."""
    _wave(conn, "ses_1", loss=MANY, stream=None)
    assert _next(conn)["max_bitrate"] == "450m"


# --- מחשב בנייה כמשדר (#715) ---------------------------------------------------


def test_a_direct_wave_opens_at_the_start_rate_and_never_climbs(conn):
    """המונה של udp-sender במחשב הבנייה, לא בשרת — אין ראיה לגל נקי. גם
    אחרי גל נקי מספרייה עם אותם כוננים: פתיחה."""
    _wave(conn, "ses_1")
    got = drive_rates.choose(conn, [_drive("S1"), _drive("S2")], None, direct=True)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start" and got["previous"] is None
    assert got["direct"] is True


def test_a_direct_wave_respects_the_cap_and_a_known_slow_drive(conn):
    assert drive_rates.choose(conn, [_drive("S1")], "200m", direct=True)["max_bitrate"] == "200m"
    for n in range(2):
        _kept(conn, f"ses_old{n}", "S1", 50 * MB, 75 * MB)
    got = drive_rates.choose(conn, [_drive("S1")], None, direct=True)
    assert got["max_bitrate"] == "360m" and got["basis"] == "learned"


def test_a_direct_wave_teaches_nothing_and_is_nobodys_previous(conn):
    """המקור (דיסק חי + דחיסה) יכול להיות האיטי — כוננים שכתבו לאט בגל
    כזה אינם "כוננים איטיים", וגל כזה אינו נקודת מוצא לגל מספרייה."""
    drive_rates.decide(conn, "ses_d", lambda: [_drive("S1")], None, direct=True)
    member = {"mac": "aa:bb:cc:00:00:21", "targets_json": json.dumps([{
        "dev": "sda", "state": "done", "serial": "S1", "bytes_written": 15_000 * MB,
        "first_moved_at": "2026-10-11T10:00:00+00:00",
        "moved_at": "2026-10-11T10:16:40+00:00"}])}
    assert drive_rates.record_wave(conn, "ses_d", [member]) == 0
    drive_rates.record_stream(conn, "ses_d", {"sent_bytes": SENT, "loss_blocks": 0,
                                              "stream_bps": 10 ** 9, "stream_s": 600})
    got = _next(conn, serials=("S1",))
    assert got["basis"] == "start" and got["previous"] is None


def test_the_build_machine_gets_the_decided_rate_or_the_start(conn):
    assert drive_rates.direct_bitrate(conn, None, None) == "600m"
    assert drive_rates.direct_bitrate(conn, "ses_d", "200m") == "200m"
    assert drive_rates.direct_bitrate(conn, "ses_d", "fast") == "fast"
    for n in range(2):
        _kept(conn, f"ses_old{n}", "S1", 50 * MB, 75 * MB)
    drive_rates.decide(conn, "ses_d", lambda: [_drive("S1")], None, direct=True)
    assert drive_rates.direct_bitrate(conn, "ses_d", None) == "360m"


# --- למטה, רצפה וגג ------------------------------------------------------------


def test_retransmissions_lower_the_next_wave_by_a_quarter(conn):
    _wave(conn, "ses_1", loss=MANY)
    got = _next(conn)
    assert got["max_bitrate"] == "450m"
    assert got["basis"] == "down"
    assert got["previous"]["loss_blocks"] == MANY


def test_retransmissions_never_take_the_wave_below_a_quarter_of_the_start(conn):
    """בקרה שלילית — ירידה מתחת לרצפה: גל רע אחרי גל רע. 600→450→337→252→189
    →141 — והרצפה היא 150m (רבע מ-600m)."""
    seen = [_wave(conn, f"ses_{n}", loss=MANY) for n in range(8)]
    assert seen[:5] == ["600m", "450m", "337m", "252m", "189m"]
    assert seen[5:] == ["150m", "150m", "150m"]
    got = _next(conn)
    assert got["max_bitrate"] == "150m" and got["basis"] == "down"


def test_the_floor_follows_a_lower_configured_cap(conn):
    seen = [_wave(conn, f"ses_{n}", ceiling="200m", loss=MANY) for n in range(6)]
    assert seen == ["200m", "150m", "112m", "84m", "63m", "50m"]   # רבע מ-200m


def test_a_clean_wave_never_goes_above_the_configured_cap(conn):
    """בקרה שלילית — עלייה מעל הגג: ‏`IMAGECTL_MAX_BITRATE` הוא מה שהשרת מכבד."""
    seen = [_wave(conn, f"ses_{n}", ceiling="200m") for n in range(3)]
    assert seen == ["200m", "200m", "200m"]
    got = _next(conn, ceiling="200m")
    assert got["max_bitrate"] == "200m" and got["basis"] == "ceiling"


def test_a_lowered_cap_wins_over_what_worked_before(conn):
    for n in range(3):
        _wave(conn, f"ses_{n}")                   # 600 → 700 → 800
    got = _next(conn, ceiling="300m")
    assert got["max_bitrate"] == "300m" and got["basis"] == "ceiling"


def test_a_known_slow_drive_still_lowers_the_room(conn):
    """#447 נשאר: כונן שהגביל בשני גלים שונים מוריד את הגל אליו × 0.9.
    הגל הקודם (600m) היה נקי בזרם, אבל שני הכוננים כתבו בו חצי מהקצב —
    ול-S2 יש גל מגביל נוסף מקודם (50MB/s). S1 הגביל פעם אחת בלבד."""
    _kept(conn, "ses_old", "S2", 50 * MB, 75 * MB)
    _wave(conn, "ses_1", kept_up=False)
    got = _next(conn)
    assert got["max_bitrate"] == "360m"           # 50MB/s × 8 × 0.9
    assert got["basis"] == "learned"
    assert got["by"]["serial"] == "S2"


# --- זיכרון בין סבבים -----------------------------------------------------------


def test_the_last_wave_with_the_same_drives_is_the_starting_point(conn):
    _wave(conn, "ses_room", serials=("S1", "S2"))          # 600 נקי
    _wave(conn, "ses_room2", serials=("S1", "S2"))         # 700 נקי
    _wave(conn, "ses_other", serials=("X1",), loss=MANY)   # חדר אחר, רע
    got = _next(conn, serials=("S1", "S2"))
    assert got["max_bitrate"] == "800m"
    assert got["previous"]["session_id"] == "ses_room2"


def test_the_next_wave_of_a_round_with_fewer_drives_continues(conn):
    _wave(conn, "ses_1", serials=("S1", "S2", "S3"))
    got = _next(conn, serials=("S3",))
    assert got["max_bitrate"] == "700m"
    assert got["previous"]["session_id"] == "ses_1"


def test_a_new_drive_in_the_room_starts_over(conn):
    """כונן שלא היה בגל הקודם — אין לו ראיה שהוא עומד ב-700m."""
    _wave(conn, "ses_1", serials=("S1", "S2"))
    got = _next(conn, serials=("S1", "S2", "NEW"))
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "start"


def test_a_wave_is_not_its_own_previous(conn):
    """החלטה חוזרת לאותו גל נשענת על הגל שלפניו — לא על עצמה (אחרת גל
    נקי שנבחר מחדש היה מטפס מעצמו)."""
    _wave(conn, "ses_1")
    assert _wave(conn, "ses_2") == "700m"            # ‏ses_2 נקי ונמדד
    again = drive_rates.decide(conn, "ses_2", lambda: [_drive("S1"), _drive("S2")], None)
    assert again == "700m"


# --- המתג, ההחלטה והרישום -------------------------------------------------------


def test_with_the_switch_off_the_start_rate_is_fixed(conn):
    _wave(conn, "ses_1")
    db.set_setting(conn, drive_rates.SETTING, "false")
    got = _next(conn)
    assert got["max_bitrate"] == "600m"
    assert got["basis"] == "disabled"


def test_the_journal_says_why(conn):
    _wave(conn, "ses_1")
    drive_rates.decide(conn, "ses_2", lambda: [_drive("S1"), _drive("S2")], None)
    detail = conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                          " ORDER BY id DESC").fetchone()[0]
    assert "700m" in detail and "basis=up" in detail and "ses_1" in detail


def test_a_broken_decision_falls_back_to_the_start_rate(conn):
    def broken():
        raise RuntimeError("boom")
    assert drive_rates.decide(conn, "ses_1", broken, None) == "600m"
    assert drive_rates.decide(conn, "ses_2", broken, "200m") == "200m"


def test_recording_the_stream_of_an_unknown_wave_is_harmless(conn):
    drive_rates.record_stream(conn, "ses_none", {"sent_bytes": SENT, "loss_blocks": 0})
    assert conn.execute("SELECT COUNT(*) FROM wave_rates").fetchone()[0] == 0


def test_a_broken_stream_record_does_not_raise(conn):
    drive_rates.decide(conn, "ses_1", lambda: [_drive("S1")], None)
    drive_rates.record_stream(conn, "ses_1", object())   # לא dict
    events = [r[0] for r in conn.execute("SELECT event FROM journal")]
    assert "drive_rates_failed" in events
