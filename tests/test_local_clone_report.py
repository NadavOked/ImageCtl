"""#1322 D — a cloning machine reports a local clone to the server journal.

The copy never passes through the server; when the server answers, the
agent's report becomes ONE journal line, labelled as the machine's own, after
the same identity gate as every other agent route (#855/#997). A report from
another address writes nothing; a malformed one is refused by name.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from server import local_clone_report
from test_machine_identity import MAC1, identity_server, journal_details  # noqa: F401


def _report(mac: str = MAC1, **over) -> dict:
    body = {"mac": mac, "source": {"dev": "sdc", "port": 3, "serial": "S3"},
            "targets": [{"dev": "sda", "port": 1, "serial": "S1", "state": "done", "error": ""},
                        {"dev": "sdb", "port": 2, "serial": "S2", "state": "failed",
                         "error": "מחיצה 3: שגיאת כתיבה אחרי 91.6 GB"}],
            "shrunk": False, "source_restore": "", "stopped": False,
            "bytes": 148600000000, "elapsed_s": 1065}
    body.update(over)
    return body


def test_the_report_is_one_labelled_journal_line(identity_server):
    setup_classroom(identity_server)
    ok = identity_server["station"].post("/api/v1/agent/local-clone", json=_report())
    assert ok.status_code == 200 and ok.json() == {"ok": True}, ok.text
    lines = [d for _u, d in journal_details(identity_server, "local_clone")]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith(MAC1), line
    assert "מקור דיסק 3" in line and "דיסק 1 הצליח" in line and "דיסק 2 נכשל (מחיצה 3" in line
    assert "148.6 GB" in line and "17:45" in line
    assert "דיווח המחשב" in line and "לא עבר דרך השרת" in line


def test_a_report_with_the_new_fields_is_journaled(identity_server):
    setup_classroom(identity_server)
    ok = identity_server["station"].post(
        "/api/v1/agent/local-clone",
        json=_report(shrink_path="snapshot", snapshot_teardown="ok", info_s=1, copy_s=2),
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}, ok.text
    lines = [d for _u, d in journal_details(identity_server, "local_clone")]
    assert len(lines) == 1, lines
    assert "שלבים" in lines[0]


def test_a_report_from_another_address_is_refused_and_not_journaled(identity_server):
    setup_classroom(identity_server)
    r = identity_server["stranger"].post("/api/v1/agent/local-clone", json=_report())
    assert r.status_code == 403 and r.json()["code"] == "identity_refused", r.text
    assert journal_details(identity_server, "local_clone") == []


@pytest.mark.parametrize("bad", [
    {"targets": []},
    {"targets": [{"port": 1, "state": "half"}]},
    {"source": "sdc"},
    {"mac": "zz"},
])
def test_a_malformed_report_is_refused_by_name(identity_server, bad):
    setup_classroom(identity_server)
    r = identity_server["station"].post("/api/v1/agent/local-clone", json=_report(**bad))
    assert r.status_code == 400, r.text
    assert r.json()["code"] in ("bad_report", "bad_mac")
    assert journal_details(identity_server, "local_clone") == []


def test_a_source_left_shrunk_is_said_in_the_line():
    """The worst thing a local clone can leave: the source not grown back."""
    line = local_clone_report.journal_line(_report(shrunk=True, source_restore="מחיצה 3 נשארה קטנה"))
    assert "ולא הוחזר לגודלו" in line
    line = local_clone_report.journal_line(_report(shrunk=True, source_restore="ok"))
    assert "והוחזר לגודלו" in line and "ולא" not in line


def test_an_old_agents_report_gives_todays_line():
    line = local_clone_report.journal_line(_report())
    assert "עותק בזיכרון" not in line
    assert "שלבים" not in line
    assert "כיווץ במקום" not in line and "סורב" not in line


def test_the_snapshot_path_says_the_source_was_only_read():
    line = local_clone_report.journal_line(
        _report(shrink_path="snapshot", snapshot_teardown="ok", shrunk=False)
    )
    assert "המקור נקרא בלבד ולא נכתב" in line
    assert "לא שוחרר" not in line
    assert "כווץ זמנית" not in line


def test_a_view_that_was_not_released_is_said_in_the_line():
    line = local_clone_report.journal_line(_report(
        shrink_path="snapshot",
        snapshot_teardown="dmsetup remove lc-src-3 failed: Device or resource busy",
    ))
    assert "לא שוחרר" in line
    assert "Device or resource busy" in line
    assert "לאתחל" in line


def test_a_refused_shrink_is_named():
    line = local_clone_report.journal_line(_report(shrink_path="refused"))
    assert "סורב" in line and "המקור לא נכתב" in line
    assert "כיווץ במקום" not in line


def test_the_stage_times_are_in_the_line():
    line = local_clone_report.journal_line(_report(
        info_s=12, resize_s=95, relocations_mb=3400, copy_s=840,
        verify_s=3, restore_s=1,
    ))
    assert "שלבים: מדידה 12ש', כיווץ 95ש', הועברו 3400MB, העתקה 840ש', אימות 3ש', סיום 1ש'" in line


def test_a_stage_that_was_not_measured_is_not_a_zero():
    line = local_clone_report.journal_line(_report(
        info_s=None, resize_s="95", copy_s=-1, verify_s=True,
        relocations_mb=1.5, restore_s=0,
    ))
    assert " · שלבים: " in line, line
    stages = line.split(" · שלבים: ", 1)[1].split(" (דיווח המחשב", 1)[0]
    assert stages == "סיום 0ש'"
    assert "מדידה" not in line


def test_a_long_error_is_cut_in_the_journal():
    line = local_clone_report.journal_line(_report(targets=[{"port": 1, "state": "failed", "error": "x" * 5000}]))
    assert len(line) < 400
