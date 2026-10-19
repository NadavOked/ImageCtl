"""#1642 -- ``update_status`` left at ``running`` after the server already runs that
tag deferred the scheduled reboot forever ("עדכון שרת רץ" every 10 minutes).

Measured on Server-TA 15/10: ``{state: running, tag: v0.73.2}`` from 13/10, server
already on v0.73.2; the console showed "done" (``status_with_verification``) while
``busy_reason`` said "running". Two readers, two answers. Now one answer, plus a
ceiling: a ``running`` with no progress stamp for an hour is not a running update.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import auto_reboot, update
from server.db import get_setting, set_setting

RUNNING = "עדכון שרת רץ"


def stamp(hours_ago: float = 0.0, minutes_ago: float = 0.0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago, minutes=minutes_ago)
    return moment.isoformat(timespec="seconds")


def hooks_for(version):
    def describe(_repo):
        if version is None:
            raise RuntimeError("git unavailable")
        return version
    return {"describe": describe, "head": lambda _repo: None}      # head None: no cache


def rt_for(server, tmp_path, *, version="v0.73.2"):
    return SimpleNamespace(conn=server["ctx"].conn, data_dir=tmp_path, vmupdate=None,
                           repo_dir=tmp_path, update_hooks=hooks_for(version))


def set_status(server, **doc):
    set_setting(server["ctx"].conn, update.STATUS_KEY, json.dumps({"state": "running", **doc}))


def test_running_for_the_tag_the_server_already_runs_is_not_busy(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(minutes_ago=5))
    assert auto_reboot.busy_reason(rt_for(server, tmp_path)) is None


def test_the_console_and_the_scheduler_give_the_same_answer(server, tmp_path):
    """The console says "done" for exactly the statuses the scheduler no longer defers on."""
    for version, tag in (("v0.73.2", "v0.73.2"), ("v0.73.1", "v0.73.2"), (None, "v0.73.2")):
        set_status(server, tag=tag, started_at=stamp(minutes_ago=5))
        rt = rt_for(server, tmp_path, version=version)
        shown = update.status_with_verification(server["ctx"].conn, rt.update_hooks, tmp_path)
        assert (shown["state"] == "done") == (auto_reboot.busy_reason(rt) is None), version


@pytest.mark.parametrize("version", ["v0.73.1", None], ids=["other-version", "version-unreadable"])
def test_a_fresh_running_status_for_another_or_unknown_version_still_defers(server, tmp_path, version):
    set_status(server, tag="v0.73.2", started_at=stamp(minutes_ago=5))
    assert auto_reboot.busy_reason(rt_for(server, tmp_path, version=version)) == RUNNING


def test_running_without_a_tag_and_with_fresh_progress_defers(server, tmp_path):
    set_status(server, started_at=stamp(minutes_ago=1))
    assert auto_reboot.busy_reason(rt_for(server, tmp_path)) == RUNNING


def test_running_for_another_version_with_no_progress_for_hours_is_not_busy(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(hours_ago=50),
               stages={"fetch": stamp(hours_ago=50), "install": stamp(hours_ago=49.9)})
    assert auto_reboot.busy_reason(rt_for(server, tmp_path, version="v0.73.1")) is None


def test_a_recent_stage_stamp_is_progress_even_when_the_run_started_long_ago(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(hours_ago=50),
               stages={"fetch": stamp(hours_ago=50), "restart": stamp(minutes_ago=2)})
    assert auto_reboot.busy_reason(rt_for(server, tmp_path, version="v0.73.1")) == RUNNING


@pytest.mark.parametrize("doc", [
    {"tag": "v0.73.2"},                                              # no stamp at all
    {"tag": "v0.73.2", "started_at": "yesterday"},                   # not a time
    {"tag": "v0.73.2", "started_at": "2026-10-13T03:31:00"},         # no timezone: ambiguous
    {"tag": "v0.73.2", "started_at": "2020-01-01T00:00:00+00:00",
     "stages": {"fetch": "garbage"}},                                # one bad stamp spoils it
], ids=["no-stamp", "not-a-time", "naive-time", "one-bad-stage"])
def test_a_status_whose_age_cannot_be_read_is_unknown_and_defers(server, tmp_path, doc):
    set_status(server, **doc)
    assert auto_reboot.busy_reason(rt_for(server, tmp_path, version="v0.73.1")) == RUNNING


def test_busy_reason_never_writes_the_status_or_the_journal(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(hours_ago=50))
    conn = server["ctx"].conn
    before = (get_setting(conn, update.STATUS_KEY), conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0])
    assert auto_reboot.busy_reason(rt_for(server, tmp_path, version="v0.73.1")) is None
    assert before == (get_setting(conn, update.STATUS_KEY),
                      conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0])


def scheduled(server, tmp_path, version, calls):
    set_setting(server["ctx"].conn, "auto_reboot", "daily")
    set_setting(server["ctx"].conn, "auto_reboot_time", "03:00")
    return auto_reboot.Scheduler(rt_for(server, tmp_path, version=version),
                                 lambda: calls.append(1), lambda: datetime(2026, 10, 15, 3))


def journal_lines(server):
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'auto_reboot' ORDER BY id")]


def test_the_scheduled_reboot_runs_and_journals_the_stale_status_it_ignored(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(hours_ago=50))
    calls = []
    assert scheduled(server, tmp_path, "v0.73.1", calls).tick() == "done"
    assert calls == [1]
    lines = journal_lines(server)
    assert any(line.startswith("התעלמות — סטטוס עדכון v0.73.2 'רץ' בלי התקדמות") for line in lines)
    assert lines[-1].startswith("בוצע")


def test_the_scheduled_reboot_journals_a_running_status_for_the_current_tag(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(minutes_ago=5))
    calls = []
    assert scheduled(server, tmp_path, "v0.73.2", calls).tick() == "done"
    assert any(line.startswith("התעלמות — סטטוס עדכון v0.73.2 נשאר 'רץ'") for line in journal_lines(server))


def test_a_live_update_still_defers_the_scheduled_reboot_with_the_true_reason(server, tmp_path):
    set_status(server, tag="v0.73.2", started_at=stamp(minutes_ago=5))
    calls = []
    assert scheduled(server, tmp_path, "v0.73.1", calls).tick() == "deferred"
    assert calls == []
    assert journal_lines(server) == [f"נדחה — {RUNNING}"]
