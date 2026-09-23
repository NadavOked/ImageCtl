"""בדיקות הסוכן — הלוגיקה שרצה ב-initramfs.

הסוכן כתוב ב-POSIX sh כי בסביבת ה-boot יש רק busybox. הבדיקות מריצות
את אותם קבצים בדיוק דרך bash מקומי: טבלת ההחלטה נבדקת שורה-שורה,
וה-JSON שהסוכן בונה ביד (hello, progress) מפוענח ב-python ומושווה
לממשקים — לא רק "נראה תקין" אלא נטען בפועל.

מה שלא נבדק כאן: הצינור עצמו (udp-receiver/partclone) — הוא דורש חומרה,
והוא ברשימת הבדיקות הידניות של שלב א'.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
import sizelimit

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
INSTALLER = REPO / "installer"


def find_bash() -> str | None:
    """ב-Windows מעדיפים את Git Bash על פני ה-bash של WSL שיושב
    ב-System32 — הוא זה שקיים בכל עמדת פיתוח של הפרויקט."""
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()

# בלי bash כל סוכן ה-POSIX אינו נבדק. בעמדת פיתוח בווינדוס זה דילוג
# לגיטימי; במקום שבו bash אמור להיות — כישלון, לא ירוק (#52).
pytestmark = requires_native(("bash", BASH))

SH_FILES = sizelimit.guarded_shell_files(REPO)


def sh(script: str, cwd: Path | None = None) -> str:
    """מריץ קטע bash ומחזיר stdout. כשל = כשל בדיקה עם stderr מלא.

    כשמריצים את bash.exe ישירות (לא דרך מסוף Git Bash) הנתיב /usr/bin
    לא נמצא ב-PATH, ואיתו cat/basename/awk — לכן הוא מתווסף כאן."""
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True,
        text=True,
        encoding="utf-8",  # הסוכן מדפיס UTF-8; ‏cp1255 של ווינדוס נופל על א (D7 90)
        cwd=str(cwd or REPO),
        stdin=subprocess.DEVNULL,  # לא יורשים את ה-stdin של pytest — בווינדוס
        # ה-handle שלו נשבר בריצה רב-קבצית ו-DuplicateHandle נופל (WinError 50)
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stderr}\n{proc.stdout}"
    return proc.stdout


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


# --- כל הקבצים חייבים לעבור פרסינג -----------------------------------------


@pytest.mark.parametrize("path", SH_FILES + [REPO / "tools" / "build_initramfs.sh"],
                         ids=lambda p: p.name)
def test_script_parses(path):
    # ‏#573: גם `stderr` נלכד, ולא רק `stdin`. תת-תהליך שיורש את
    # ‏`stderr` של pytest תחת capture נכשל ב-`DuplicateHandle` ברגע
    # שה-handle כבר אינו תקף — ‏`OSError: [WinError 6]` — וכל 27
    # הפרמטרים נופלים יחד על קבצים שאיש לא נגע בהם.
    #
    # ⚠️ **נמדד בשני הכיוונים, משתנה אחד:** אותה פקודה לבדה נותנת
    # ‏179 עוברים; עם `pytest` שני שרץ במקביל — ‏27 נכשלים. זו
    # המשפחה של #14, שבה `stdin=DEVNULL` כבר סגר את הצד האחד.
    #
    # ‏`check=True` נשמר, ו-`stderr` נכנס לחריגה במקום להיעלם —
    # שגיאת תחביר של `bash -n` היא בדיוק מה שהטסט הזה קיים בשבילו.
    proc = subprocess.run([BASH, "-n", posix(path)],
                          stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace")
    assert proc.returncode == 0, f"{path.name}: {proc.stdout}"


@pytest.mark.parametrize("path", SH_FILES, ids=lambda p: p.name)
def test_agent_files_stay_small(path):
    """מגבלת השורות של הפרויקט — קיר ב-300, נורה ב-280 (#483)."""
    sizelimit.assert_within_limit(path)


# --- טבלת ההחלטה -------------------------------------------------------------


def decide(schema="1", known="true", role="classroom",
           task="null", session="none", mode="normal") -> str:
    out = sh(
        f'D_SCHEMA={schema!r} D_KNOWN={known!r} D_ROLE={role!r} '
        f'D_TASK={task!r} D_SESSION_STATE={session!r} D_MODE={mode!r}; '
        f'export D_SCHEMA D_KNOWN D_ROLE D_TASK D_SESSION_STATE D_MODE; '
        f'. {posix(AGENT)}/lib/decide.sh; decide'
    )
    return out.strip()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        # גרסת ממשק לא מוכרת — לא מנחשים, עולים מהדיסק. גם כשיש סבב רץ.
        (dict(schema="2", session="running"), "local"),
        # MAC לא רשום — לא מציעים דבר, גם לא במצב recovery.
        (dict(known="false", mode="recovery"), "unknown"),
        (dict(known="false"), "unknown"),
        # ESC במקלדת — תפריט השחזור, רק למי שהשרת מכיר.
        (dict(mode="recovery"), "recovery"),
        (dict(mode="recovery", session="running"), "recovery"),
        # משימה ישירה גוברת על הסבב.
        (dict(task='{"id":"tsk_1"}', session="running"), "task"),
        # כיתה: פתוח → ממתינים; רץ → כותבים; כל השאר → דיסק מקומי.
        (dict(session="open"), "wait_open"),
        (dict(session="running"), "restore"),
        (dict(session="closed"), "local"),
        (dict(session="none"), "local"),
        # חדר שיכפולים: אין OS מקומי — מחכים לעבודה במקום לאתחל.
        (dict(role="cloner", session="none"), "wait_poll"),
        (dict(role="cloner", session="open"), "wait_poll"),
        (dict(role="cloner", session="running"), "restore"),
        # מחשב הבנייה עולה מהרשת בכוונה — הוא ממתין להזמנת קליטה,
        # ולא משתתף בסבבים גם כשהם רצים.
        (dict(role="build"), "build_console"),
        (dict(role="build", session="running"), "build_console"),
        # תפקיד לא מוכר — דיסק מקומי, כמו כל מצב לא ברור.
        (dict(role="banana"), "local"),
    ],
    ids=lambda v: str(v),
)
def test_decision_table(kwargs, expected):
    assert decide(**kwargs) == expected


# --- hello — הסוכן בונה את הממשק ביד, ולכן חייבים לפרסר אותו באמת -----------


@pytest.fixture()
def fake_machine(tmp_path):
    """עץ /sys ו-/dev מזויף: שני כרטיסי רשת, דיסק GPT אחד, וכל מה
    ש-build_hello קורא ממנו."""
    sysroot = tmp_path / "root"
    net = sysroot / "sys/class/net"
    for name, mac in [("eth0", "b4:2e:99:07:1a:c4"), ("eth1", "b4:2e:99:07:1a:c5")]:
        (net / name).mkdir(parents=True)
        (net / name / "address").write_text(mac + "\n")
    (net / "lo").mkdir()
    (net / "lo" / "address").write_text("00:00:00:00:00:00\n")

    (sysroot / "proc").mkdir()
    (sysroot / "proc/meminfo").write_text("MemTotal:        8388608 kB\n")

    dmi = sysroot / "sys/class/dmi/id"
    dmi.mkdir(parents=True)
    (dmi / "product_uuid").write_text("4C4C4544-0037-AAAA-BBBB-CCCCDDDDEEEE\n")

    efivars = sysroot / "sys/firmware/efi/efivars"
    efivars.mkdir(parents=True)
    (efivars / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(
        b"\x07\x00\x00\x00\x01"
    )

    blk = sysroot / "sys/block"
    (blk / "sda").mkdir(parents=True)
    (blk / "sda/size").write_text("500118192\n")          # סקטורים של 512
    (blk / "sda/removable").write_text("0\n")
    (blk / "sda/queue").mkdir()
    # גודל הסקטור הלוגי — ממנו נגזר איפה יושבת כותרת ה-GPT (‏#126).
    # לכל כונן אמיתי יש את הקובץ הזה, ולכן גם למכונה המזויפת.
    (blk / "sda/queue/logical_block_size").write_text("512\n")
    (blk / "sda/device").mkdir()
    (blk / "sda/device/model").write_text("Samsung SSD 870 EVO 250GB   \n")
    (blk / "loop0").mkdir()                               # חייב להיות מסונן

    dev = tmp_path / "dev"
    dev.mkdir()
    (dev / "sda").write_bytes(b"\x00" * 512 + b"EFI PART" + b"\x00" * 100)

    run = tmp_path / "run"
    run.mkdir()
    return {"sysroot": sysroot, "dev": dev, "run": run}


def test_hello_matches_the_interface(fake_machine):
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    )
    hello = json.loads(out)

    assert hello["schema"] == 2          # #720: hello נושא מלאי חומרה (inventory.sh)
    assert hello["mac"] == "b4:2e:99:07:1a:c4"
    assert hello["all_macs"] == ["b4:2e:99:07:1a:c4", "b4:2e:99:07:1a:c5"]
    assert hello["ip"] == "10.44.12.187"
    assert hello["hostname_current"] is None
    assert hello["uuid"].startswith("4C4C4544-0037")
    assert hello["firmware"] == "uefi"
    assert hello["secure_boot"] is True
    assert hello["memory_bytes"] == 8388608 * 1024

    (disk,) = hello["disks"]
    assert disk["dev"] == "sda"
    assert disk["size_bytes"] == 500118192 * 512
    assert disk["model"] == "Samsung SSD 870 EVO 250GB"   # בלי הרווחים
    assert disk["serial"] is None                          # SATA בלי sysfs serial
    assert disk["removable"] is False
    assert disk["scheme"] == "gpt"
    assert disk["has_data"] is True
    # אין ataN בנתיב (כמו על VM עם SCSI) — אין חריץ, והדיווח יוצא כרגיל.
    assert disk["port"] is None
    # ‏#652: בלי smart.sh (ובלי probe) שדה הבריאות הוא `unchecked` —
    # לא-נבדק, לא נכשל. ה-probe עצמו רץ רק בצד השחזור.
    assert disk["smart"] == "unchecked"


# --- #839: סוד המוניטור — המכונה מגרילה באתחול, ו-hello נושא אותו -----------

SECRET_HEX = re.compile(r"^[0-9a-f]{32}$")


def _hello_with_monitor_lib(fake_machine) -> dict:
    return json.loads(sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'. {posix(AGENT)}/lib/monitor.sh; '
        f'build_hello'
    ))


def test_hello_carries_a_boot_monitor_secret_that_is_stable_per_boot(fake_machine):
    """‏32 ספרות hex, מוגרלות פעם אחת ל-RUN_DIR (= לאתחול): שני hello
    באותו אתחול נושאים את אותו סוד, והקובץ שהמוניטור יקרא מכיל בדיוק
    אותו. זה הסוד שהפרוקסי יחשב ממנו HMAC ל-challenge של 5900."""
    first = _hello_with_monitor_lib(fake_machine)
    assert SECRET_HEX.match(first["monitor_secret"]), first["monitor_secret"]
    assert first["monitor_auth"] == "hmac"
    second = _hello_with_monitor_lib(fake_machine)
    assert second["monitor_secret"] == first["monitor_secret"]
    assert second["monitor_auth"] == "hmac"
    on_disk = (fake_machine["run"] / "monitor.secret").read_text().strip()
    assert on_disk == first["monitor_secret"]


def test_hello_picks_up_a_secret_rotated_after_a_connection(fake_machine):
    """סימולציה של מה ש-imagectl-monitor כותב אחרי חיבור שהסתיים: קובץ
    סוד חדש. ה-hello הבא נושא אותו, לא את הישן."""
    first = _hello_with_monitor_lib(fake_machine)
    rotated = "ffeeddccbbaa99887766554433221100"
    (fake_machine["run"] / "monitor.secret").write_bytes((rotated + "\n").encode())
    second = _hello_with_monitor_lib(fake_machine)
    assert second["monitor_secret"] == rotated
    assert second["monitor_secret"] != first["monitor_secret"]
    assert second["monitor_auth"] == "hmac"


def test_hello_omits_the_secret_when_the_monitor_lib_is_absent(fake_machine):
    """בלי monitor.sh אין מי שיגריל — והשדה נעדר, לא ריק ולא מומצא
    (השרת מתעלם מערך פגום; העדר השדה משאיר את הסוד הקודם)."""
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    )
    body = json.loads(out)
    assert "monitor_secret" not in body
    assert "monitor_auth" not in body


# --- החריץ הפיזי של המגירה (#27) ---------------------------------------------


# --- Secure Boot: שלושה מצבים, לא שניים (#84) --------------------------------


def secure_boot_of(sysroot: Path) -> str:
    return sh(
        f'export SYSROOT={posix(sysroot)!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'detect_secure_boot'
    ).strip()


def test_a_bios_machine_reports_secure_boot_off_because_it_really_is(tmp_path):
    """אין ‏/sys/firmware/efi — אין Secure Boot, ו-`false` הוא ידיעה."""
    assert secure_boot_of(tmp_path) == "false"


def test_a_uefi_machine_whose_efivars_cannot_be_read_reports_unknown(tmp_path):
    """זה השדה ששיקר חודשים.

    ‏`efivarfs` הוא מודול (‏CONFIG_EFIVAR_FS=m) שלא נארז ב-initramfs,
    ולכן המאונט נכשל בשקט — ומכונה שה-Secure Boot **דלוק** בה דיווחה
    "כבוי". ‏`false` שם היה טענה חיובית שגויה; `null` הוא מה שידענו.
    """
    (tmp_path / "sys/firmware/efi").mkdir(parents=True)
    assert secure_boot_of(tmp_path) == "null"


@pytest.mark.parametrize("data_byte, expected", [(1, "true"), (0, "false")])
def test_the_efi_variable_is_read_when_it_is_there(tmp_path, data_byte, expected):
    """‏4 בייטי מאפיינים ואז בייט הנתון — כשאפשר לקרוא, קוראים."""
    efivars = tmp_path / "sys/firmware/efi/efivars"
    efivars.mkdir(parents=True)
    (efivars / "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c").write_bytes(
        bytes([7, 0, 0, 0, data_byte]))
    assert secure_boot_of(tmp_path) == expected


def test_unknown_reaches_hello_as_a_bare_null_not_a_string(fake_machine):
    """‏`null` חייב להגיע כ-JSON null — המחרוזת "null" היא ערך, לא היעדרו."""
    efivars = fake_machine["sysroot"] / "sys/firmware/efi/efivars"
    for leftover in efivars.iterdir():
        leftover.unlink()
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    )
    assert json.loads(out)["secure_boot"] is None


def test_missing_uuid_reaches_hello_as_a_bare_null_not_an_empty_string(fake_machine):
    """#524: UUID חסר ב-DMI הוא JSON null, לא מחרוזת ריקה — כמו serial/port."""
    (fake_machine["sysroot"] / "sys/class/dmi/id/product_uuid").unlink()
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    )
    hello = json.loads(out)
    assert hello["uuid"] is None


def port_from(path: str, sysroot: Path | None = None) -> str:
    prefix = f"export SYSROOT={posix(sysroot)!r}; " if sysroot else ""
    return sh(
        prefix + f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f"port_from_path {path!r}"
    ).strip()


PCI = "/sys/devices/pci0000:00/0000:00:17.0"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # AHCI: החריץ הוא ataN, ולא סדר הגילוי של הקרנל.
        (f"{PCI}/ata1/host0/target0:0:0/0:0:0:0", "1"),
        (f"{PCI}/ata2/host1/target1:0:0/1:0:0:0", "2"),
        (f"{PCI}/ata3/host2/target2:0:0/2:0:0:0", "3"),
        (f"{PCI}/ata12/host11/target11:0:0/11:0:0:0", "12"),
        # אין ממה לגזור — שדה ריק, לא ניחוש.
        ("/sys/devices/pci0000:00/0000:00:1d.0/nvme/nvme0/nvme0n1", ""),
        ("/sys/devices/vmbus_0/host0/target0:0:0/0:0:0:0", ""),
        ("/sys/block/sda/device", ""),
        ("", ""),
    ],
    ids=lambda v: str(v),
)
def test_the_sata_slot_is_read_from_the_sysfs_link(path, expected):
    assert port_from(path) == expected


@pytest.mark.parametrize(
    ("driver", "expected"),
    # ה-scsi host הראשון של בקר ATA הוא ata1 — hostN הוא חריץ N+1.
    # ‏storvsc של VM מספר תורים, לא מגירות: עדיף בלי מספר מאשר מספר שגוי.
    [("ahci", "2"), ("sata_nv", "2"), ("storvsc", ""), ("mptspi", "")],
)
def test_the_host_fallback_counts_only_ata_controllers(tmp_path, driver, expected):
    sysroot = tmp_path / "root"
    host = sysroot / "sys/class/scsi_host/host1"
    host.mkdir(parents=True)
    (host / "proc_name").write_text(driver + "\n")
    assert port_from("/sys/devices/vmbus_0/host1/target1:0:0/1:0:0:0",
                     sysroot) == expected


def test_hello_reports_the_slot_of_a_sata_drawer(fake_machine):
    """מקצה לקצה: הקישור האמיתי ב-sysfs → שדה `port` ב-hello."""
    sysroot = fake_machine["sysroot"]
    # שמות הרכיבים כאן בלי נקודתיים — הן אינן חוקיות בשם קובץ בווינדוס.
    # מה שנבדק הוא רכיב ה-ataN בנתיב, וזה בדיוק מה שהקרנל שם שם.
    target = sysroot / "sys/devices/pci0000_00/ata2/host1/target1_0_0/1_0_0_0"
    target.mkdir(parents=True)
    (target / "model").write_text("Samsung SSD 870 EVO 250GB   \n")
    device = sysroot / "sys/block/sda/device"
    shutil.rmtree(device)
    try:
        device.symlink_to(target, target_is_directory=True)
    except OSError as exc:                      # ווינדוס בלי הרשאת symlink
        pytest.skip(f"symlinks are not available here: {exc}")

    out = sh(
        f'export SYSROOT={posix(sysroot)!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    )
    (disk,) = json.loads(out)["disks"]
    assert disk["port"] == 2                    # ata2 = המגירה האמצעית
    assert disk["dev"] == "sda"                 # שם ההתקן נשאר כפי שהוא


def test_mac_stays_canonical(fake_machine):
    """התבנית הקנונית — lowercase עם נקודתיים — נשמרת כמו שהיא מ-sysfs."""
    out = sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'list_macs'
    )
    for mac in out.split():
        assert re.fullmatch(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", mac)


# --- progress ----------------------------------------------------------------


def test_progress_matches_the_interface(tmp_path):
    run = tmp_path / "run"
    for dev, state, base, raw, total in [
        ("sda", "writing", 1000, "500\n2500\n", 57982058496),
        ("sdb", "done", 57982058496, "", 57982058496),
        ("sdc", "failed", 0, "4194304\n", 57982058496),
    ]:
        t = run / "targets" / dev
        t.mkdir(parents=True)
        (t / "state").write_text(state)
        (t / "base").write_text(str(base))
        (t / "bytes.raw").write_text(raw)
        (t / "total").write_text(str(total))
    (run / "targets/sdc/error").write_text("I/O error at sector 8419328")
    (run / "state").write_text("writing")

    out = sh(
        f'export RUN_DIR={posix(run)!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/progress.sh; '
        f'build_progress ses_a91f b4:2e:99:07:1a:c4'
    )
    report = json.loads(out)

    assert report["session_id"] == "ses_a91f"
    assert report["mac"] == "b4:2e:99:07:1a:c4"
    assert report["state"] == "writing"

    by_dev = {t["dev"]: t for t in report["targets"]}
    assert len(by_dev) == 3
    # בייטים = מחיצות שהסתיימו + השורה האחרונה של pv, לפי סעיף 7.
    assert by_dev["sda"]["bytes_written"] == 1000 + 2500
    assert by_dev["sdb"]["state"] == "done"
    assert by_dev["sdb"]["bytes_written"] == by_dev["sdb"]["bytes_total"]
    # יעד שנכשל נושא error — ולא מפיל את הדיווח של השאר.
    assert by_dev["sdc"]["state"] == "failed"
    assert "I/O error" in by_dev["sdc"]["error"]


# --- שורת הפקודה של הקרנל — הממשק שאסור שיתרחב -----------------------------


def test_cmdline_reads_only_server_and_mode(tmp_path):
    """פרט משימה שמנסה לעבור דרך הקרנל חייב להיזרק. אחרת נולד ממשק
    שני, לא מתועד — בדיוק מה שהמחולל נבדק עליו מהצד השני."""
    cmdline = tmp_path / "cmdline"
    cmdline.write_text(
        "quiet imagectl.server=10.0.0.1:8080 imagectl.mode=recovery "
        "imagectl.image_id=img_7f3a91 imagectl.session=ses_a91f\n"
    )
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export CMDLINE_FILE={posix(cmdline)!r} RUN_DIR={posix(run)!r}; '
        f'. {posix(AGENT)}/lib/common.sh; parse_cmdline >/dev/null 2>&1; '
        f'printf "%s|%s\\n" "$IMAGECTL_SERVER" "$IMAGECTL_MODE"; '
        f'set | grep "^IMAGECTL_" || true'
    )
    first, vars_dump = out.split("\n", 1)
    assert first == "10.0.0.1:8080|recovery"
    # שום משתנה עם פרטי המשימה לא נוצר.
    assert "img_7f3a91" not in vars_dump
    assert "ses_a91f" not in vars_dump


# --- SSH לטכנאי (#44) --------------------------------------------------------
# עשרים תחנות על צינור סריאלי אחד לא עובד. ה-SSH נכנס מאחורי אותו שער
# בדיוק של מעטפת הניפוי — imagectl.debug=1 — ובלי סיסמאות: ה-initramfs
# מוגש ב-HTTP פתוח, כך שכל סיסמה בתוכו היא סיסמה מפורסמת.


#: טבלת הסוקטים של הקרנל, כפי שהיא נראית באמת. ‏0016 = 22, ‏0A = LISTEN.
PROC_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr "
               "tm->when retrnsmt   uid  timeout inode\n")
PROC_LISTENING = PROC_HEADER + (
    "   0: 00000000:0016 00000000:0000 0A 00000000:00000000 "
    "00:00000000 00000000 0 0 1\n")


#: מפתח ed25519 קנוני לזיוף dropbearkey -y — blob של 51 בייט, טביעת
#: SHA256 כפי ש-OpenSSH מחשב. הסוכן מחשב את אותה טביעה מ-openssl.
SSH_PUB_A = ("ssh-ed25519 "
             "AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f")
SSH_FP_A = "SHA256:ZkAslGjFiUHdGf/WUL8rQvkib4PTvQatUV0OUQSncCA"


def run_ssh_start(tmp_path, key_text=None, with_dropbear=True, listening="yes",
                  ip="10.44.12.187"):
    """מריץ ssh_start בארגז חול: dropbear/dropbearkey מזויפים ובית זמני.

    ‏_ssh_spawn נדרס אחרי ה-source כדי ללכוד את שורת הפקודה בלי להשאיר
    דמון רץ — הבדיקה בודקת במה הדמון מופעל, לא שהוא עלה.

    ‏`listening` שולט במה שהקרנל *יגיד*: ‏"yes" מאזין, ‏"no" הטבלה
    נקראה ואין שם כלום, ‏"unreadable" אין טבלה בכלל. הטבלה חייבת
    להיות מזויפת — בלעדיה בדיקה שרצה על מכונה ש-sshd פעיל בה הייתה
    מוצאת את פורט 22 שלה ומדווחת הצלחה (אותו לקח של SSH_DROPBEAR)."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    proc_net = box / "procnet"
    if listening != "unreadable":
        proc_net.mkdir()
        (proc_net / "tcp").write_text(
            PROC_LISTENING if listening == "yes" else PROC_HEADER)
    # הזיופים נכתבים מתוך bash ולא מ-python: ב-Git Bash קובץ שנוצר
    # מווינדוס אינו נחשב בר-הרצה, ואז command -v לא מוצא אותו. ב-CI
    # ‏(Linux) ההפך הוא הנכון — שם `cat >` יוצר קובץ בלי סיבית הרצה,
    # ולכן ה-chmod חייב להיות כאן: בלעדיו הבדיקה עוברת בווינדוס ונופלת
    # על Linux בלבד.
    make_stubs = ""
    if with_dropbear:
        make_stubs = (
            f"cat > {posix(stub_dir)}/dropbear <<'STUB'\n"
            "#!/bin/sh\nexit 0\n"
            "STUB\n"
            f"cat > {posix(stub_dir)}/dropbearkey <<'STUB'\n"
            "#!/bin/sh\n"
            "y=0; file=\n"
            'while [ $# -gt 0 ]; do\n'
            '    case "$1" in\n'
            '        -y) y=1 ;;\n'
            '        -f) file=$2; shift ;;\n'
            "    esac\n"
            "    shift\n"
            "done\n"
            'if [ "$y" = 1 ]; then\n'
            "    echo 'Public key portion is:'\n"
            f"    echo '{SSH_PUB_A}'\n"
            "    echo 'Fingerprint: md5 aa:bb'\n"
            "    exit 0\n"
            "fi\n"
            '[ -n "$file" ] && printf fake-host-key > "$file"\n'
            "STUB\n"
            f"chmod 0755 {posix(stub_dir)}/dropbear {posix(stub_dir)}/dropbearkey\n"
        )
    keys = box / "packed-authorized_keys"
    if key_text is not None:
        keys.write_text(key_text)
    run = box / "run"
    run.mkdir()
    home = box / "home"
    spawned = box / "spawned"
    out = sh(
        make_stubs
        # ‏PATH חייב נתיבי POSIX: ‏"C:/..." נחתך על הנקודתיים בשמו של
        # הכונן, ואז command -v לא מוצא כלום.
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} SSH_HOME={posix(home)!r} '
        f'SSH_KEYS={posix(keys)!r} SSH_PROC_NET={posix(proc_net)!r} '
        f'IP={ip!r} SSH_VERIFY_TRIES=1; '
        # ‏PATH לבדו אינו מבודד: על מכונה ש-dropbear מותקן בה (שרת
        # המעבדה) `command -v dropbear` מצא את זה של המערכת, והמקרה
        # השלילי "עבר" רק במקומות שבהם הוא לא מותקן.
        + ("" if with_dropbear else "export SSH_DROPBEAR=imagectl-absent-dropbear; ")
        + f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sshd.sh; '
        f'_ssh_spawn() {{ printf "%s\\n" "$*" > {posix(spawned)!r}; }}; '
        f'ssh_start >/dev/null 2>&1; echo "rc=$?"'
    )
    return out.strip(), spawned, home, run


LAB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIylM5T imagectl-lab\n"


def test_ssh_does_not_listen_without_a_packed_key(tmp_path):
    """בלי authorized_keys אין ברירת מחדל של סיסמה — פשוט לא מאזינים."""
    rc, spawned, _home, _run = run_ssh_start(tmp_path, key_text=None)
    assert rc == "rc=1"
    assert not spawned.exists()


def test_ssh_does_not_listen_without_dropbear_in_the_image(tmp_path):
    rc, spawned, _home, _run = run_ssh_start(
        tmp_path, key_text=LAB_KEY, with_dropbear=False
    )
    assert rc == "rc=1"
    assert not spawned.exists()


def test_ssh_runs_dropbear_with_passwords_disabled(tmp_path):
    rc, spawned, home, _run = run_ssh_start(tmp_path, key_text=LAB_KEY)
    assert rc == "rc=0"
    argv = spawned.read_text().split()
    assert argv[0] == "dropbear"
    # ‏-s אין סיסמאות, ‏-g גם לא ל-root, ‏-j/-k בלי מנהור יציאות.
    for flag in ("-s", "-g", "-j", "-k"):
        assert flag in argv, f"dropbear רץ בלי {flag}"
    assert "-r" in argv
    installed = home / ".ssh" / "authorized_keys"
    assert installed.read_text() == LAB_KEY


def test_dropbear_binds_the_deployment_ip_not_wildcard(tmp_path):
    """R20-F7 / #1080: ‏-p <IP>:22, לא 0.0.0.0 ולא פורט בלי כתובת."""
    rc, spawned, _home, _run = run_ssh_start(tmp_path, key_text=LAB_KEY)
    assert rc == "rc=0"
    argv = spawned.read_text().split()
    assert "-p" in argv
    assert argv[argv.index("-p") + 1] == "10.44.12.187:22"
    assert "0.0.0.0" not in spawned.read_text()


def test_ssh_does_not_listen_without_a_deployment_ip(tmp_path):
    """בלי $IP אין האזנה על 0.0.0.0 — הכיוון הבטוח הוא לא לפתוח דלת."""
    rc, spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY, ip="")
    assert rc == "rc=1"
    assert not spawned.exists()
    assert "0.0.0.0" in (run / "agent.log").read_text(encoding="utf-8")


def test_a_daemon_that_never_bound_the_port_is_reported_as_not_listening(tmp_path):
    """הבאג שהיה כאן עד #83: ‏ssh_start כתב "dropbear on port 22"
    מיד אחרי ההרצה. ‏dropbear יוצא על מפתח host פגום, על פורט תפוס
    ועל חשבון ש-NSS לא מוצא — וכל אחד מאלה נראה בדיוק כמו הצלחה.
    היעדר סימן כישלון אינו ראיה (עיקרון 5)."""
    rc, spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY,
                                            listening="no")
    assert rc == "rc=1"
    assert spawned.exists()                 # הופעל — פשוט לא תפס פורט
    log = (run / "agent.log").read_text(encoding="utf-8")
    assert "did not bind" in log
    assert "listening on port" not in log


def test_a_socket_table_that_cannot_be_read_is_not_a_closed_port(tmp_path):
    """שלושה מצבים, לא שניים: "לא הצלחנו לבדוק" נאמר במפורש."""
    rc, _spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY,
                                             listening="unreadable")
    assert rc == "rc=1"
    assert "cannot read" in (run / "agent.log").read_text(encoding="utf-8")


def test_a_listener_the_kernel_confirms_is_the_only_success(tmp_path):
    _rc, _spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY)
    assert "listening on port 22" in (run / "agent.log").read_text(encoding="utf-8")


def test_ssh_makes_its_host_key_at_boot_not_at_build(tmp_path):
    """מפתח host שנארז בבנייה היה אותו מפתח פרטי בכל תחנה במכללה,
    ובקובץ שמוגש ב-HTTP פתוח. הוא נוצר בעלייה, ב-tmpfs."""
    _rc, spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY)
    hostkey = spawned.read_text().split()[-1]
    assert hostkey.startswith(posix(run)), "מפתח ה-host לא ב-RUN_DIR"
    assert Path(hostkey).read_text() == "fake-host-key"
    # הבנאי אורז את dropbearkey (הסוכן צריך אותו בעלייה) אבל לעולם
    # לא מריץ אותו — הרצה כזו הייתה צורבת מפתח פרטי לתוך התוצר.
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert not re.search(r"dropbearkey\s+-", builder), \
        "הבנאי מייצר מפתח host — אותו מפתח פרטי בכל תחנה, ובנייה לא דטרמיניסטית"


def test_ssh_only_starts_behind_the_debug_gate():
    """אותו שער של מעטפת הטכנאי, לא שער שני. תחנת תלמיד רגילה
    לא מאזינה לשום פורט. אחרי $IP, כי ה-bind הוא לכתובת ההפצה (#1080)."""
    text = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert text.count("ssh_start") == 1
    assert '[ "$IMAGECTL_DEBUG" = "1" ] && ssh_start' in text
    assert text.index("ssh_start") > text.index(". \"$NET_FILE\"")


def test_sshd_json_reads_the_pubkey_into_hello(tmp_path):
    """ה-fingerprint וה-pubkey מגיעים ל-hello; בלי מפתח — השדה נעדר."""
    rc, _spawned, _home, run = run_ssh_start(tmp_path, key_text=LAB_KEY)
    assert rc == "rc=0"
    sysroot = tmp_path / "sysroot"
    (sysroot / "proc/sys/kernel/random").mkdir(parents=True)
    (sysroot / "proc/sys/kernel/random/boot_id").write_text(
        "11111111-2222-3333-4444-555555555555\n")
    frag = sh(
        f'export PATH="$(cd {posix(tmp_path / "box" / "stubs")!r} && pwd):$PATH" '
        f'RUN_DIR={posix(run)!r} SYSROOT={posix(sysroot)!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sshd.sh; '
        f'printf "{{\\"x\\":0%s}}" "$(sshd_json)"'
    )
    body = json.loads(frag)
    assert body["ssh_hostkey"]["type"] == "ed25519"
    assert body["ssh_hostkey"]["pubkey"] == SSH_PUB_A
    assert body["ssh_hostkey"]["fingerprint"] == SSH_FP_A
    assert body["ssh_hostkey"]["boot_id"] == "11111111-2222-3333-4444-555555555555"


def test_hello_without_sshd_has_no_ssh_hostkey_field(fake_machine):
    hello = json.loads(sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
        f'build_hello'
    ))
    assert "ssh_hostkey" not in hello


def test_the_builder_takes_the_authorized_keys_as_an_argument():
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "--ssh-key" in builder
    assert "authorized_keys" in builder


def test_the_builder_packs_the_account_dropbear_hands_the_session_to():
    """‏dropbear מחפש את החשבון דרך NSS. ‏/etc/passwd חסר או שורת
    ‏passwd חסרה ב-nsswitch = כניסה נדחית בשקט — הלקח מ-#33 שוב."""
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "/etc/passwd" in builder
    assert "passwd: files" in builder


# --- אשף השחזור: כניסה לפני התפריט (#80) ------------------------------------


def wizard(tmp_path, keys, require_login="true", codes=("200",), delay=0.0):
    """מריץ את `recovery_flow` עם קלט מוקלד ומחזיר (פלט, קוד יציאה).
    ‏`delay` > 0 = האדם מקליד רק אחרי כל כך הרבה שניות (#912) — הקלט מגיע
    אז מצינור בתוך bash ולא מ-`input=`; ‏`build_hello`/`http_post_json`
    מזויפים ופעימת ה-hello נרשמת ל-`hellos`. ‏attended.sh נטען רק אם הוא
    קיים, כדי שהבקרה השלילית תיפול על ההתנהגות ולא על `.` של קובץ חסר.

    שלושה זיופים, כולם על תפרים שהקוד כבר מגדיר:

    * ‏`json_get` — jsonq.sh קיים כדי ש"תסתפקו בזיוף פונקציה אחת במקום
      לשלוח jq לכל סביבה". כאן זה גם מה שמאפשר לבדוק `require_login`
      חסר, מצב שאי אפשר לבטא בקובץ JSON תקין.
    * ‏`login_post` — מחזיר קוד HTTP מתור. זה כל העניין של #80 מבחינת
      עיקרון 5: ‏401 ("בדקנו — שגוי") ו-000/503 ("לא בדקנו") חייבים
      להיות שני מצבים, ולא "כישלון".
    * ‏`single_station_flow` / `class_round_flow` — עקבות בלבד. מה
      שנבדק כאן הוא הסדר והשער, לא השחזור עצמו.
    """
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    (run / "codes.txt").write_text("\n".join(codes) + "\n", encoding="utf-8")
    (run / "code_n").write_text("0\n", encoding="utf-8")
    attended = AGENT / "lib" / "attended.sh"
    feed = ""
    if delay:
        feed = ("{ command sleep " + str(delay) + "; printf '"
                + "".join(f"{k}\\n" for k in keys) + "'; } | ")
    script = (
        f'export RUN_DIR={posix(run)!r} MAC="b4:2e:99:07:1a:c4" '
        f'SERVER="http://127.0.0.1:1" RESP={posix(run / "resp.json")!r} '
        f'IMAGECTL_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=1 ATTENDED_BEAT_S=0.3 '
        f'REQUIRE_LOGIN={require_login!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; '
        f'. {posix(AGENT)}/lib/classround.sh; . {posix(AGENT)}/lib/ui.sh; . {posix(AGENT)}/lib/recovery.sh; '
        + (f'. {posix(attended)}; ' if attended.exists() else "")
        + 'build_hello() { printf %s "{\\"mac\\":\\"$MAC\\",\\"joining\\":$1}"; }; '
        'http_post_json() { cat "$2" >> "$RUN_DIR/hellos"; echo >> "$RUN_DIR/hellos"; }; '
        'json_get() { case "$2" in .ui.require_login) echo "$REQUIRE_LOGIN" ;; '
        '*) echo null ;; esac; }; '
        'login_post() { _n=$(cat "$RUN_DIR/code_n"); _n=$((_n + 1)); '
        'echo "$_n" > "$RUN_DIR/code_n"; echo "{}" > "$2"; '
        'sed -n "${_n}p" "$RUN_DIR/codes.txt"; }; '
        'single_station_flow() { echo "STUB-SINGLE user=${RECOVERY_USER:-}"; }; '
        'class_round_flow() { echo "STUB-CLASS user=${RECOVERY_USER:-}"; }; '
        + feed + 'recovery_flow'
    )
    # ‏input בבייטים ולא ב-text: עם `text=True` ווינדוס מתרגם כל `\n`
    # שנכתב לצינור ל-`\r\n`, ‏`read -r` במעטפת מקבל `0\r`, וה-`case`
    # לא מתאים — התשובה היא `invalid choice -- rebooting`. חמישה טסטים
    # נפלו כך על סיבה שאינה מה שהם בודקים (#479). הפלט נשאר text.
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, cwd=str(REPO),
        input=b"" if delay else "".join(f"{k}\n" for k in keys).encode("utf-8"),
    )
    return proc.stdout.decode("utf-8", "replace"), proc.returncode


def wizard_hellos(tmp_path) -> list[dict]:
    path = tmp_path / "run" / "hellos"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


MENU = "Deployment type:"
PROMPT = "  Username: "
WRONG = "Wrong username or password."


def test_the_login_screen_comes_before_the_menu(tmp_path):
    """הלב של #80: מי שנכשל בשלוש הכניסות לא ראה תפריט בכלל — כולל
    את עצם קיומו של מצב "סבב כיתה", שהוא מידע על מה שהשרת יודע לעשות."""
    out, code = wizard(tmp_path, ["a", "b"] * 3, codes=("401",) * 3)
    assert MENU not in out and "Class round" not in out
    assert out.count(PROMPT) == 3 and out.count(WRONG) == 3
    assert "TEST-REBOOT: login failed" in out and code == 86


def test_a_good_login_opens_the_menu_once(tmp_path):
    """כניסה תקינה → תפריט. הסדר נבדק כסדר, לא כנוכחות."""
    out, code = wizard(tmp_path, ["labtech", "pass", "0"])
    assert out.index(PROMPT) < out.index(MENU)
    assert out.count(PROMPT) == 1
    assert "TEST-REBOOT: user cancelled" in out and code == 86


def test_the_menu_choice_reaches_the_flow_with_the_credentials(tmp_path):
    out, _ = wizard(tmp_path, ["labtech", "pass", "1"])
    assert "STUB-SINGLE user=labtech" in out


def test_a_class_round_no_longer_asks_a_second_time(tmp_path):
    """הכניסה של השער היא הכניסה של הסבב — לא מסך שני אחרי הבחירה."""
    out, _ = wizard(tmp_path, ["labtech", "pass", "2"])
    assert out.count(PROMPT) == 1
    assert "STUB-CLASS user=labtech" in out


# --- #912: hello ממשיך בזמן שמסך הכניסה ממתין למפעיל --------------------------


def test_hello_keeps_going_while_the_recovery_sign_in_waits(tmp_path):
    """אותו דפוס כמו #906/#908, על אשף השחזור: תחנה שעומדת על "Username:"
    נראית בקונסולה "לא נראתה" אחרי ONLINE_SECONDS. כאן האדם מקליד רק אחרי
    ~3 פעימות (‏ATTENDED_BEAT_S=0.3), ולכן ≥2 hello עם `prompt: signin`,
    ‏`waiting_for: operator` ו-`joining: false`. בקרה שלילית: `recovery.sh`
    של main → 0."""
    out, _ = wizard(tmp_path, ["labtech", "pass", "1"], delay=0.9)
    assert "STUB-SINGLE user=labtech" in out, out
    beats = wizard_hellos(tmp_path)
    assert len(beats) >= 2, (beats, out)
    for h in beats:
        assert h["waiting_for"] == "operator" and h["prompt"] == "signin"
        assert h["joining"] is False and h["mac"] == "b4:2e:99:07:1a:c4"


def test_a_refused_recovery_sign_in_leaves_no_beat_behind(tmp_path):
    """‏`recovery_gate` מסתיים ב-`die_local` כשהכניסה נדחתה (#80). הפעימה
    נעצרת לפני כן — אחרי TEST-REBOOT מספר ה-hello אינו גדל, אחרת נשאר
    תהליך יתום שכותב `signin` על תחנה שכבר אתחלה."""
    out, code = wizard(tmp_path, ["a", "b"] * 3, codes=("401",) * 3, delay=0.4)
    assert "TEST-REBOOT: login failed" in out and code == 86
    beats = wizard_hellos(tmp_path)
    assert beats and all(h["prompt"] == "signin" for h in beats), (beats, out)
    before = len(beats)
    time.sleep(1.0)
    assert len(wizard_hellos(tmp_path)) == before


# --- שלושת המצבים: נדרשת / מוותרים / לא נאמר --------------------------------


def test_the_deployment_vlan_station_never_sees_a_login_screen(tmp_path):
    """‏#42: בוילן ההפצה `require_login` הוא false ואין שם מסך כניסה.
    הזזת הכניסה קדימה אסור שתמציא מסך במקום שלא היה בו."""
    out, _ = wizard(tmp_path, ["1"], require_login="false")
    assert PROMPT not in out
    assert MENU in out and "STUB-SINGLE user=" in out


def test_a_class_round_still_authenticates_where_the_login_was_waived(tmp_path):
    """ויתור על הכניסה הוא ויתור על שחזור בודד, לא על פתיחת סבב:
    פתיחת סבב היא החלטה, והשרת ידחה גוף בלי אישורים."""
    out, _ = wizard(tmp_path, ["2", "labtech", "pass"], require_login="false")
    assert out.index(MENU) < out.index(PROMPT)
    assert "STUB-CLASS user=labtech" in out


def test_a_server_that_did_not_say_is_not_a_server_that_waived(tmp_path):
    """עיקרון 5 על השדה עצמו: "לא נאמר" אינו "לא צריך סיסמה". שדה חסר
    מסתיים בדיסק המקומי — בלי תפריט ובלי מסך כניסה."""
    out, code = wizard(tmp_path, ["1"], require_login="null")
    assert MENU not in out and PROMPT not in out
    assert "TEST-REBOOT: the server did not say whether recovery needs a login" in out
    assert code == 86


# --- שלושת המצבים של הכניסה עצמה --------------------------------------------


@pytest.mark.parametrize("http_code", ["000", "503", "500"])
def test_a_server_that_never_answered_is_not_a_wrong_password(tmp_path, http_code):
    """‏http=000 שנספר כתשובה הוא הדפוס של עיקרון 5, וכאן הוא היה
    מוצג לטכנאי כ"סיסמה שגויה" — שולח אותו לתקן את הדבר הלא נכון.
    הכישלון גם לא מנסה שוב: הקלדה חוזרת לא מתקנת כבל."""
    out, code = wizard(tmp_path, ["labtech", "pass"], codes=(http_code,) * 3)
    assert WRONG not in out
    assert "The server did not answer -- the password was not checked." in out
    assert out.count(PROMPT) == 1
    assert "TEST-REBOOT: the server never checked the password" in out
    assert MENU not in out and code == 86


def test_a_wrong_password_is_still_three_tries(tmp_path):
    """המצב השלישי לא נבלע: 401 הוא תשובה, ומותר לנסות שוב."""
    out, _ = wizard(tmp_path, ["a", "b", "c", "d", "labtech", "pass", "0"],
                    codes=("401", "401", "200"))
    assert out.count(WRONG) == 2 and out.count(PROMPT) == 3
    assert out.index(WRONG) < out.index(MENU)


# --- כניסה ממסך השחזור -------------------------------------------------------


def test_the_login_body_survives_hostile_passwords(tmp_path):
    """סיסמה עם גרשיים ולוכסנים חייבת לצאת JSON תקין — נבנית ביד."""
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export RUN_DIR={posix(run)!r} MAC="b4:2e:99:07:1a:c4"; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; '
        f'. {posix(AGENT)}/lib/sysinfo.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/ui.sh; . {posix(AGENT)}/lib/recovery.sh; '
        'login_body "nadav" "pa\\"ss\\\\word"'
    )
    body = json.loads(out)
    assert body == {"username": "nadav", "password": 'pa"ss\\word',
                    "mac": "b4:2e:99:07:1a:c4"}


def test_the_open_round_body_matches_the_station_endpoint(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export RUN_DIR={posix(run)!r} MAC="b4:2e:99:07:1a:c4" '
        f'RECOVERY_USER="labtech" RECOVERY_PASS=\'p"ss\'; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/classround.sh; '
        f'open_round_body grp_LAB1 img_7f3a91'
    )
    body = json.loads(out)
    assert body == {
        "username": "labtech", "password": 'p"ss', "mac": "b4:2e:99:07:1a:c4",
        "group_id": "grp_LAB1", "image_id": "img_7f3a91",
    }


def test_the_open_round_body_carries_the_chosen_machines(tmp_path):
    """בחירת מחשבים בתחנה: הרשימה עוברת בשדה `macs`; בלעדיה — כל הכיתה."""
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export RUN_DIR={posix(run)!r} MAC="b4:2e:99:07:1a:c4" '
        f'RECOVERY_USER="labtech" RECOVERY_PASS="pass"; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/classround.sh; '
        'open_round_body grp_LAB1 img_7f3a91 '
        '\'["b4:2e:99:07:1a:c5","b4:2e:99:07:1a:c6"]\''
    )
    body = json.loads(out)
    assert body["macs"] == ["b4:2e:99:07:1a:c5", "b4:2e:99:07:1a:c6"]
    assert body["group_id"] == "grp_LAB1"


# --- הסיסמה בפתיחת סבב לא נוגעת במערכת הקבצים (#96) --------------------------


def run_open_round_post(tmp_path):
    """מריץ `open_round_post` מול curl מזויף שרושם את הארגומנטים ואת
    מה שקיבל ב-stdin. מחזיר (argv, גוף-שהתקבל, תיקיית ה-RUN)."""
    box = tmp_path / "box"
    stub_dir = box / "bin"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    run.mkdir()
    argv_file, stdin_file = box / "argv", box / "stdin"
    (stub_dir / "curl").write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > {posix(argv_file)}\n'
        f"cat > {posix(stdin_file)}\n"
        # התשובה של השרת נכתבת לקובץ שביקשו ב--o, כמו curl אמיתי.
        'while [ $# -gt 0 ]; do\n'
        '  [ "$1" = "-o" ] && { printf \'{"prefix":"LAB1"}\' > "$2"; }\n'
        "  shift\ndone\n"
    )
    out = sh(
        f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} MAC="b4:2e:99:07:1a:c4" '
        f'SERVER="http://10.44.12.10:8080" '
        f'RECOVERY_USER="labtech" RECOVERY_PASS="s3cret-in-the-classroom"; '
        f'chmod 0755 {posix(stub_dir)}/curl; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/classround.sh; '
        f'open_round_post grp_LAB1 img_7f3a91 "" {posix(run)!r}/open_resp.json; '
        'echo "rc=$?"'
    )
    assert out.strip().endswith("rc=0"), out
    return argv_file.read_text(), stdin_file.read_text(encoding="utf-8"), run


def test_the_round_body_reaches_curl_over_a_pipe(tmp_path):
    """הגוף נשלח מ-stdin, לא מקובץ — ‏`--data-binary @-`."""
    argv, body, _run = run_open_round_post(tmp_path)
    assert "--data-binary @-" in argv
    assert json.loads(body)["password"] == "s3cret-in-the-classroom"
    assert json.loads(body)["group_id"] == "grp_LAB1"


def test_opening_a_round_leaves_no_password_on_tmpfs(tmp_path):
    """הבקרה השלילית של #96, בצורתה המדידה: אחרי הפעולה אין שום קובץ
    ב-`$RUN_DIR` שהסיסמה נמצאת בו."""
    _argv, _body, run = run_open_round_post(tmp_path)
    leaked = [
        p.name for p in run.rglob("*")
        if p.is_file() and "s3cret-in-the-classroom" in p.read_text(
            encoding="utf-8", errors="replace")
    ]
    assert leaked == []


def test_the_class_round_flow_never_writes_the_body_to_a_file(tmp_path):
    """הבקרה השלילית של #96 על הזרימה עצמה.

    לפני התיקון `class_round_flow` הפנה את `open_round_body` לתוך
    `$RUN_DIR/open.json` וסמך על `rm -f` אחרי ה-POST — מחיקה שרצה רק
    אם `curl` חזר. ניקוי שצריך להצליח כדי שהתיקון יחזיק אינו תיקון,
    ולכן הדרישה היא שהגוף לא ייכתב מלכתחילה.
    """
    source = (AGENT / "lib" / "classround.sh").read_text(encoding="utf-8")
    # ההערות מדברות על הכשל הישן בשמו, ולכן נבדק הקוד בלבד.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    flow = code[code.index("class_round_flow() {"):]
    assert "open_round_body" not in flow, \
        "גוף הפתיחה (עם הסיסמה) נבנה שוב בתוך הזרימה במקום לזרום ל-curl"
    assert "open.json" not in code


# --- כתיבת שם המחשב (ממשק 5) -------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "suffix", "expected"),
    [("LAB1", "05", "LAB1-05"), ("lab2", "ins", "LAB2-INS"), ("LAB3", "INS", "LAB3-INS")],
)
def test_hostname_composition(prefix, suffix, expected):
    """סעיף 10: קידומת-סיומת, ו-INS תמיד באותיות גדולות."""
    out = sh(f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
             f'compose_hostname {prefix!r} {suffix!r}')
    assert out.strip() == expected


@pytest.mark.parametrize(
    "name",
    ["", "LAB1_05", "LAB1 05", "שם", "TOOLONGNAMEFORNETBIOS", "LAB1;rm -rf /"],
)
def test_a_bad_hostname_is_refused_before_touching_the_disk(tmp_path, name):
    """שם פסול נעצר לפני הרכבה — מחיצה לא נגעת, והפלט הוא שגיאה מסודרת."""
    run = tmp_path / "run"
    run.mkdir()
    out = sh(
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; '
        f'write_hostname sda /nonexistent.json {name!r} || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False
    assert result["code"] == "bad_hostname"


def test_the_hostname_result_matches_the_interface():
    """מבנה הפלט של סעיף 5 — נבנה ידנית, ולכן נבדק כ-JSON אמיתי."""
    out = sh(f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
             f'printf \'{{"ok":true,"hostname":"%s","method":"%s"}}\\n\' '
             f'"LAB1-05" "$HOSTNAME_METHOD"')
    assert json.loads(out) == {
        "ok": True, "hostname": "LAB1-05", "method": "offline-registry",
    }


# --- Linux: אזרח שווה (אפיון סעיף 14) ---------------------------------------


LINUX_MANIFEST = {
    "schema": 1, "id": "img_lin", "family": 256, "os": "linux",
    "scheme": "gpt", "sector_size": 512,
    "partitions": [
        {"index": 1, "type_guid": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
         "role": "esp", "fs": "vfat", "start_sector": 2048, "size_bytes": 1,
         "file": "p1.esp.pcl.zst", "sha256": "x", "expandable": False},
        {"index": 2, "type_guid": "0FC63DAF-8483-4772-8E79-3D69D8477DE4",
         "role": "linux", "fs": "ext4", "start_sector": 4096, "size_bytes": 1,
         "file": "p2.linux.pcl.zst", "sha256": "y", "expandable": True},
    ],
}


@pytest.mark.parametrize(
    ("fs", "tool"),
    [("ntfs", "partclone.ntfs"), ("ext4", "partclone.ext4"),
     ("btrfs", "partclone.btrfs"), ("vfat", "partclone.fat"), ("xfs", "partclone.dd")],
)
def test_every_supported_filesystem_has_a_partclone(fs, tool):
    out = sh(f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
             f'partclone_for_fs {fs}')
    assert out.strip() == tool


def test_expansion_knows_every_filesystem_family():
    """ntfsresize ל-Windows, resize2fs ל-ext4, btrfs resize, xfs_growfs."""
    source = (AGENT / "lib" / "grow.sh").read_text(encoding="utf-8")
    grow = source[source.index("grow_filesystem() {"):]
    assert "ntfsresize" in grow
    assert "resize2fs" in grow
    assert "btrfs filesystem resize max" in grow
    assert "xfs_growfs" in grow


def test_swap_is_recreated_not_streamed():
    restore = (AGENT / "lib" / "restore.sh").read_text(encoding="utf-8")
    capture = (AGENT / "lib" / "capture.sh").read_text(encoding="utf-8")
    assert "mkswap" in restore
    assert '"role\\":\\"swap\\"' in capture and '"file\\":null' in capture
    # ‏mkswap מוחק את החתימה הישנה, ולכן ה-UUID של מערכת הקבצים חייב
    # להיכנס למניפסט — אחרת אין ממה לשחזר אותו (#48).
    assert '"uuid\\":' in capture


def test_a_linux_image_is_named_through_etc_hostname(tmp_path):
    """שם המחשב במניפסט בלי מחיצת windows הולך למסלול Linux — לא לרג'יסטרי."""
    run = tmp_path / "run"
    run.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(LINUX_MANIFEST), encoding="utf-8")
    out = sh(
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(tmp_path / "nodev")!r} '
        f'LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; '
        f'. {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/hostname.sh; '
        f'write_hostname sda {posix(manifest)!r} LAB1-05 || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    # אין כונן אמיתי בבדיקה, ולכן ההרכבה נכשלת — אבל של מחיצת Linux.
    assert result["ok"] is False
    assert result["code"] == "mount_failed"
    assert "linux" in result["error"]


def test_linux_hostname_files_are_rewritten_the_installer_way(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hostname").write_text("ubuntu-build\n", encoding="utf-8")
    (etc / "hosts").write_text(
        "127.0.0.1\tlocalhost\n127.0.1.1\tubuntu-build\n\n::1 ip6-localhost\n",
        encoding="utf-8")
    sh(f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
       f'_write_linux_files {posix(etc)!r} LAB2-INS')
    assert (etc / "hostname").read_text(encoding="utf-8") == "LAB2-INS\n"
    hosts = (etc / "hosts").read_text(encoding="utf-8").splitlines()
    assert "127.0.1.1\tLAB2-INS" in hosts
    assert "127.0.0.1\tlocalhost" in hosts and "::1 ip6-localhost" in hosts
    assert not any("ubuntu-build" in line for line in hosts)


def test_linux_hosts_line_is_added_when_missing(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "hostname").write_text("x\n", encoding="utf-8")
    sh(f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/hostname.sh; '
       f'_write_linux_files {posix(etc)!r} LAB1-07')
    assert "127.0.1.1\tLAB1-07" in (etc / "hosts").read_text(encoding="utf-8")


def _stub(path: Path, body: str) -> str:
    # ‏chmod חובה: `cat >` יוצר קובץ בלי סיבית הרצה, וזיוף שלא ניתן להרצה
    # עובר בווינדוס (שם כל קובץ "בר-הרצה") ונופל ב-CI בלבד — כמו ב-run_expand.
    return (f"cat > {posix(path)} <<'STUB'\n#!/bin/sh\n{body}\nSTUB\n"
            f"chmod 0755 {posix(path)}\n")


def test_a_linux_hostname_write_that_cannot_unmount_is_not_success(tmp_path):
    """עיקרון 5: השם נכתב, אבל ה-umount נכשל — הדיסק נשאר עגון. אסור
    שזה ידווח `ok:true`. mount מצליח וכתיבה מצליחה, רק הניתוק נכשל."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    (run / "linux" / "etc").mkdir(parents=True)  # מה ש-mount היה יוצר
    stubs = (_stub(stub_dir / "mount", "exit 0")
             + _stub(stub_dir / "umount", "exit 1"))  # הניתוק נכשל
    plan = "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|4096|1|p2|y|false|u"
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; '
        f'manifest_plan() {{ printf \'%s\\n\' {plan!r}; }}; '
        f'partition_node() {{ echo /dev/fake2; }}; '
        f'write_hostname sda /dev/null LAB1-05 || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False
    assert result["code"] == "umount_failed"
    # והכתיבה עצמה כן קרתה — כדי לוודא שלא בלבלנו כישלון כתיבה בכישלון ניתוק.
    assert (run / "linux" / "etc" / "hostname").read_text().strip() == "LAB1-05"


def test_a_windows_hostname_write_that_cannot_unmount_is_not_success(tmp_path):
    """אותו עיקרון במסלול הרג'יסטרי: כתיבה ואימות עוברים, ה-umount נכשל."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    hive = run / "win" / "Windows" / "System32" / "config"
    hive.mkdir(parents=True)
    (hive / "SYSTEM").write_text("hive", encoding="utf-8")  # מה ש-ntfs-3g היה חושף
    # hivewrite: קריאה (-g) של Current מחזירה מספר סט־בקרה; כל קריאה אחרת
    # מחזירה את השם שנכתב, כך שהאימות (read-back) עובר. כתיבה יוצאת 0.
    hivewrite_body = (
        'if [ "$1" = "-g" ]; then\n'
        '  case "$*" in *Current*) echo 1;; *) echo LAB1-05;; esac\n'
        '  exit 0\nfi\nexit 0')
    stubs = (_stub(stub_dir / "ntfs-3g", "exit 0")
             + _stub(stub_dir / "hivewrite", hivewrite_body)
             + _stub(stub_dir / "umount", "exit 1"))  # הניתוק נכשל
    plan = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|4096|1|p1|y|false|u"
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; '
        f'manifest_plan() {{ printf \'%s\\n\' {plan!r}; }}; '
        f'partition_node() {{ echo /dev/fake1; }}; '
        f'write_hostname sda /dev/null LAB1-05 || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False
    assert result["code"] == "umount_failed"


def test_an_unreadable_control_set_fails_instead_of_defaulting_to_001(tmp_path):
    """‏#505, עיקרון 5: קריאת Select\\Current נכשלת (יציאה לא-אפסית, פלט
    ריק). המכונה האמיתית על ControlSet002 (LKG אחרי אתחול כושל), אבל
    הקוד הישן נפל בשקט ל-001, כתב לשם, קרא בחזרה **מ-001** — כך שהאימות
    אישר את ההנחה של עצמו — ודיווח ok:true. ה-umount כאן מצליח, כדי
    שמקור הכישלון היחיד יהיה הסט־בקרה שלא נקרא ולא הבלבול עם #כשל ניתוק."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    hive = run / "win" / "Windows" / "System32" / "config"
    hive.mkdir(parents=True)
    (hive / "SYSTEM").write_text("hive", encoding="utf-8")
    # קריאת Select\Current נכשלת (exit 3, stdout ריק — "value not found"
    # של hivewrite.c). כל read-back אחר מחזיר את השם, כך שאם הקוד היה
    # בוחר סט־בקרה כלשהו, האימות "היה עובר" — וזה בדיוק הבאג.
    hivewrite_body = (
        'if [ "$1" = "-g" ]; then\n'
        '  case "$*" in *Current*) exit 3;; *) echo LAB1-05; exit 0;; esac\n'
        'fi\nexit 0')
    stubs = (_stub(stub_dir / "ntfs-3g", "exit 0")
             + _stub(stub_dir / "hivewrite", hivewrite_body)
             + _stub(stub_dir / "umount", "exit 0"))
    plan = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|4096|1|p1|y|false|u"
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; '
        f'manifest_plan() {{ printf \'%s\\n\' {plan!r}; }}; '
        f'partition_node() {{ echo /dev/fake1; }}; '
        f'write_hostname sda /dev/null LAB1-05 || true'
    )
    result = json.loads(out.strip().splitlines()[-1])
    assert result["ok"] is False, "כישלון קריאת Select\\Current נבלע ודווח כהצלחה"
    assert result["code"] == "no_controlset"


def test_capture_derives_the_os_from_the_roles():
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    win = sh(lib + '_image_os \'{"role":"esp"},{"role":"windows"}\'').strip()
    lin = sh(lib + '_image_os \'{"role":"esp"},{"role":"linux"},{"role":"swap"}\'').strip()
    assert (win, lin) == ("windows", "linux")


def test_a_linux_root_that_is_last_is_expandable():
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    parts = '{"role":"esp","expandable":false},{"role":"linux","expandable":false}'
    out = sh(lib + f"_mark_expandable '{parts}'")
    assert out.count('"expandable":true') == 1
    assert '"role":"linux","expandable":true' in out


def test_a_root_with_swap_behind_it_is_still_expandable():
    """הפריסה שמתקין דביאן מייצר — ‏ESP · root · swap. ה-swap מתועדת
    במניפסט ולא משודרת, ולכן השחזור מעביר אותה לזנב ומרחיב את מה
    שלפניה: המועמד הוא המחיצה האחרונה שאינה swap (#46). זה גם בדיוק
    הכלל שהקליטה בשרת אוכפת."""
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    parts = ('{"role":"esp","expandable":false},'
             '{"role":"linux","expandable":false},'
             '{"role":"swap","expandable":false}')
    out = sh(lib + f"_mark_expandable '{parts}'")
    assert out.count('"expandable":true') == 1
    assert '"role":"linux","expandable":true' in out
    assert '"role":"swap","expandable":false' in out


#: הפריסה הרגילה של Windows 11, כפי שהיא בשני האימג'ים שבספרייה (#58):
#: ‏esp · msr · windows · recovery, וה-recovery אחרונה על הדיסק.
WINDOWS_PARTS = (
    '{"index":1,"role":"esp","start_sector":2048,"expandable":false},'
    '{"index":2,"role":"msr","start_sector":616448,"expandable":false},'
    '{"index":3,"role":"windows","start_sector":649216,"expandable":false},'
    '{"index":4,"role":"recovery","start_sector":535267328,"expandable":false}'
)

#: אימג' הענן של דביאן שבספרייה. ‏`sgdisk -p` מונה לפי אינדקסים, ולכן
#: השורש — מחיצה **1** — הוא ה*ראשון* ברשימה ובכל זאת האחרון על הדיסק.
#: האחרונה ברשימה היא 16, שתפקידה `data`: ולכן לא סומן דבר.
CLOUD_LINUX_PARTS = (
    '{"index":1,"role":"linux","start_sector":2099200,"expandable":false},'
    '{"index":14,"role":"data","start_sector":2048,"expandable":false},'
    '{"index":15,"role":"esp","start_sector":10240,"expandable":false},'
    '{"index":16,"role":"data","start_sector":227328,"expandable":false}'
)


def test_the_candidate_is_the_last_one_on_the_disk_not_in_the_list():
    """‏#58, הבאג הראשון: הסימון קרא את סדר המערך ולא את הסדר על הדיסק.
    באימג' הענן השורש הוא מחיצה 1, האחרון ברשימה הוא 16 (`data`) — ולכן
    לא סומן דבר, ושחזור 256→500 נגמר בשקט עם 244GB לא מוקצים."""
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    out = sh(lib + f"_mark_expandable '{CLOUD_LINUX_PARTS}'")
    assert out.count('"expandable":true') == 1
    assert '"index":1,"role":"linux","start_sector":2099200,"expandable":true' in out


def test_a_recovery_partition_behind_the_windows_one_no_longer_blocks_it():
    """‏#58, הבאג השני — והיפוך מכוון של השומר הקודם. ‏recovery אחרונה
    היא ברירת המחדל של Windows 11, והשומר שנועד למנוע את *ניפוחה* מנע
    כל הרחבה של Windows בכלל. עכשיו היא עוברת לזנב בשחזור ונכתבת שם
    מקובץ הזרם שלה, בדיוק כמו ה-swap שלפניה (#46)."""
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    out = sh(lib + f"_mark_expandable '{WINDOWS_PARTS}'")
    assert out.count('"expandable":true') == 1
    assert '"role":"windows","start_sector":649216,"expandable":true' in out
    assert '"role":"recovery","start_sector":535267328,"expandable":false' in out


def test_an_image_without_a_system_partition_is_never_marked():
    """המועמד הוא תמיד windows/linux. בלי אחת כזו — אין הרחבה, ו-recovery
    או data לא ינופחו במקומה (עיקרון 1: מצב לא ברור נגמר בלי נגיעה)."""
    lib = f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
    parts = ('{"role":"esp","start_sector":2048,"expandable":false},'
             '{"role":"recovery","start_sector":616448,"expandable":false}')
    out = sh(lib + f"_mark_expandable '{parts}'")
    assert '"expandable":true' not in out


# --- ההרחבה מעל swap נגררת (#46) ---------------------------------------------

#: index|type_guid|role|fs|start|size_bytes|file|sha256|expandable|unique_guid
DEBIAN_PLAN = [
    "1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|2048|104857600"
    "|p1.esp.pcl.zst|aa|false|11111111-1111-1111-1111-111111111111",
    "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|206848|102400000000"
    "|p2.linux.pcl.zst|bb|true|22222222-2222-2222-2222-222222222222",
    "3|0657FD6D-A4AB-43C4-84E5-0933C84B4F4F|swap|swap|200206848|8589934592"
    "|null|null|false|33333333-3333-3333-3333-333333333333",
]
#: הסקטור שאחרי ה-root, ועוד ה-swap: מעבר לזה מתחיל הרווח האמיתי.
ROOT_END = 206848 + 102400000000 // 512
SWAP_SECTORS = 8589934592 // 512

#: הפריסה של Windows 11 מהספרייה (#58) — כאן כתוכנית שחזור מלאה.
#: ה-recovery אינה swap: היא נכתבת מקובץ הזרם שלה, במקומה החדש.
WINDOWS_PLAN = [
    "1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|2048|314572800"
    "|p1.esp.pcl.zst|aa|false|11111111-1111-1111-1111-111111111111",
    "2|E3C9E316-0B5C-4DB8-817D-F92DF00215AE|msr|unknown|616448|16777216"
    "|p2.msr.pcl.zst|bb|false|22222222-2222-2222-2222-222222222222",
    "3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|649216|273724473344"
    "|p3.windows.pcl.zst|cc|true|33333333-3333-3333-3333-333333333333",
    "4|DE94BBA4-06D1-4D40-A16A-BFD50179D6AC|recovery|ntfs|535267328|838860800"
    "|p4.recovery.pcl.zst|dd|false|44444444-4444-4444-4444-444444444444",
]
WINDOWS_END = 649216 + 273724473344 // 512
RECOVERY_SECTORS = 838860800 // 512

#: אימג' הענן של דביאן מהספרייה: המועמד הוא מחיצה 1, ראשונה ברשימה
#: ואחרונה על הדיסק — ואין אחריה כלום.
CLOUD_PLAN = [
    "1|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|2099200|45000687616"
    "|p1.linux.pcl.zst|dd|true|dddddddd-dddd-dddd-dddd-dddddddddddd",
    "14|21686148-6449-6E6F-744E-656564454649|data|unknown|2048|4194304"
    "|p14.data.pcl.zst|aa|false|aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "15|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|10240|111149056"
    "|p15.esp.pcl.zst|bb|false|bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    "16|0FC63DAF-8483-4772-8E79-3D69D8477DE4|data|ext4|227328|958398464"
    "|p16.data.pcl.zst|cc|false|cccccccc-cccc-cccc-cccc-cccccccccccc",
]


def test_dev(tmp_path) -> str:
    """‏`$DEVROOT` של הטסט — תיקייה בתוך tmp_path, לעולם לא ‏`/dev` של המארח
    (ראה test_tests_never_touch_host_dev.py)."""
    return f"{posix(tmp_path / 'box' / 'run')}/dev"


def run_expand(tmp_path, plan, disk_sectors, override=None):
    """מריץ expand_last מול sgdisk מזויף ומחזיר (rc, שורות הפקודה, סימון).

    ‏manifest_plan נדרס אחרי ה-source כדי לא לדרוש jq בסביבת הבדיקה —
    אותו דפוס שבו נבדק _ssh_spawn.

    ‏`override` = ``EXPAND_OVERRIDE`` (#59) — הבחירה מהקונסולה, שמגיעה
    ל-`_expand_candidate` בדיוק כפי ש-`imagectl-agent` מציב אותה לפני
    שהוא קורא ל-`run_restore`/`run_restore_drawers`. ‏`None` (ברירת
    המחדל) משאיר אותו **בלתי מוצב** — זה בדיוק סוכן ישן, או `pulls.py`/
    `station.py` שלא עודכנו: אותה ריצה בדיוק כמו לפני #59."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text(f"{disk_sectors}\n")
    run = box / "run"
    run.mkdir()
    plan_file = box / "plan"
    plan_file.write_text("\n".join(plan) + "\n")
    calls = box / "calls"
    # ‏chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה, וזיוף שלא ניתן להרצה
    # עובר בווינדוס (שם כל קובץ "בר-הרצה") ונופל ב-CI בלבד.
    stubs = (
        f"cat > {posix(stub_dir)}/sgdisk <<'STUB'\n"
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {posix(calls)}\n'
        "exit 0\n"
        "STUB\n"
        f"cat > {posix(stub_dir)}/blockdev <<'STUB'\n#!/bin/sh\nexit 0\nSTUB\n"
        f"cat > {posix(stub_dir)}/sleep <<'STUB'\n#!/bin/sh\nexit 0\nSTUB\n"
        f"chmod 0755 {posix(stub_dir)}/sgdisk {posix(stub_dir)}/blockdev "
        f"{posix(stub_dir)}/sleep\n"
    )
    override_export = f'export EXPAND_OVERRIDE={override!r}; ' if override is not None else ""
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export SYSROOT={posix(box)!r} RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "log")!r}; ' + override_export
        + f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; '
        f'. {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/expand.sh; . {posix(AGENT)}/lib/grow.sh; '
        f'manifest_plan() {{ cat {posix(plan_file)!r}; }}; '
        # הבדיקות האלה על *הגיאומטריה* של הטבלה, לא על הראיה שהיא הגיעה
        # לדיסק — זו נבדקת בפני עצמה ב-test_restore_evidence.py, ושם גם
        # מוודאים שהיא באמת חוסמת. כאן היא מוחלפת כי אין דרך ליצור התקן
        # בלוקים בלי root, וטסט מדולג הוא ירוק בלי ראיה (#52).
        "node_is_block() { true; }; "
        # אותו דבר לקריאה החוזרת של GUID המחיצות (#1212): היא נבדקת, גם
        # אחרי הרחבה, ב-test_partition_guid.py מול sgdisk שזוכר את `-u`.
        "verify_unique_guids() { true; }; "
        f'expand_last sda /dev/null >/dev/null 2>&1; echo "rc=$?"'
    )
    marker = run / "targets" / "sda" / "expanded"
    return (
        out.strip(),
        calls.read_text().splitlines() if calls.exists() else [],
        marker.read_text().strip() if marker.exists() else None,
    )


def test_a_trailing_swap_is_rebuilt_at_the_tail_and_the_root_takes_the_rest(tmp_path):
    """‏256→500 עם swap אחרונה: ה-swap נמחקת, נבראת מחדש בזנב בגודל
    שהמניפסט מצהיר, והשורש נמתח עד תחילתה (#46)."""
    rc, calls, marker = run_expand(tmp_path, DEBIAN_PLAN, disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls[0] == f"-d 3 {test_dev(tmp_path)}/sda"
    # ‏-<סקטורים> = כך וכך סקטורים לפני סוף השטח הפנוי; ‏0 = סופו. ערך
    # שלילי ולא סקטור מפורש כדי לא לעקוף את יישור ה-2048 של sgdisk.
    assert calls[1] == (
        f"-n 3:-{SWAP_SECTORS}:0 -t 3:0657FD6D-A4AB-43C4-84E5-0933C84B4F4F "
        f"-u 3:33333333-3333-3333-3333-333333333333 {test_dev(tmp_path)}/sda"
    )
    assert calls[2] == (
        "-d 2 -n 2:206848:0 -t 2:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 2:22222222-2222-2222-2222-222222222222 {test_dev(tmp_path)}/sda"
    )
    assert len(calls) == 3
    assert marker == "2|ext4"


def test_expansion_preserves_attributes_on_the_candidate_and_moved_tail(tmp_path):
    """#1130: both partition recreations used to omit sgdisk -A."""
    attrs = "8000000000000001"
    plan = [line + f"||{attrs}" for line in DEBIAN_PLAN]
    rc, calls, _marker = run_expand(tmp_path, plan, disk_sectors=976773168)
    assert rc == "rc=0"
    assert any(f"-A 2:=:0x{attrs}" in call for call in calls), calls
    assert any(f"-A 3:=:0x{attrs}" in call for call in calls), calls


def test_the_room_check_counts_the_swap_that_comes_back(tmp_path):
    """הזנב גדול ב-9GiB מהטבלה, אבל 8GiB מתוכו חוזרים ל-swap — מתחת
    לסף הרווח האמיתי אין נגיעה בטבלה בכלל."""
    disk = ROOT_END + SWAP_SECTORS + 2097152      # בדיוק על הסף, לא מעליו
    rc, calls, marker = run_expand(tmp_path, DEBIAN_PLAN, disk_sectors=disk)
    assert rc == "rc=0"
    assert calls == []
    assert marker is None


def test_expansion_without_swap_leaves_the_old_path_alone(tmp_path):
    plan = [DEBIAN_PLAN[0], DEBIAN_PLAN[1]]
    rc, calls, marker = run_expand(tmp_path, plan, disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls == [
        "-d 2 -n 2:206848:0 -t 2:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 2:22222222-2222-2222-2222-222222222222 {test_dev(tmp_path)}/sda"
    ]
    assert marker == "2|ext4"


def unmarked(plan):
    """אותה תוכנית, בלי שום `expandable: true` — כלומר מניפסט שנקלט
    לפני #58. שלושת האימג'ים שבספרייה נראים בדיוק כך."""
    return [line.replace("|true|", "|false|") for line in plan]


def test_an_unmarked_debian_manifest_is_expanded_all_the_same(tmp_path):
    """‏#58 אחרי הרחבת הדרישה: הבחירה נגזרת מהמניפסט בכל שחזור ולא
    נקראת מהסימון. הסימון נעשה בקליטה בלבד, ולכן "אף אחד לא סימן"
    נראה בדיוק כמו "אל תרחיב" — ואת שני המצבים האלה אסור לקפל לאחד
    (עיקרון 5). התוצאה זהה לחלוטין לתוכנית המסומנת."""
    rc, calls, marker = run_expand(tmp_path, unmarked(DEBIAN_PLAN),
                                   disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls[0] == f"-d 3 {test_dev(tmp_path)}/sda"
    assert calls[2] == (
        "-d 2 -n 2:206848:0 -t 2:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 2:22222222-2222-2222-2222-222222222222 {test_dev(tmp_path)}/sda"
    )
    assert marker == "2|ext4"


def test_an_unmarked_windows_manifest_picks_the_system_partition(tmp_path):
    """האימג'ים שבספרייה: ‏esp · msr · windows · recovery, כולן `false`.
    ה-windows נבחרת מעצמה וה-recovery עוברת לזנב — בלי קליטה מחדש."""
    rc, calls, marker = run_expand(tmp_path, unmarked(WINDOWS_PLAN),
                                   disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls[0] == f"-d 4 {test_dev(tmp_path)}/sda"
    assert calls[2].startswith("-d 3 -n 3:649216:0 ")
    assert marker == "3|ntfs"


def test_an_unmarked_cloud_manifest_picks_the_root_that_is_first_in_the_list(tmp_path):
    """ואותו דבר על אימג' הענן: מחיצה 1 היא הראשונה ברשימה והאחרונה על
    הדיסק, ולכן היא הנבחרת — בלי סימון, ובלי להסתכל על סדר הרשימה."""
    rc, calls, marker = run_expand(tmp_path, unmarked(CLOUD_PLAN),
                                   disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls == [
        "-d 1 -n 1:2099200:0 -t 1:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 1:dddddddd-dddd-dddd-dddd-dddddddddddd {test_dev(tmp_path)}/sda"
    ]
    assert marker == "1|ext4"


#: דו-אתחול: שורש Linux ואחריו Windows. הבחירה האוטומטית הייתה לוקחת
#: את ה-Windows (האחרון פיזית), אבל המניפסט מסמן במפורש את הלינוקס.
DUAL_BOOT_PLAN = [
    "1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|2048|314572800"
    "|p1.esp.pcl.zst|aa|false|11111111-1111-1111-1111-111111111111",
    "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|ext4|616448|53687091200"
    "|p2.linux.pcl.zst|bb|true|22222222-2222-2222-2222-222222222222",
    "3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|105501248|53687091200"
    "|p3.windows.pcl.zst|cc|false|33333333-3333-3333-3333-333333333333",
]


def test_a_manifest_that_marks_exactly_one_partition_overrides_the_choice(tmp_path):
    """הסימון לא בוטל — הוא הפך מתנאי לעקיפה. מניפסט שמסמן בדיוק מחיצה
    אחת מקבל אותה, גם כשהבחירה האוטומטית הייתה בוחרת אחרת. זו השליטה
    הידנית, וזו גם התאימות לאחור."""
    rc, calls, marker = run_expand(tmp_path, DUAL_BOOT_PLAN,
                                   disk_sectors=976773168)
    assert rc == "rc=0"
    assert marker == "2|ext4", "הסימון המפורש לא גבר על הבחירה האוטומטית"
    assert calls[0] == f"-d 3 {test_dev(tmp_path)}/sda"
    assert calls[2] == (
        "-d 2 -n 2:616448:0 -t 2:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 2:22222222-2222-2222-2222-222222222222 {test_dev(tmp_path)}/sda"
    )


def test_an_image_with_no_system_partition_is_left_alone(tmp_path):
    """עיקרון 1: אין מחיצת windows/linux — אין מועמד, אין הרחבה, ואין
    שגיאה. ‏`recovery` לעולם אינה מועמדת, כי היא אינה windows/linux."""
    plan = unmarked([WINDOWS_PLAN[0], WINDOWS_PLAN[3]])
    rc, calls, marker = run_expand(tmp_path, plan, disk_sectors=976773168)
    assert (rc, calls, marker) == ("rc=0", [], None)


# --- #59: בחירת ההרחבה מהקונסולה, דרך EXPAND_OVERRIDE -------------------------


def test_an_old_agent_that_never_sets_the_override_behaves_exactly_as_before(tmp_path):
    """סוכן ישן שלא מכיר את #59 כלל, ולכן אף פעם לא מציב
    `EXPAND_OVERRIDE` — בדיוק כל הטסטים שמעל השורה הזו. הבקרה השלילית
    האמיתית של השורה הזו כבר קיימת: הם היו כאן לפני #59 והם עדיין
    ירוקים בלעדיו."""
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=976773168)
    assert rc == "rc=0"
    assert marker == "3|ntfs"
    assert calls[0] == f"-d 4 {test_dev(tmp_path)}/sda"


def test_an_explicit_auto_override_matches_the_default(tmp_path):
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=976773168,
                                   override="auto")
    assert rc == "rc=0"
    assert marker == "3|ntfs"
    assert calls[0] == f"-d 4 {test_dev(tmp_path)}/sda"


def test_none_disables_expansion_even_on_a_huge_target(tmp_path):
    """כיבוי מהקונסולה גובר על כל מרחב פנוי — אין מועמד, אין קריאה
    ל-sgdisk בכלל, בדיוק כמו אימג' בלי מחיצת מערכת."""
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=976773168,
                                   override="none")
    assert (rc, calls, marker) == ("rc=0", [], None)


def test_a_manual_override_picks_a_different_partition_than_auto_would(tmp_path):
    """דו-אתחול, בלי שום סימון במניפסט: הבחירה האוטומטית הייתה לוקחת
    את ה-Windows (האחרון פיזית) — הבחירה הידנית מהקונסולה בוחרת את
    הלינוקס במקום, בדיוק כמו הסימון הידני הישן שהטסט שמעליו בודק,
    אבל בלי לגעת במניפסט עצמו."""
    rc, calls, marker = run_expand(tmp_path, unmarked(DUAL_BOOT_PLAN),
                                   disk_sectors=976773168, override="2")
    assert rc == "rc=0"
    assert marker == "2|ext4"
    assert calls[0] == f"-d 3 {test_dev(tmp_path)}/sda"
    assert calls[2] == (
        "-d 2 -n 2:616448:0 -t 2:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 2:22222222-2222-2222-2222-222222222222 {test_dev(tmp_path)}/sda"
    )


def test_an_override_for_a_partition_not_in_this_manifest_falls_back_to_auto(tmp_path):
    """אימג' שהוחלף מתחת לרגליים בין הבחירה בקונסולה לפתיחת הסבב —
    האינדקס שנבחר לא קיים כאן. לא נכשל ולא מתעלם משקט: חוזר לבחירה
    האוטומטית, בדיוק כאילו לא נשלחה בחירה בכלל."""
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=976773168,
                                   override="9")
    assert rc == "rc=0"
    assert marker == "3|ntfs"
    assert calls[0] == f"-d 4 {test_dev(tmp_path)}/sda"


def test_a_trailing_recovery_moves_to_the_tail_and_windows_takes_the_rest(tmp_path):
    """‏#58 על הפריסה השכיחה ביותר: ה-recovery נמחקת, נבראת מחדש בזנב
    **באותו אינדקס ובאותו גודל**, ומחיצת המערכת נמתחת עד תחילתה. אותה
    צורה בדיוק כמו ה-swap של #46 — רק שאת ה-recovery יכתוב הזרם."""
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls == [
        f"-d 4 {test_dev(tmp_path)}/sda",
        f"-n 4:-{RECOVERY_SECTORS}:0 -t 4:DE94BBA4-06D1-4D40-A16A-BFD50179D6AC "
        f"-u 4:44444444-4444-4444-4444-444444444444 {test_dev(tmp_path)}/sda",
        "-d 3 -n 3:649216:0 -t 3:EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 "
        f"-u 3:33333333-3333-3333-3333-333333333333 {test_dev(tmp_path)}/sda",
    ]
    assert marker == "3|ntfs"


#: זנב של שתי מחיצות שסדר האינדקסים שלהן הפוך לסדר על הדיסק: ה-swap
#: (אינדקס 5) יושבת *לפני* ה-recovery (אינדקס 4). המניפסט מונה לפי
#: אינדקסים, ולכן מי שמסתמך על סדר הרשימה יחליף ביניהן בשקט.
CROSSED_TAIL_PLAN = WINDOWS_PLAN[:3] + [
    "4|DE94BBA4-06D1-4D40-A16A-BFD50179D6AC|recovery|ntfs|543655936|838860800"
    "|p4.recovery.pcl.zst|dd|false|44444444-4444-4444-4444-444444444444",
    "5|0657FD6D-A4AB-43C4-84E5-0933C84B4F4F|swap|swap|535267328|4294967296"
    "|null|null|false|55555555-5555-5555-5555-555555555555",
]


def test_the_tail_keeps_its_order_on_the_disk_not_its_order_in_the_list(tmp_path):
    """הזנב נבנה מהסוף פנימה, ולכן הסדר שנקבע כאן הוא הסדר שיישאר על
    הפלטה. הוא נגזר מ-`start_sector` ולא מסדר הרשימה: כאן ה-swap היא
    אינדקס 5 ויושבת *לפני* ה-recovery שהיא אינדקס 4. החלפה ביניהן היא
    בדיוק סוג הכשל השקט שאין לו סימן — הטבלה נראית תקינה, וה-swap
    פשוט נחתה במקום שבו אמורה הייתה להיות מחיצת השחזור."""
    rc, calls, marker = run_expand(tmp_path, CROSSED_TAIL_PLAN,
                                   disk_sectors=976773168)
    assert rc == "rc=0"
    # שתי המחיקות בסדר כלשהו, ואז בנייה מהסוף פנימה: ה-recovery — האחרונה
    # על הדיסק — נבראת ראשונה, ורק אחריה ה-swap שלפניה.
    assert sorted(calls[:2]) == [f"-d 4 {test_dev(tmp_path)}/sda", f"-d 5 {test_dev(tmp_path)}/sda"]
    assert calls[2] == (
        f"-n 4:-{RECOVERY_SECTORS}:0 -t 4:DE94BBA4-06D1-4D40-A16A-BFD50179D6AC "
        f"-u 4:44444444-4444-4444-4444-444444444444 {test_dev(tmp_path)}/sda"
    )
    assert calls[3] == (
        f"-n 5:-{4294967296 // 512}:0 -t 5:0657FD6D-A4AB-43C4-84E5-0933C84B4F4F "
        f"-u 5:55555555-5555-5555-5555-555555555555 {test_dev(tmp_path)}/sda"
    )
    assert marker == "3|ntfs"


def test_the_room_check_counts_every_partition_that_comes_back(tmp_path):
    """הסף נמדד על מה שבאמת יתווסף — הזנב פחות **כל** מה שיחזור אליו,
    לא רק ה-swap. כאן ה-recovery בולעת בדיוק את העודף, ולכן אין נגיעה."""
    disk = WINDOWS_END + RECOVERY_SECTORS + 2097152
    rc, calls, marker = run_expand(tmp_path, WINDOWS_PLAN, disk_sectors=disk)
    assert (rc, calls, marker) == ("rc=0", [], None)


def test_the_cloud_root_is_stretched_although_it_is_first_in_the_list(tmp_path):
    """מחיצה 1 היא המועמד, ואין אחריה כלום — המחיצות 14/15/16 יושבות
    *לפניה* על הדיסק ואסור להן לנדוד לזנב רק בגלל סדר הרשימה."""
    rc, calls, marker = run_expand(tmp_path, CLOUD_PLAN, disk_sectors=976773168)
    assert rc == "rc=0"
    assert calls == [
        "-d 1 -n 1:2099200:0 -t 1:0FC63DAF-8483-4772-8E79-3D69D8477DE4 "
        f"-u 1:dddddddd-dddd-dddd-dddd-dddddddddddd {test_dev(tmp_path)}/sda"
    ]
    assert marker == "1|ext4"


def test_the_filesystem_grows_only_for_the_partition_that_was_widened(tmp_path):
    """שלב שני של ההרחבה, אחרי שהנתונים הגיעו. בלי סימון — אין מה להגדיל."""
    run = tmp_path / "run"
    run.mkdir()
    lib = (
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/expand.sh; . {posix(AGENT)}/lib/grow.sh; '
        f'grow_filesystem() {{ echo "grow:$1:$2"; }}; '
    )
    assert sh(lib + 'grow_expanded sda; echo "rc=$?"').strip() == "rc=0"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "expanded").write_text("2|ext4\n")
    out = sh(lib + "grow_expanded sda").splitlines()
    assert out == [f"grow:ext4:{posix(run)}/dev/sda2"]


def test_the_table_is_widened_before_the_data_arrives():
    """סדר שאין עליו ויכוח: ה-swap עוברת לזנב לפני השחזור, ולכן ה-mkswap
    של הלולאה נוחת על מקומה הסופי — פעם אחת. הגדלת מערכת הקבצים היא
    השלב היחיד שחייב לחכות לנתונים."""
    source = (AGENT / "lib" / "restore.sh").read_text(encoding="utf-8")
    run = source[source.index("run_restore() {"):]
    grow = "finish_grow" if "finish_grow" in run else "grow_expanded"
    assert run.index("expand_last") < run.index("restore_partition") \
        < run.index(grow)
    assert len(re.findall(r"^\s*mkswap ", source, flags=re.M)) == 1


def test_the_stream_lands_on_the_node_of_the_index_not_on_a_start_sector(tmp_path):
    """החוליה שבזכותה מחיצה נגררת שאינה swap אפשרית בכלל (#58).

    ‏expand_last בורא את ה-recovery מחדש בזנב *באותו אינדקס*, והזרם כותב
    לפי אינדקס בלבד: הצינור של מחיצה 4 נפתח על `/dev/sda4` — שהיא כבר
    המחיצה שהוזזה. ‏`start_sector` שבמניפסט אינו מגיע לכאן כלל, ולכן אין
    מה להעתיק ואין מה לתקן אחרי הזרם. הבדיקה מריצה את הצינור האמיתי
    ולוכדת את מה ש-partclone קיבל."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    (target / "state").write_text("writing\n")
    (target / "base").write_text("0\n")
    (target / "bytes.raw").write_text("")
    calls = box / "partclone.calls"
    payload = b"recovery-bytes"
    digest = hashlib.sha256(payload).hexdigest()
    # ‏chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה, וזיוף שלא ניתן להרצה
    # עובר בווינדוס (שם כל קובץ "בר-הרצה") ונופל ב-CI בלבד.
    stubs = (
        f"cat > {posix(stub_dir)}/pv <<'STUB'\n"
        "#!/bin/sh\necho 0 >&2\ncat\necho 14 >&2\n"
        "STUB\n"
        f"cat > {posix(stub_dir)}/zstd <<'STUB'\n#!/bin/sh\nexec cat\nSTUB\n"
        f"cat > {posix(stub_dir)}/partclone.ntfs <<'STUB'\n"
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {posix(calls)}\n'
        "cat > /dev/null\n"
        "STUB\n"
        f"chmod 0755 {posix(stub_dir)}/pv {posix(stub_dir)}/zstd "
        f"{posix(stub_dir)}/partclone.ntfs\n"
    )
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "log")!r} WAIT_POLL_S=1; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; '
        f'. {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; '
        "stream_source() { printf 'recovery-bytes'; }; "
        # ‏/dev/sda4 אינו קיים בסביבת הבדיקה, ובדיקת ההתקן היא הבדיקה
        # הראשונה בצינור (#51). היא מוחלפת כאן ונבדקת בנפרד.
        "node_is_block() { true; }; "
        f"restore_partition unicast http://s img sda 4 ntfs p4.recovery.pcl.zst "
        f'{digest} "" > {posix(box)}/pipe.out 2>&1; echo "rc=$?"'
    )
    assert out.strip() == "rc=0", (box / "pipe.out").read_text(encoding="utf-8")
    assert calls.exists(), "partclone לא הורץ בכלל"
    assert calls.read_text().splitlines() == [
        f"-r -s - -O {posix(run)}/dev/sda4 -L {posix(run)}/targets/sda/partclone.log"
    ]


# --- ה-swap חוזרת עם ה-UUID שלה, בשני המסלולים (#48, #49) -------------------

SWAP_UUID = "9f2c1a44-6b1e-4d55-8e73-0c5a1b2d3e4f"


def swap_box(tmp_path, disks=("sda",)):
    """קופסה עם mkswap מזויף שמתעד כל קריאה, ומגירות במצב writing.

    ‏mkswap נכשל על התקן שרשום ב-box/fail — ככה נבדק שכשל במגירה אחת
    לא גורר את השאר. שאר הכלים (fanout, udp-receiver, partclone) *לא*
    מזויפים בכוונה: אם מסלול ה-swap ינסה בכל זאת להזרים, זה ייפול כאן."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    for dev in disks:
        target = run / "targets" / dev
        target.mkdir(parents=True)
        (target / "state").write_text("writing\n")
        (target / "base").write_text("0\n")
        (target / "bytes.raw").write_text("")
    calls = box / "mkswap.calls"
    # ‏chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה, וזיוף שלא ניתן להרצה
    # עובר בווינדוס (שם כל קובץ "בר-הרצה") ונופל ב-CI בלבד.
    prelude = (
        f"cat > {posix(stub_dir)}/mkswap <<'STUB'\n"
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {posix(calls)}\n'
        "for _a; do :; done\n"
        f'[ -f "{posix(box)}/fail" ] && grep -qx "$_a" "{posix(box)}/fail" && exit 1\n'
        "exit 0\n"
        "STUB\n"
        f"chmod 0755 {posix(stub_dir)}/mkswap\n"
        f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; '
        f'. {posix(AGENT)}/lib/progress.sh; '
        f'. {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/drawers.sh; . {posix(AGENT)}/lib/verdict.sh; . {posix(AGENT)}/lib/failmark.sh; '
        "log() { :; }; "
        # ‏/dev/sdaN אינו קיים כאן, ומסלול ה-swap עובר באותה בדיקת התקן
        # של כל כתיבה (#51). היא נבדקת בנפרד ב-test_restore_evidence.py.
        "node_is_block() { true; }; "
    )
    return box, run, calls, prelude


def mkswap_calls(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def state_of(run: Path, dev: str) -> str:
    return (run / "targets" / dev / "state").read_text().strip()


def test_a_swap_with_a_uuid_in_the_manifest_gets_it_back(tmp_path):
    """‏#48: ‏/etc/fstab של מתקין דביאן מפנה לשורת ה-swap ב-`UUID=`. בלי
    ‏-U כל שחזור חותם UUID חדש, ו-swapon --show חוזר ריק במערכת שעלתה."""
    _box, _run, calls, prelude = swap_box(tmp_path)
    out = sh(prelude + "restore_partition multicast http://s img sda 3 swap "
             f'null null "{SWAP_UUID}"; echo "rc=$?"')
    assert out.strip() == "rc=0"
    assert mkswap_calls(calls) == [f"-U {SWAP_UUID} {test_dev(tmp_path)}/sda3"]


@pytest.mark.parametrize("uuid", ["", "null"])
def test_an_old_manifest_without_a_uuid_falls_back_to_a_plain_mkswap(tmp_path, uuid):
    """נפילה אחורה (עיקרון 1): מניפסט שנקלט לפני #48 אין בו שדה, ו-jq
    מחזיר מחרוזת ריקה. זה לא כשל — זו בדיוק ההתנהגות הקודמת."""
    _box, _run, calls, prelude = swap_box(tmp_path)
    out = sh(prelude + "restore_partition multicast http://s img sda 3 swap "
             f'null null "{uuid}"; echo "rc=$?"')
    assert out.strip() == "rc=0"
    assert mkswap_calls(calls) == [f"{test_dev(tmp_path)}/sda3"]


def test_every_drawer_gets_exactly_one_mkswap_and_nothing_is_streamed(tmp_path):
    """‏#49: רשומת swap במניפסט הוזנה עד היום ל-fanout כמו כל מחיצה, עם
    ‏file=null — זרם ריק ואי-התאמת sha256. עכשיו כל מגירה בוראת אותה
    בעצמה, פעם אחת בדיוק, ואף fifo לא נפתח."""
    _box, run, calls, prelude = swap_box(tmp_path, disks=("sda", "sdb"))
    out = sh(prelude + "restore_partition_drawers multicast http://s img 3 swap "
             f'null null "{SWAP_UUID}" sda sdb; echo "rc=$?"')
    assert out.strip() == "rc=0"
    assert mkswap_calls(calls) == [
        f"-U {SWAP_UUID} {test_dev(tmp_path)}/sda3",
        f"-U {SWAP_UUID} {test_dev(tmp_path)}/sdb3",
    ]
    assert not (run / "targets" / "sda" / "feed").exists()
    assert [state_of(run, d) for d in ("sda", "sdb")] == ["writing", "writing"]


def test_a_drawer_that_cannot_make_swap_does_not_stop_the_others(tmp_path):
    """תרחיש QA: כשל במגירה אחת לא עוצר את השאר — גם על ה-swap."""
    box, run, calls, prelude = swap_box(tmp_path, disks=("sda", "sdb"))
    (box / "fail").write_text(f"{test_dev(tmp_path)}/sda3\n", newline="\n")
    out = sh(prelude + "restore_partition_drawers multicast http://s img 3 swap "
             f'null null "{SWAP_UUID}" sda sdb; echo "rc=$?"')
    assert out.strip() == "rc=0"
    assert state_of(run, "sda") == "failed"
    assert state_of(run, "sdb") == "writing"
    assert len(mkswap_calls(calls)) == 2


def test_the_drawers_settle_the_table_before_the_swap_is_made():
    """‏#49 מעל #46: ההרחבה — שמזיזה swap נגררת לזנב — רצה לפני לולאת
    המחיצות, ולכן ה-mkswap של החדר נוחת על המיקום הסופי. ‏mkswap עצמו
    אינו מופיע כאן בכלל: הוא עובר דרך make_swap של restore.sh, שהיא
    הכתיבה היחידה למחיצה."""
    source = (AGENT / "lib" / "drawers.sh").read_text(encoding="utf-8")
    room = source[source.index("run_restore_drawers() {"):]
    assert room.index("expand_last") < room.index("restore_partition_drawers")
    assert re.findall(r"^\s*mkswap ", source, flags=re.M) == []
    assert "make_swap " in source


def test_the_capture_reads_the_filesystem_uuid_not_the_gpt_guid(tmp_path):
    """ה-UUID שמעניין את fstab הוא של מערכת הקבצים (‏blkid -s UUID), לא
    ה-`unique_guid` של רשומת ה-GPT — שני מספרים שונים לגמרי."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    calls = tmp_path / "blkid.calls"
    out = sh(
        f"cat > {posix(stub_dir)}/blkid <<'STUB'\n"
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {posix(calls)}\n'
        f"echo {SWAP_UUID}\n"
        "STUB\n"
        f"chmod 0755 {posix(stub_dir)}/blkid\n"
        f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/manifest.sh; _uuid_of /dev/sda3'
    )
    assert out.strip() == SWAP_UUID
    assert calls.read_text().strip() == "-o value -s UUID /dev/sda3"


def test_the_agent_writes_the_name_after_a_restore():
    """הקישור עצמו: אחרי שחזור מוצלח נקראת כתיבת השם, לפני האתחול."""
    source = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert "name_this_machine" in source
    restore_at = source.index('run_restore "multicast"')
    naming_at = source.index("name_this_machine \"$_disk\"")
    reboot_at = source.index("sync; reboot -f", restore_at)
    assert restore_at < naming_at < reboot_at


def test_every_lib_file_is_loaded_and_packed():
    """קובץ lib חדש שאיש לא טוען הוא פונקציה חסרה **בשקט** על מכונה
    אמיתית — בדיוק הצורה של #84, שם עץ מודולים שלא נארז הפיל
    ‏used_bytes ואת שם המחשב בלי מילה. שני השערים נבדקים כאן: הבנאי
    אורז את `lib/*.sh` בגלוב (ולכן קובץ חדש נתפס מאליו), אבל הטעינה
    ב-`imagectl-agent` היא **רשימה מפורשת** — ורשימה מפורשת שוכחת."""
    agent_src = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    missing = [p.name for p in sorted((AGENT / "lib").glob("*.sh"))
               if f'. "$LIB_DIR/{p.name}"' not in agent_src]
    assert missing == [], f"קבצי lib שאינם נטענים ב-imagectl-agent: {missing}"

    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert '"$AGENT_DIR"/lib/*.sh' in builder, \
        "הבנאי אינו אורז את agent/lib/*.sh — כל הסוכן חסר ב-initramfs"


def test_arm_wol_is_callable_after_sourcing_common_in_fresh_shell(tmp_path):
    """The monitor's fresh ``sh -c`` gets a self-contained arm_wol function from common.sh."""
    sysroot = tmp_path / "root"
    (sysroot / "sys" / "class" / "net").mkdir(parents=True)
    out = sh(
        f'export SYSROOT={posix(sysroot)!r} RUN_DIR={posix(tmp_path)!r}; '
        f'. {posix(AGENT)}/lib/common.sh; command -V arm_wol; '
        'arm_wol || printf "%s\\n" expected-no-interface')
    assert "arm_wol is a function" in out
    assert out.rstrip().endswith("expected-no-interface")


def test_only_common_touches_proc_cmdline():
    offenders = [
        p.name for p in SH_FILES
        if "/proc/cmdline" in p.read_text(encoding="utf-8")
        and p.name != "common.sh"
    ]
    assert offenders == []


# --- הבנאי חייב לארוז כל בינארי שהסוכן קורא לו ------------------------------


def builder_binaries() -> set[str]:
    text = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    match = re.search(r"BINARIES=\((.*?)\)", text, flags=re.DOTALL)
    assert match, "רשימת BINARIES לא נמצאה בבנאי"
    return set(match.group(1).split())

# פקודות חיצוניות שמופיעות בסקריפטי הסוכן ואינן applets של busybox.
REAL_BINARIES_USED = {
    "curl", "jq", "zstd", "pv", "sgdisk", "blockdev", "sha256sum", "od",
    "hdparm", "ntfsresize", "ntfs-3g", "ntfs-3g.probe", "ntfsfix", "umount",
    "blkid", "df", "mount", "stty", "udp-receiver",
    # ‏#715 — מחשב הבנייה משדר מהדיסק שלו ישירות למחשבי השיכפול
    # (‏directsend.sh); אותה חבילה (udpcast) כמו udp-receiver.
    "udp-sender",
    "e2fsck", "resize2fs", "btrfs",
    # #667 — XFS grow on RHEL/Fedora golden images (mounted, xfs_growfs).
    "xfs_growfs",
    "partclone.ntfs", "partclone.fat", "partclone.ext4", "partclone.btrfs",
    "partclone.dd",
    # ‏SSH לטכנאי (#44) — נארז תמיד, מאזין רק מאחורי imagectl.debug=1.
    "dropbear", "dropbearkey",
    # ‏#85 — קריאת החותם של מטעני האתחול שעל ה-ESP בזמן הקליטה. הוא גם
    # אחד מארבעת הכלים החסרים שרשומים ב-#86 (הכשרת מכונה בזמן שחזור).
    "openssl",
    # ‏#652 — שער בריאות SMART על דיסק היעד לפני כתיבה (smart.sh).
    "smartctl",
    # ‏#649 — ארגז הכלים: הכלי המובנה "זיהוי המחשב" (tools.sh).
    "dmidecode",
    # ‏#433 — רשומת Boot#### לווינדוס המשוחזר על UEFI (bootentry.sh).
    "efibootmgr",
}

#: מקומפל מהמקור בבנאי ולכן אינו ברשימת ה-BINARIES שנאספת מהמערכת.
COMPILED_BINARIES = {"fanout", "hivewrite", "lldpsniff"}


def test_builder_packs_every_binary_the_agent_uses():
    packed = builder_binaries()
    missing = REAL_BINARIES_USED - packed
    assert not missing, f"הסוכן קורא לבינארים שהבנאי לא אורז: {missing}"


def test_the_used_list_is_not_stale():
    """הכיוון ההפוך: כל פקודה ברשימה באמת מופיעה בקוד הסוכן."""
    source = "\n".join(p.read_text(encoding="utf-8") for p in SH_FILES)
    stale = {cmd for cmd in REAL_BINARIES_USED if cmd not in source}
    assert not stale, f"פקודות שכבר לא בשימוש: {stale}"


def test_the_builder_compiles_what_it_cannot_install():
    """fanout אינו חבילה — בלי הקימפול, מצב חדר השיכפולים לא רץ כלל."""
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    source = "\n".join(p.read_text(encoding="utf-8") for p in SH_FILES)
    for name in COMPILED_BINARIES:
        assert f"{name}.c" in builder, f"{name} לא מקומפל בבנאי"
        assert name in source, f"{name} מקומפל אבל לא בשימוש"


def test_the_builder_packs_gconv_for_hivex():
    """libhivex ממיר שמות מפתחות עם iconv של glibc; בלי מודולי gconv
    ב-initramfs כל חיפוש מפתח נכשל בשקט — כך שם המחשב לא נכתב חודשים
    (‏#33). הבדיקה מצמידה את האריזה לבנאי."""
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    for needed in ("gconv-modules", "ISO8859-1.so", "UTF-16.so"):
        assert needed in builder, f"הבנאי לא אורז את {needed}"


def _fanout_buffer_for(tmp_path, mem_available_kb, drawers):
    root = tmp_path / "membox"
    (root / "proc").mkdir(parents=True, exist_ok=True)
    (root / "proc" / "meminfo").write_text(
        f"MemTotal: {mem_available_kb + 100000} kB\n"
        f"MemAvailable: {mem_available_kb} kB\n"
    )
    out = sh(
        f"export SYSROOT={posix(root)!r}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/drawers.sh; . {posix(AGENT)}/lib/verdict.sh; . {posix(AGENT)}/lib/failmark.sh; "
        f"_fanout_buffer {drawers}"
    )
    return int(out.strip())


def test_fanout_buffer_shrinks_to_the_memory_that_exists(tmp_path):
    """מכונת 512MB עם 3 מגירות: 3×256MB היה OOM באמצע הזרם (#21) —
    החוצץ נגזר מהזמין, פחות רזרבה לצנרת."""
    got = _fanout_buffer_for(tmp_path, mem_available_kb=400_000, drawers=3)
    assert got == (400_000 - 131_072) * 1024 // 3
    assert got < 268435456


def test_fanout_buffer_caps_at_the_default(tmp_path):
    got = _fanout_buffer_for(tmp_path, mem_available_kb=16_000_000, drawers=3)
    assert got == 268435456


def test_fanout_buffer_never_shrinks_below_read_chunk(tmp_path):
    """מאגר קטן מ-READ_CHUNK נכשל בשקט (הלקח מ-#12) — יש רצפה."""
    got = _fanout_buffer_for(tmp_path, mem_available_kb=100_000, drawers=3)
    assert got == 2097152


# --- מה עושים כשמשימה נגמרה (#500) ------------------------------------------
#
# ברירת המחדל היא כיבוי, ובכוונה: מגירות מוחלפות במכונה כבויה (נספח
# א׳). אבל במעבדה מרוחקת מכונה שכבתה היא מכונה שאיש אינו יכול להדליק,
# ואיתה נעלמת היכולת להמשיך. המתג נכתב בזמן הבנייה ואינו מגיע משורת
# הפקודה של הקרנל — שם מותר רק `imagectl.server` ו-`imagectl.mode`.


def _finish_and_stop(tmp_path, after_task, role=None):
    """מריץ את ההכרעה עם poweroff/reboot מזויפים, ומחזיר מי נקרא.

    הזיוף הוא **פונקציות מעטפת** ולא קבצים ב-PATH: על ווינדוס `chmod`
    אינו מסמן ביצוע, וסטאב שאינו נמצא נראה בדיוק כמו סטאב שלא נקרא.

    ‏`role` מזריק את D_ROLE — המשתנה שהסוכן קובע מתשובת ה-hello —
    כדי לבדוק את הברירה תלוית-התפקיד בלי override מפורש."""
    box = tmp_path / "box"
    (box / "etc" / "imagectl").mkdir(parents=True, exist_ok=True)
    if after_task is not None:
        (box / "etc" / "imagectl" / "after-task").write_text(
            after_task + "\n", encoding="utf-8", newline="\n")
    out = tmp_path / "called"
    body = (REPO / "agent" / "lib" / "common.sh").read_text(encoding="utf-8")
    assert "finish_and_stop() {" in body, "הפונקציה נעלמה מ-common.sh"
    role_line = f'export D_ROLE={role}; ' if role is not None else ''
    script = (
        'BOX=' + repr(posix(box)) + '; OUT=' + repr(posix(out)) + '; '
        'poweroff() { echo poweroff >> "$OUT"; }; '
        'reboot()   { echo reboot   >> "$OUT"; }; '
        'sync()     { :; }; log() { :; }; '
        + role_line +
        'export AFTER_TASK_FILE="$BOX/etc/imagectl/after-task"; '
        # הפונקציה האמיתית, לא עותק שלה
        '. ' + posix(AGENT) + '/lib/common.sh; '
        # These tests isolate after-task selection.  A separate shim suite
        # exercises real arm_wol success and every failure state.
        'arm_wol() { :; }; finish_and_stop')
    subprocess.run([BASH, "-c", script], capture_output=True,
                   cwd=str(REPO), stdin=subprocess.DEVNULL)
    return out.read_text(encoding="utf-8").split() if out.exists() else []


def test_the_default_is_still_powering_off(tmp_path):
    """בלי override ובלי role ידוע — הברירה הבטוחה היא כיבוי."""
    assert "poweroff" in _finish_and_stop(tmp_path, None)


def test_reboot_is_chosen_only_when_the_file_says_so(tmp_path):
    """‏`reboot` הוא בחירה מפורשת בזמן בנייה, לא ברירת מחדל."""
    called = _finish_and_stop(tmp_path, "reboot")
    assert "reboot" in called and "poweroff" not in called


def test_an_unknown_value_falls_back_to_powering_off(tmp_path):
    """ערך שאיננו מכירים אינו סיבה לנחש — נופלים לברירה תלוית-התפקיד,
    וברירת המחדל הבטוחה בלי role היא כיבוי."""
    assert "poweroff" in _finish_and_stop(tmp_path, "maybe")


def test_classroom_reboots_by_default(tmp_path):
    """תחנת כיתה: בלי override נגזר reboot — אדם ממתין לידה."""
    called = _finish_and_stop(tmp_path, None, role="classroom")
    assert "reboot" in called and "poweroff" not in called


def test_cloner_and_build_power_off_by_default(tmp_path):
    """בנייה/שיכפול: בלי override נגזר poweroff — המגירה מוחלפת כבויה."""
    for role in ("cloner", "build"):
        called = _finish_and_stop(tmp_path, None, role=role)
        assert "poweroff" in called and "reboot" not in called, role


def test_explicit_override_beats_the_role(tmp_path):
    """ערך מפורש גובר על הברירה תלוית-התפקיד — זה מה שכלי המעבדה כותב:
    שיכפול עם `reboot` מפורש מאתחל, וכיתה עם `poweroff` מפורש מתכבה."""
    reboot_called = _finish_and_stop(tmp_path, "reboot", role="cloner")
    assert "reboot" in reboot_called and "poweroff" not in reboot_called
    assert "poweroff" in _finish_and_stop(tmp_path, "poweroff", role="classroom")


# ‏#690: גיון התפקיד של monitor.sh — מי מפעיל את ה-RFB, ומי בלי --input.


def _monitor_spawn_args(tmp_path, role=None, flag="1", ip="10.0.0.9",
                        secret=True):
    """מריץ monitor_start עם _monitor_spawn מזויף ומחזיר את הארגומנטים
    שנמסרו ל-imagectl-monitor, או None אם המוניטור לא הופעל בכלל.

    ‏_monitor_spawn מוגדר-מחדש **אחרי** טעינת monitor.sh (הטעינה מגדירה
    את האמיתי). ‏MONITOR_BIN=/bin/sh רק כדי לעבור את בדיקת `[ -x ]`; הוא
    לעולם לא מורץ, כי ה-spawn מזויף. ‏#839: סוד האתחול מונח ב-RUN_DIR
    כמו ש-build_hello משאיר אותו; ‏secret=False מדמה hello שלא הגריל.
    ‏FB_DEV=/dev/null — התקן תווים בכל מקום — אומר "יש מסך"; המסלול בלי
    מסך (#835) נבדק ב-test_headless_framebuffer.py."""
    out = tmp_path / "spawn-args"
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    if secret:
        (run / "monitor.secret").write_text("00112233445566778899aabbccddeeff\n")
    role_line = f'export D_ROLE={role}; ' if role is not None else ''
    script = (
        'OUT=' + repr(posix(out)) + '; '
        f'export IMAGECTL_MONITOR={flag}; '
        'export IMAGECTL_SERVER=http://10.0.0.1:8080; '
        'export RUN_DIR=' + repr(posix(run)) + '; '
        'export IP=' + ip + '; export MONITOR_BIN=/bin/sh; export FB_DEV=/dev/null; '
        'log() { :; }; '
        + role_line +
        '. ' + posix(AGENT) + '/lib/monitor.sh; '
        '_monitor_spawn() { echo "$@" > "$OUT"; _monitor_pid=4242; }; '
        'monitor_start')
    subprocess.run([BASH, "-c", script], capture_output=True,
                   cwd=str(REPO), stdin=subprocess.DEVNULL)
    return out.read_text(encoding="utf-8").strip() if out.exists() else None


def test_monitor_runs_view_only_on_a_cloner(tmp_path):
    """קלונר: המוניטור עולה אך **בלי** --input — צפייה בלבד, בלי uinput.
    זה מה שמונע שליטה במחשב שיכפול (אין מקלדת/עכבר על קלונר)."""
    args = _monitor_spawn_args(tmp_path, role="cloner")
    assert args is not None, "המוניטור לא עלה על קלונר"
    assert "--input" not in args.split(), args
    assert "--bind" in args and "10.0.0.9" in args


def test_monitor_enables_input_on_the_build_machine(tmp_path):
    """מחשב בנייה: --input מועבר — שליטה מלאה (uinput)."""
    args = _monitor_spawn_args(tmp_path, role="build")
    assert args is not None and "--input" in args.split(), args


def test_monitor_never_starts_on_a_classroom_machine(tmp_path):
    """כיתה: המוניטור **לא עולה בכלל**, גם כשהדגל דלוק — 5900 לא נפתח על
    מחשב תלמיד. זה השומר של 'כיתה בלי 5900' בצד הסוכן: השרת מסרב לפרוקסי
    וה-FW הוא השכבה התפעולית, אך הסוכן כלל אינו פותח את הפורט מלכתחילה."""
    assert _monitor_spawn_args(tmp_path, role="classroom", flag="1") is None


def test_monitor_stays_off_without_the_boot_flag(tmp_path):
    """בלי imagectl.monitor=1 (IMAGECTL_MONITOR!=1) — לא עולה, בכל תפקיד."""
    assert _monitor_spawn_args(tmp_path, role="build", flag="0") is None


def test_monitor_gets_the_boot_secret_file(tmp_path):
    """‏#839: המוניטור מקבל את קובץ הסוד — בלעדיו אין לו במה לאמת את
    הפרוקסי, ו-5900 היה נפתח לכל הווילן."""
    args = _monitor_spawn_args(tmp_path, role="cloner").split()
    assert "--secret-file" in args, args
    path = args[args.index("--secret-file") + 1]
    assert path.endswith("/run/monitor.secret"), path


def test_monitor_gets_allow_from_the_server_ip(tmp_path):
    """‏#1077: peer-check — רק IP השרת מ-imagectl.server. בלי זה 5900
    היה פתוח לכל הווילן גם עם HMAC."""
    args = _monitor_spawn_args(tmp_path, role="cloner").split()
    assert "--allow-from" in args, args
    assert args[args.index("--allow-from") + 1] == "10.0.0.1"


def test_monitor_does_not_start_without_a_boot_secret(tmp_path):
    """הגרלה שנכשלה = אין קובץ = אין מוניטור. סגור-בכישלון: עדיף מכונה
    בלי מוניטור ממוניטור בלי אימות (עיקרון 5 — אי-ידיעה אינה 'תקין')."""
    assert _monitor_spawn_args(tmp_path, role="build", secret=False) is None


def test_monitor_does_not_start_without_a_server_ip(tmp_path):
    """בלי imagectl.server אין למי להגביל את 5900 — לא עולים."""
    out = tmp_path / "spawn-args"
    run = tmp_path / "run"
    run.mkdir()
    (run / "monitor.secret").write_text("00112233445566778899aabbccddeeff\n")
    script = (
        'OUT=' + repr(posix(out)) + '; '
        'export IMAGECTL_MONITOR=1 IMAGECTL_SERVER=; '
        'export RUN_DIR=' + repr(posix(run)) + '; '
        'export D_ROLE=build IP=10.0.0.9 MONITOR_BIN=/bin/sh FB_DEV=/dev/null; '
        'log() { :; }; '
        '. ' + posix(AGENT) + '/lib/monitor.sh; '
        '_monitor_spawn() { echo "$@" > "$OUT"; _monitor_pid=4242; }; '
        'monitor_start')
    subprocess.run([BASH, "-c", script], capture_output=True,
                   cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert not out.exists()


def test_the_build_refuses_an_after_task_it_does_not_know():
    """‏`--after-task nonsense` נכשל בבנייה, לא מול מכונה שלא עשתה דבר."""
    src = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "--after-task must be auto, poweroff or reboot" in src


# --- used_bytes: "לא נמדד" נבדל מ-"נמדד וריק" (#298, עיקרון 5) --------------


def _used_bytes(tmp_path, mount_rc, used_kib=None):
    """מריץ את `_used_bytes` מול mount/df/umount מזויפים ומחזיר את stdout.

    ‏mount_rc = קוד היציאה של mount (שני הניסיונות, עם ובלי `-t`). כש-
    ‏used_kib נתון, ‏df מצליח ומחזיר את העמודה Used בערך הזה (ב-1024-בלוקים);
    ‏None = ‏df אינו רלוונטי (העגינה נכשלה לפני שהגיעו אליו)."""
    box = tmp_path / "box"
    stub_dir = box / "stubs"
    stub_dir.mkdir(parents=True)
    run = box / "run"
    run.mkdir(parents=True)
    stubs = _stub(stub_dir / "mount", f"exit {mount_rc}")
    stubs += _stub(stub_dir / "umount", "exit 0")
    if used_kib is not None:
        # פורמט `df -kP`: שורת כותרת ואז שורת נתונים; ‎$3 הוא Used ב-1024-בלוקים.
        stubs += _stub(
            stub_dir / "df",
            'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
            f'echo "/dev/fake 1000000 {used_kib} 1 1% /mnt"')
    out = sh(
        stubs
        + f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/manifest.sh; '
        f'_used_bytes /dev/fake ext4')
    return out.strip()


def test_a_partition_that_will_not_mount_is_null_not_zero(tmp_path):
    """עיקרון 5: העגינה נכשלה — לא הצלחנו למדוד. זה `null`, לא `0`. ‏`0`
    היה נראה במניפסט זהה למחיצה ריקה שנמדדה, ושני המצבים שונים (#298)."""
    assert _used_bytes(tmp_path, mount_rc=1) == "null"


def test_a_measured_empty_partition_is_zero_not_null(tmp_path):
    """הצד השני של אותו מטבע: העגינה הצליחה ו-`df` מדד 0 תפוס — זו מדידה
    אמיתית של מחיצה ריקה, ולכן `0`. ‏null שמור למי שלא נמדד."""
    assert _used_bytes(tmp_path, mount_rc=0, used_kib=0) == "0"


def test_a_measured_partition_reports_the_bytes_it_measured(tmp_path):
    """מדידה שהצליחה עוברת כמות שהיא — Used בקילו-בלוקים כפול 1024."""
    assert _used_bytes(tmp_path, mount_rc=0, used_kib=1024) == str(1024 * 1024)
