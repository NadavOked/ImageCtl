"""תשתית משותפת לבדיקות השרת: ספרייה מזויפת, שעון נשלט, ולקוחות
מחוברים בשני התפקידים — ושתי בדיקות על הריצה עצמה (#52, ‏#79).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import hostguard
import hygiene
import native

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


# --- כנות הריצה: כלי שחסר, ותהליך ששרד (#52, ‏#79) --------------------------
#
# ‏"N עברו, אפס דילוגים" הוא המשפט שכל האימות של הפרויקט נשען עליו,
# ולכן המכשיר עצמו צריך בדיקה: חבילה שדילגה במקום שהכלים אמורים להיות
# אינה ירוקה, וטסט שהשאיר תהליך שידור חי לא סיים.

_skips = native.SkipAudit()
_run: dict[str, str | None] = {"basetemp": None}


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", f"{native.MISSING_MARK}(tools): כלי מקומי חסר במקום שנדרש"
    )
    hygiene.block_real_processes()
    # ‏portbase גבוה ואקראי לכל הריצה: יתום של ריצה שנקטעה לא ינקה איש,
    # ולכן ההגנה היא שלא יהיה לו במה להתנגש (#156).
    hygiene.assign_test_portbase()
    hostguard.block_real_host_reads()


def pytest_runtest_setup(item) -> None:
    native.fail_on_missing_native(item)


def pytest_runtest_logreport(report) -> None:
    _skips.record(report)


def _say(lines: list[str]) -> None:
    text = "\n" + "\n".join(lines)
    try:
        print(text)
    except UnicodeEncodeError:           # pragma: no cover — קונסולת cp1252
        print(text.encode("ascii", "backslashreplace").decode())


def pytest_sessionfinish(session, exitstatus) -> None:
    # דילוג מוצהר אינו כישלון — אבל גם אינו שקט: מספר וסיבה, בכל ריצה (#295).
    notes = _skips.notes()
    if notes:
        _say(notes)
    problems = _skips.verdict()
    problems += hygiene.session_verdict(_run["basetemp"], native.native_required())
    if hygiene.blocked_spawns:
        problems.append(f"{len(hygiene.blocked_spawns)} ניסיונות להפעיל שולח אמיתי:")
        problems += [f"    {' '.join(cmd)}" for cmd in hygiene.blocked_spawns]
    if not problems:
        return
    _say(problems)
    session.exitstatus = 1


@pytest.fixture(scope="session", autouse=True)
def _run_basetemp(tmp_path_factory) -> None:
    """תיקיית ה-tmp של הריצה הזאת. רק תהליכים שמזכירים אותה ייסרקו
    וייהרגו — ריצה מקבילה של מישהו אחר על אותו שרת אינה שלנו."""
    _run["basetemp"] = str(tmp_path_factory.getbasetemp())


@pytest.fixture(autouse=True)
def _no_real_sender():
    """טסט שהגיע לשולח האמיתי נכשל בשמו, ולא בשקט אחרי הריצה."""
    before = len(hygiene.blocked_spawns)
    yield
    escaped = hygiene.blocked_spawns[before:]
    if escaped:
        pytest.fail(
            "הטסט הגיע ל-udp-sender האמיתי במקום לשולח מזויף (#79): "
            + " | ".join(" ".join(cmd) for cmd in escaped),
            pytrace=False,
        )

WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"

MANIFEST_256 = {
    "schema": 1,
    "id": "img_7f3a91",
    "name": "Office 2024 Standard",
    "description": "האימג' הנפוץ ביותר",
    "folder": "Office",
    "created": "2026-08-22T09:14:00+03:00",
    "created_by": "nadav",
    "family": 256,
    "source_disk_bytes": 256060514304,
    "min_target_bytes": 256060514304,
    "scheme": "gpt",
    "sector_size": 512,
    "partitions": [
        {
            "index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
            "start_sector": 2048, "size_bytes": 104857600, "used_bytes": 31457280,
            "file": "p1.esp.pcl.zst", "sha256": "aa" * 32, "expandable": False,
        },
        {
            "index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
            "start_sector": 1085440, "size_bytes": 254803968000,
            "used_bytes": 84509376512,
            "file": "p3.win.pcl.zst", "sha256": "bb" * 32, "expandable": True,
        },
    ],
    "total_compressed_bytes": 57982058496,
    "partclone_version": "0.3.x",
    "compression": "zstd-9",
    "field_from_the_future": "ignored",   # שדה לא ידוע — מתעלמים, לא נכשלים
}

MANIFEST_500 = {
    **MANIFEST_256,
    "id": "img_2c8e04",
    "name": "CAD Heavy",
    "family": 500,
    "min_target_bytes": 500107862016,
}


class Clock:
    """שעון נשלט — הבדיקות מזיזות אותו במקום לישון."""

    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


LINUX_GUID = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
SWAP_GUID = "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F"
RECOVERY_GUID = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"

#: אימג' Linux (אפיון סעיף 14): ESP, שורש ext4, ו-swap שמתועד אבל לא נשמר.
MANIFEST_LINUX = {
    **MANIFEST_256,
    "id": "img_lnx001",
    "name": "Ubuntu 24.04 Linux Course",
    "folder": "Linux",
    "os": "linux",
    "partitions": [
        MANIFEST_256["partitions"][0],
        {
            "index": 2, "type_guid": SWAP_GUID, "role": "swap", "fs": "swap",
            "start_sector": 206848, "size_bytes": 8589934592, "used_bytes": 0,
            "file": None, "sha256": None, "expandable": False,
        },
        {
            "index": 3, "type_guid": LINUX_GUID, "role": "linux", "fs": "ext4",
            "start_sector": 16984064, "size_bytes": 247364194304,
            "used_bytes": 21474836480,
            "file": "p3.linux.pcl.zst", "sha256": "cc" * 32, "expandable": True,
        },
    ],
}
PARTITION_BYTES = b"compressed-partition-bytes"


def write_image(root: Path, manifest: dict) -> None:
    """כותב אימג' מזויף לדיסק — עם sha256 אמיתי.

    הערכים בקבועים למעלה הם ממלאי מקום לצורת המבנה; מה שנכתב לדיסק
    חייב להיות עקבי עם עצמו, אחרת בדיקות שמאמתות אימג' (ייבוא, שחזור)
    נכשלות על נתוני בדיקה ולא על באג.
    """
    folder = root / manifest["id"]
    folder.mkdir(parents=True)
    digest = hashlib.sha256(PARTITION_BYTES).hexdigest()
    on_disk = copy.deepcopy(manifest)
    for part in on_disk["partitions"]:
        if part.get("file"):
            part["sha256"] = digest
    (folder / "manifest.json").write_text(
        json.dumps(on_disk, ensure_ascii=False), encoding="utf-8"
    )
    for part in on_disk["partitions"]:
        if part.get("file"):
            (folder / part["file"]).write_bytes(PARTITION_BYTES)


@pytest.fixture()
def images_root(tmp_path: Path) -> Path:
    root = tmp_path / "images"
    write_image(root, MANIFEST_256)
    write_image(root, MANIFEST_500)
    return root


@pytest.fixture()
def clock() -> Clock:
    return Clock()


def _build_server(tmp_path: Path, images_root: Path, clock: Clock, recorder) -> dict:
    """אפליקציה מלאה עם שולח מזויף, ולקוחות מחוברים בשני התפקידים."""
    from server import users
    from server.app import create_app

    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=recorder,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")

    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post(
        "/api/console/login", json={"username": "noc", "password": "admin-pass-123"}
    ).status_code == 200
    assert deploy.post(
        "/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}
    ).status_code == 200
    return {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
            "anon": TestClient(app), "clock": clock, "recorder": recorder}


@pytest.fixture()
def server(tmp_path: Path, images_root: Path, clock: Clock):
    """האפליקציה המלאה + לקוחות מחוברים כ-admin וכ-deploy.

    השולח מזויף **גם כאן**, ולא רק ב-`server_with_sender`: סבב מבשיל
    בכמה מסלולים — hello, מבט-על, תחנה — וכל אחד מהם הפעיל `udp-sender`
    אמיתי, על פורט השידור של השרת, שנשאר לרוץ אחרי סוף הריצה (#79).
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415

    bundle = _build_server(tmp_path, images_root, clock, Recorder(block=True))
    yield bundle
    bundle["ctx"].sender.stop()


@pytest.fixture()
def server_with_sender(server: dict):
    """כמו `server`, ועם ה-Recorder בידיים — לבדיקות של השידור עצמו."""
    return server, server["recorder"]


def setup_classroom(server: dict, expected: int = 2) -> dict:
    """קבוצת כיתה עם שתי מכונות רשומות; מחזיר מזהים שימושיים."""
    admin = server["admin"]
    assert admin.post(
        "/api/console/groups",
        json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"},
    ).status_code == 200
    result = admin.post(
        "/api/console/machines/import",
        json={
            "group_id": "grp_LAB1",
            "text": "b4:2e:99:07:1a:c4 05\nB4-2E-99-07-1A-C5, 6\n",
        },
    ).json()
    assert result["saved"] == 2 and not result["rejected"]
    return {
        "group": "grp_LAB1",
        "mac1": "b4:2e:99:07:1a:c4",
        "mac2": "b4:2e:99:07:1a:c5",
        "expected": expected,
    }


def hello_body(mac: str, disk_bytes: int = 256060514304) -> dict:
    """גוף hello מינימלי-תקני (ממשק 2)."""
    return {
        "schema": 1,
        "mac": mac,
        "all_macs": [mac],
        "ip": "10.44.12.187",
        "hostname_current": None,
        "uuid": "4C4C4544-0037",
        "firmware": "uefi",
        "secure_boot": True,
        "agent_version": "0.1.0",
        "memory_bytes": 8589934592,
        "disks": [
            {
                "dev": "sda", "size_bytes": disk_bytes, "model": "Test SSD",
                "serial": "S1", "removable": False, "scheme": "gpt", "has_data": True,
            }
        ],
    }
