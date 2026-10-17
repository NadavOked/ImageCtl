"""‏#1535 — התראות CodeQL בשרת מהשער הציבורי של v0.75.0.

1. **הרשאות** (`py/overly-permissive-file`): אזור העבודה של עדכון ה-VM היה
   ‏0o755/0o644 — כל משתמש מקומי עבר בו וקרא את קובצי האתחול שהורדו מה-ESP.
   מי שצריך אותם בפועל הוא משתמש ה-VM בלבד, ו-`as_user`/`agent_shell.call`
   מריצים אותו עם `--regid` שלו ו-`--clear-groups` — ולכן הגישה עוברת דרך
   **הקבוצה שלו**: ‏root בעלים, הקבוצה = משתמש ה-VM, "אחרים" — אפס.
   תיקייה — מעבר בלבד (0o710: ‏`boot_ca_probe` בודק `[ -f ]` על נתיבים
   ידועים ואינו מונה); קובץ — קריאה (0o640). ‏QEMU פותח את הדיסק כ-root
   לפני `-run-with`, והשמירה רצה כשהוא כבוי.
2. **ניקוי תגיות** (`js/incomplete-multi-character-sanitization`):
   ‏vmupdate.js ו-nicsvc.js — ‏`stripTags` של console.js (‏#1523), לא ה-regex.
3. **log-injection**: שבירת שורה מהבקשה אינה מגיעה ליומן.
   (‏`stack-trace-exposure` — ב-`test_vmupdate_api.py`.)
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

import pytest

from native import requires_native
from server import agent_shell, gui_failure, vmdisk

STATIC = Path(__file__).resolve().parents[1] / "server" / "static"
posix_only = requires_native(posix=True, why="chown/קבוצות של POSIX")


def _mode(path: Path) -> int:
    return os.lstat(path).st_mode & 0o777


# --- 1. הרשאות -----------------------------------------------------------------


class FakeBox:
    def fetch(self, _index, paths, dest):
        for path in paths:
            target = dest / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MZ")


@posix_only
def test_boot_files_are_open_to_the_vm_group_only(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    gid = os.getgid()
    paths = ["/EFI/BOOT/bootx64.efi", "/EFI/Microsoft/Boot/bootmgfw.efi"]
    # בבקרה השלילית אין `gid` בחתימה — הטסט נופל על ההרשאות, לא על TypeError.
    extra = [gid] if "gid" in inspect.signature(vmdisk.boot_files).parameters else []
    target = vmdisk.boot_files(FakeBox(), 1, paths, run_dir, *extra)
    dirs = [target, target / "EFI", target / "EFI" / "BOOT", target / "EFI" / "Microsoft",
            target / "EFI" / "Microsoft" / "Boot"]
    files = [target / "EFI" / "BOOT" / "bootx64.efi",
             target / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"]
    for path in dirs:
        assert _mode(path) == 0o710, (path, oct(_mode(path)))
        assert os.lstat(path).st_gid == gid and os.lstat(path).st_uid == os.geteuid()
    for path in files:
        assert _mode(path) == 0o640, (path, oct(_mode(path)))
        assert os.lstat(path).st_gid == gid and os.lstat(path).st_uid == os.geteuid()


@posix_only
def test_capture_opens_the_work_area_to_the_vm_group_for_passage_only(tmp_path):
    """הבדיקה רצה עד הדחייה הראשונה (מערכת קבצים שהשתנתה) — ההרשאות כבר נקבעו."""
    from test_vmupdate_isolation import SOURCE, FakeGuest, NoHost
    work = tmp_path / "work"
    work.mkdir(mode=0o777)
    os.chmod(work, 0o777)
    (work / "disk.raw").write_bytes(b"")
    (work / "source-manifest.json").write_text(json.dumps(SOURCE), encoding="utf-8")
    guest = FakeGuest(fs={"/dev/sda1": "vfat", "/dev/sda2": "ext4"})
    with pytest.raises(vmdisk.DiskError):
        vmdisk.capture_disk(NoHost(), work, work / "out", lambda n: None,
                            user=vmdisk.VmUser(os.getuid(), os.getgid()),
                            appliance=lambda disk: vmdisk.Appliance(disk, factory=lambda: guest))
    for folder in (work, work / "run"):
        assert _mode(folder) == 0o710, (folder, oct(_mode(folder)))
        assert os.lstat(folder).st_gid == os.getgid()


@posix_only
def test_a_new_parent_for_the_vm_users_dirs_is_passage_only_for_its_group(tmp_path):
    parent = tmp_path / "work" / "run"
    child = agent_shell.fresh_user_dir(parent, os.getuid(), os.getgid())
    assert child.parent == parent
    assert _mode(parent) == 0o710, oct(_mode(parent))
    assert os.lstat(parent).st_gid == os.getgid()


# --- 2. ניקוי תגיות ב-JS -------------------------------------------------------


@pytest.mark.parametrize("name", ["vmupdate.js", "nicsvc.js"])
def test_tag_stripping_goes_through_strip_tags_not_the_regex(name):
    """הדפוס `/<[^>]*>/g` → "" משאיר `<script>` מ-`<<b>script>` (‏#1523).
    ‏stripTags עצמו נבדק ב-`strip_tags.test.cjs`."""
    source = (STATIC / name).read_text(encoding="utf-8")
    assert not re.search(r"""\.replace\(/<\[\^>\]\*>/g,\s*(""|'')\)""", source), name
    assert "stripTags(" in source, name


# --- 3. log-injection -----------------------------------------------------------


def test_a_gui_failure_that_was_not_journaled_logs_the_mac_on_one_line(monkeypatch, caplog):
    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(gui_failure, "journal", broken)
    with caplog.at_level(logging.ERROR, logger="imagectl.gui_failure"):
        noted = gui_failure.note(None, "aa:bb\r\nFORGED: admin logged in",
                                 {"screen": "build", "boot_next": "set"}, known=True)
    assert noted is False
    messages = [r.getMessage() for r in caplog.records if r.name == "imagectl.gui_failure"]
    assert len(messages) == 1 and "FORGED" in messages[0], messages
    assert "\n" not in messages[0] and "\r" not in messages[0], messages
