"""#1447: the server's timezone as a POSIX TZ string for the agent's GUI.

The initramfs has no zoneinfo and no /etc/localtime, so glibc in native-gui
showed UTC (metal, 16/10: the cloner's clock was 3 h behind). The zone name is
the ``timezone`` setting, else Asia/Jerusalem; the string is the footer of its
TZif file -- tzdata's own rule, not a table in this code.

``None`` means unknown (rule 5: never a guess); the GUI then shows its clock
as UTC with a ``UTC`` label. The file is read once per path per process.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

from .db import get_setting

DEFAULT_ZONE = "Asia/Jerusalem"
ZONEINFO = Path("/usr/share/zoneinfo")

_ZONE_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
_POSIX_RE = re.compile(r"^[A-Za-z0-9<>+\-,./:]{1,64}$")
log = logging.getLogger(__name__)


def zone_name(conn: sqlite3.Connection) -> str:
    value = get_setting(conn, "timezone")
    return value.strip() if value and value.strip() else DEFAULT_ZONE


def _unknown(zone: str, reason: str) -> None:
    log.warning("cannot derive POSIX timezone for %r: %s", zone, reason)


def posix_tz(conn: sqlite3.Connection) -> str | None:
    zone = zone_name(conn)
    if not _ZONE_RE.fullmatch(zone):
        _unknown(zone, "invalid zone name")
        return None
    return _footer(str(ZONEINFO / zone), zone)


@lru_cache(maxsize=16)
def _footer(path: str, zone: str) -> str | None:
    """Every hello asks; the file and the answer (also None) are per process."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        _unknown(zone, str(exc))
        return None
    if not data.startswith(b"TZif"):
        _unknown(zone, "not a TZif file")
        return None
    if len(data) < 5 or data[4:5] not in (b"2", b"3", b"4"):
        _unknown(zone, "unsupported TZif version")
        return None
    if not data.endswith(b"\n"):
        _unknown(zone, "missing TZif footer terminator")
        return None
    start = data.rfind(b"\n", 0, -1)
    footer = data[start + 1:-1] if start >= 0 else b""
    try:
        value = footer.decode("ascii")
    except UnicodeDecodeError:
        _unknown(zone, "non-ASCII TZif footer")
        return None
    if not _POSIX_RE.fullmatch(value):
        _unknown(zone, "invalid TZif footer")
        return None
    return value
