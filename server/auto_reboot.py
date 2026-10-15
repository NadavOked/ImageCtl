"""Scheduled, busy-aware server reboot (#1406)."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from . import room, update
from .db import get_setting, journal

MODE = "auto_reboot"
TIME = "auto_reboot_time"
WEEKDAY = "auto_reboot_weekday"
MODES = {"off", "daily", "weekly"}
DEFAULT_TIME = "03:00"
MARKER = "scheduled-reboot"
CHECK_SECONDS = 60.0
RETRY_MINUTES = 10
GIVE_UP_MINUTES = 120


def parse_time(value) -> tuple[int, int] | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    try:
        hour, minute = (int(part) for part in value.split(":"))
    except ValueError:
        return None
    if f"{hour:02d}:{minute:02d}" != value or hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_weekday(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        day = int(value)
    except (TypeError, ValueError):
        return None
    return day if str(day) == str(value) and 0 <= day <= 6 else None


def settings(conn) -> tuple[str, str, int]:
    mode = get_setting(conn, MODE) or "off"
    at = get_setting(conn, TIME) or DEFAULT_TIME
    raw_day = get_setting(conn, WEEKDAY)
    day = parse_weekday(raw_day if raw_day not in (None, "") else "0")
    return (mode if mode in MODES else "off", at if parse_time(at) else DEFAULT_TIME,
            day if day is not None else 0)


def next_occurrence(mode: str, at: str, weekday: int, now: datetime) -> datetime | None:
    """Next wall-clock occurrence; naive datetimes deliberately use server local time."""
    if mode == "off":
        return None
    parsed = parse_time(at)
    if parsed is None or mode not in MODES or not 0 <= weekday <= 6:
        return None
    hour, minute = parsed
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if mode == "daily":
        return candidate if candidate > now else candidate + timedelta(days=1)
    candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
    return candidate if candidate > now else candidate + timedelta(days=7)


def busy_reason(rt) -> str | None:
    conn = rt.conn
    if conn.execute("SELECT 1 FROM sessions WHERE state IN ('open','running') LIMIT 1").fetchone():
        return "יש סבב הפצה פתוח או רץ"
    if conn.execute("SELECT 1 FROM room_rounds WHERE state IN ('active','closing') LIMIT 1").fetchone():
        return "יש סבב חדר פתוח או רץ"
    if conn.execute("SELECT 1 FROM tasks WHERE type = 'capture' AND state = 'running' LIMIT 1").fetchone():
        return "קליטת אימג' רצה"
    if (update.get_status(conn) or {}).get("state") == "running":
        return "עדכון שרת רץ"
    vm = getattr(rt, "vmupdate", None)
    if vm is not None:
        state = vm.status(None).get("state")
        if state not in ("idle", "saved", "error"):
            return f"עדכון VM במצב {state}"
    rows = conn.execute("SELECT local_clone, last_seen FROM net_devices WHERE local_clone IS NOT NULL")
    if any(room.local_copy_now(row["local_clone"], row["last_seen"]) for row in rows):
        return "מחשב מדווח על שיכפול מקומי"
    return None


def default_reboot() -> None:
    subprocess.run(["systemctl", "reboot"], check=True)


def consume_marker(conn, data_dir: str | Path) -> bool:
    marker = Path(data_dir) / MARKER
    if not marker.exists():
        return False
    marker.unlink()
    journal(conn, "auto_reboot", "השרת אותחל לפי לוח הזמנים")
    return True


@dataclass
class Scheduler:
    rt: object
    reboot: object = default_reboot
    now: object = datetime.now
    due: datetime | None = None
    first_deferred: datetime | None = None
    retry_at: datetime | None = None

    def tick(self) -> str | None:
        current = self.now().replace(second=0, microsecond=0)
        mode, at, weekday = settings(self.rt.conn)
        if mode == "off":
            self.due = self.first_deferred = self.retry_at = None
            return None
        if self.due is None:
            # Include the current scheduled minute on startup/tick.
            self.due = next_occurrence(mode, at, weekday, current - timedelta(minutes=1))
        if current < self.due or (self.retry_at is not None and current < self.retry_at):
            return None
        try:
            reason = busy_reason(self.rt)
        except Exception as exc:
            journal(self.rt.conn, "auto_reboot", f"תקלה: {type(exc).__name__}: {exc}")
            reason = "לא ניתן לבדוק אם השרת עסוק"
        if reason:
            self.first_deferred = self.first_deferred or current
            if current - self.first_deferred >= timedelta(minutes=GIVE_UP_MINUTES):
                journal(self.rt.conn, "auto_reboot", f"דולג — השרת היה עסוק במשך שעתיים: {reason}")
                self.due = next_occurrence(mode, at, weekday, current)
                self.first_deferred = self.retry_at = None
                return "skipped"
            journal(self.rt.conn, "auto_reboot", f"נדחה — {reason}")
            self.retry_at = current + timedelta(minutes=RETRY_MINUTES)
            return "deferred"
        self.reboot()
        (Path(self.rt.data_dir) / MARKER).write_text(current.isoformat(), encoding="ascii")
        journal(self.rt.conn, "auto_reboot", "בוצע — הפעלה מחדש לפי לוח הזמנים")
        self.due = next_occurrence(mode, at, weekday, current)
        self.first_deferred = self.retry_at = None
        return "done"


async def run(rt, reboot=default_reboot, interval: float = CHECK_SECONDS) -> None:
    scheduler = Scheduler(rt, reboot)
    while True:
        try:
            scheduler.tick()
        except Exception as exc:  # one failed check must not kill the scheduler
            journal(rt.conn, "auto_reboot", f"תקלה: {type(exc).__name__}: {exc}")
        await asyncio.sleep(interval)


def health_detail(ctx, now: datetime | None = None) -> str:
    task = getattr(ctx, "auto_reboot_task", None)
    if task is not None and task.done() and not task.cancelled():
        return "המתזמן אינו רץ"
    mode, at, weekday = settings(ctx.conn)
    nxt = next_occurrence(mode, at, weekday, now or datetime.now())
    return "כבוי" if nxt is None else nxt.strftime("%d/%m/%Y %H:%M")
