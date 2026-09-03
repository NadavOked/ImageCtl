"""סדר טעינת המודולים ב-initramfs — דרייבר PHY לפני דרייבר MAC.

‏`r8169` שעושה probe לפני ש-`realtek.ko` נרשם נכשל ב-EADDRNOTAVAIL,
והקרנל אינו מנסה שוב: המודול נשאר טעון וההתקן נשאר בלי דרייבר. מחשב
Lenovo עם RTL8168 לא קיבל ממשק רשת בכלל בגלל זה, ורק במעבדת חומרה
(‏2026-08-29, ‏#76) זה נראה — ב-VM הכרטיס אינו זקוק ל-PHY נפרד.

הבדיקה מריצה את הקטע האמיתי מתוך `tools/build_initramfs.sh` על עץ
מודולים מזויף, ולא בודקת את הטקסט שלו: ניסוח אחר שישבור את הסדר
צריך להיכשל כאן.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

BUILDER = Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh"
BASH = shutil.which("bash")
KVER = "9.9.9-test"

#: הקטע מתוך הבנאי רץ ב-sh אמיתי; בלעדיו אין כאן בדיקה, ובמקום שבו
#: הוא אמור להיות זו תקלה ולא סיבה לדלג (#52).
pytestmark = requires_native("sh", why="הקטע מהבנאי רץ ב-sh")


def module_list_snippet() -> str:
    """הקטע שמייצר את /etc/imagectl/modules, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("_phy_mods=$(find"))
    end = next(i for i, l in enumerate(lines) if l.startswith("} | awk "))
    return "\n".join(lines[start:end + 1])


def generate(tmp_path: Path, phy: list[str], ethernet: list[str]) -> list[str]:
    root = tmp_path / "root"
    for sub, names in (("phy", phy), ("ethernet", ethernet)):
        d = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "net" / sub
        d.mkdir(parents=True, exist_ok=True)
        for name in names:
            (d / f"{name}.ko.xz").write_bytes(b"")
    (root / "etc" / "imagectl").mkdir(parents=True, exist_ok=True)

    script = f'ROOT={root.as_posix()!r}\nKVER={KVER!r}\nWITH_GUI=0\n' + module_list_snippet()
    subprocess.run(["sh", "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)
    out = root / "etc" / "imagectl" / "modules"
    return out.read_text(encoding="utf-8").split()


def test_phy_drivers_load_before_the_mac_drivers_that_need_them(tmp_path: Path):
    """‏realtek לפני r8169 — למרות ש-r8169 קודם לו אלפביתית."""
    mods = generate(tmp_path, phy=["realtek", "marvell"],
                    ethernet=["r8169", "e1000e", "igc"])
    assert mods.index("realtek") < mods.index("r8169")
    assert mods.index("marvell") < mods.index("r8169")


def test_every_phy_driver_precedes_every_ethernet_driver(tmp_path: Path):
    """הכלל הוא קבוצתי, לא רשימת מקרים פרטיים."""
    phy = ["realtek", "broadcom", "micrel", "aquantia"]
    ethernet = ["r8169", "atlantic", "bnx2x", "e1000", "tg3"]
    mods = generate(tmp_path, phy=phy, ethernet=ethernet)
    last_phy = max(mods.index(m) for m in phy)
    first_mac = min(mods.index(m) for m in ethernet)
    assert last_phy < first_mac


def test_storage_still_comes_first_and_there_are_no_duplicates(tmp_path: Path):
    """‏sort -u ביטל בשקט גם את הכוונה של "Storage first"; היא חזרה."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169", "e1000e"])
    assert mods.index("ahci") < mods.index("realtek")
    assert mods.index("nvme") < mods.index("r8169")
    assert len(mods) == len(set(mods))


def test_a_phy_module_listed_in_both_trees_is_not_duplicated(tmp_path: Path):
    """הסינון שומר על הסדר — ולכן חייב גם להסיר כפילויות."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["realtek", "r8169"])
    assert mods.count("realtek") == 1
    assert mods.index("realtek") < mods.index("r8169")

def test_the_usb_controller_loads_before_anything_that_hangs_off_it(tmp_path: Path):
    """‏usbhid מצהיר depends: usbcore,hid — ובלי הבקר אין USB בכלל (#77).

    ‏#43 הוסיף את `hid` ונעצר שם, ולכן מקלדת USB לא עבדה על חומרה:
    האשף הוצג ואף הקשה לא הגיעה. ‏PS/2 הסתירה את זה — `i8042` built-in.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for controller in ("usbcore", "xhci_hcd", "ehci_hcd", "uhci_hcd"):
        assert controller in mods, f"{controller} is not loaded at all"
        assert mods.index(controller) < mods.index("usbhid")
    # ‏usbhid תלוי גם ב-hid עצמו.
    assert mods.index("hid") < mods.index("usbhid")
    # ומה שמתחבר ל-USB בא אחרי הבקר.
    assert mods.index("usbcore") < mods.index("usb-storage")


def test_the_builder_packs_the_usb_core_and_host_trees(tmp_path: Path):
    """רשימת הטעינה חסרת ערך אם המודולים עצמם אינם נארזים."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "kernel/drivers/usb/core" in text
    assert "kernel/drivers/usb/host" in text


def closure_snippet() -> str:
    """הקטע שסוגר את גרף התלויות, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("_closure_round=0"))
    end = next(i for i, l in enumerate(lines)
               if l.startswith('echo "module dependency closure:'))
    return "\n".join(lines[start:end + 1])


def run_closure(tmp_path: Path, src: Path, root: Path):
    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\n" + closure_snippet())
    return subprocess.run(["sh", "-c", script], check=True,
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=90)


def test_a_module_pulls_in_a_dependency_from_a_directory_nobody_listed(tmp_path: Path):
    """‏usbcore תלוי ב-usb-common, שיושב בתיקייה שלישית שאיש לא רשם.

    זה מה שהשאיר את המקלדת בלי חשמל אחרי שכבר נוספו usb/core ו-usb/host
    בידיים (‏#77): הנורה לא נדלקה, כי בלי usb-common גם usbcore לא נטען.
    רשימת תיקיות ידנית תמיד תפספס תלות אחת עמוק יותר — ולכן הגרף נסגר
    מ-modules.dep ולא מניחוש.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for sub in ("core", "common", "host"):
        (src / "kernel" / "drivers" / "usb" / sub).mkdir(parents=True)
    (src / "kernel/drivers/usb/core/usbcore.ko.xz").write_bytes(b"")
    (src / "kernel/drivers/usb/common/usb-common.ko.xz").write_bytes(b"")
    (src / "kernel/drivers/usb/host/xhci-hcd.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text(
        "kernel/drivers/usb/core/usbcore.ko.xz:"
        " kernel/drivers/usb/common/usb-common.ko.xz\n"
        "kernel/drivers/usb/host/xhci-hcd.ko.xz:"
        " kernel/drivers/usb/core/usbcore.ko.xz"
        " kernel/drivers/usb/common/usb-common.ko.xz\n",
        encoding="utf-8")

    # רק ה-host הועתק, בדיוק כמו רשימת תיקיות שפספסה את השאר.
    dst = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "usb" / "host"
    dst.mkdir(parents=True)
    (dst / "xhci-hcd.ko.xz").write_bytes(b"")

    run_closure(tmp_path, src, root)

    packed = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "usb"
    assert (packed / "core" / "usbcore.ko.xz").is_file(), "usbcore was not pulled in"
    assert (packed / "common" / "usb-common.ko.xz").is_file(), (
        "usb-common was not pulled in -- the dependency that left the keyboard dark")


def test_the_closure_terminates_when_nothing_is_missing(tmp_path: Path):
    """סגירה שלא עוצרת היא בנייה תקועה — גרוע מאימג' חסר."""
    src, root = tmp_path / "src", tmp_path / "root"
    (src / "kernel" / "drivers" / "net").mkdir(parents=True)
    (src / "kernel/drivers/net/lonely.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text("kernel/drivers/net/lonely.ko.xz:\n",
                                     encoding="utf-8")
    dst = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "net"
    dst.mkdir(parents=True)
    (dst / "lonely.ko.xz").write_bytes(b"")

    done = run_closure(tmp_path, src, root)
    assert b"after 1 rounds" in done.stdout


# --- מערכות קבצים: העץ שלא נארז, וארבעה תסמינים שנראו לא קשורים (#84) --------


def copy_snippet() -> str:
    """הקטע שמעתיק את עצי המודולים, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("MODULE_SUBDIRS=("))
    copy = next(i for i, l in enumerate(lines) if 'cp -a "$MODSRC/$sub/."' in l)
    end = next(i for i, l in enumerate(lines[copy:], copy) if l == "done")
    return "\n".join(lines[start:end + 1])


def run_copy(tmp_path: Path, trees: dict[str, list[str]]) -> Path:
    """מריץ את לולאת ההעתקה האמיתית על עץ מודולים מזויף.

    ‏bash ולא sh: ‏MODULE_SUBDIRS הוא מערך, ו-dash אינו מכיר מערכים.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for rel, names in trees.items():
        directory = src / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / f"{name}.ko.xz").write_bytes(b"")
    (root / "lib" / "modules" / KVER).mkdir(parents=True)

    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\nWITH_GUI=0\n" + copy_snippet())
    subprocess.run([BASH, "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=90)
    return root / "lib" / "modules" / KVER


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_the_builder_packs_the_filesystem_trees(tmp_path: Path):
    """בלעדיהם ה-initramfs יכול לעגן NTFS דרך FUSE ותו לא.

    זה נראה כמו שלושה באגים נפרדים: שם המחשב בלינוקס לא נכתב כי
    ‏`mount -t ext4` נכשל (#62), ‏`used_bytes` היה 0 בכל מניפסט כי
    המדידה מודדת אחרי מאונט, והרחבת btrfs הייתה נכשלת ברגע שיהיה
    אימג' כזה. עץ אחד שלא נארז.
    """
    packed = run_copy(tmp_path, {
        "kernel/fs/fat": ["fat", "vfat", "msdos"],
        "kernel/fs/nls": ["nls_cp437", "nls_ascii"],
        "kernel/fs/ext4": ["ext4"],
        "kernel/fs/btrfs": ["btrfs"],
        "kernel/fs/efivarfs": ["efivarfs"],
        "kernel/fs/ceph": ["ceph"],          # לא נדרש — ולא אמור להיארז
        "kernel/drivers/net/ethernet": ["e1000e"],
    })
    for rel in ("fat/vfat", "ext4/ext4", "btrfs/btrfs", "efivarfs/efivarfs"):
        assert (packed / "kernel/fs" / f"{rel}.ko.xz").is_file(), f"{rel} לא נארז"


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_the_whole_nls_tree_is_packed_and_not_a_guessed_subset(tmp_path: Path):
    """‏vfat דורש את קידוד ברירת המחדל של הקרנל — כאן `cp437` **וגם**
    `ascii` (‏CONFIG_FAT_DEFAULT_CODEPAGE=437, ‏IOCHARSET="ascii").
    בחירת תת-קבוצה היא בדיוק הניחוש שנכשל ב-#33, ‏#76 ו-#77."""
    packed = run_copy(tmp_path, {
        "kernel/fs/nls": ["nls_cp437", "nls_ascii", "nls_cp1255", "nls_utf8"],
    })
    for name in ("nls_cp437", "nls_ascii", "nls_cp1255", "nls_utf8"):
        assert (packed / "kernel/fs/nls" / f"{name}.ko.xz").is_file()


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_a_tree_nobody_asked_for_is_left_out(tmp_path: Path):
    """הרשימה היא בחירה, לא `kernel/fs` כולו — אחרת ה-initramfs תופח."""
    packed = run_copy(tmp_path, {
        "kernel/fs/ceph": ["ceph"],
        "kernel/fs/bcachefs": ["bcachefs"],
        "kernel/fs/ext4": ["ext4"],
    })
    assert (packed / "kernel/fs/ext4/ext4.ko.xz").is_file()
    assert not (packed / "kernel/fs/ceph").exists()
    assert not (packed / "kernel/fs/bcachefs").exists()


def test_ext4_pulls_in_the_dependencies_that_sit_outside_its_own_tree(tmp_path: Path):
    """‏mbcache יושב ישירות ב-kernel/fs ולא בתת-תיקייה, ו-crc16 ב-kernel/lib.

    בדיוק הצורה של #77: התיקייה שנרשמה בידיים היא לא התיקייה שבה יושבת
    התלות. הגרף נסגר מ-modules.dep, ולכן אלה לא צריכים להיות ברשימה.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for rel in ("kernel/fs/ext4", "kernel/fs/jbd2", "kernel/lib", "kernel/fs"):
        (src / rel).mkdir(parents=True, exist_ok=True)
    (src / "kernel/fs/ext4/ext4.ko.xz").write_bytes(b"")
    (src / "kernel/fs/jbd2/jbd2.ko.xz").write_bytes(b"")
    (src / "kernel/fs/mbcache.ko.xz").write_bytes(b"")
    (src / "kernel/lib/crc16.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text(
        "kernel/fs/ext4/ext4.ko.xz: kernel/lib/crc16.ko.xz"
        " kernel/fs/mbcache.ko.xz kernel/fs/jbd2/jbd2.ko.xz\n"
        "kernel/fs/jbd2/jbd2.ko.xz:\n"
        "kernel/fs/mbcache.ko.xz:\n"
        "kernel/lib/crc16.ko.xz:\n",
        encoding="utf-8")

    dst = root / "lib" / "modules" / KVER / "kernel" / "fs" / "ext4"
    dst.mkdir(parents=True)
    (dst / "ext4.ko.xz").write_bytes(b"")

    run_closure(tmp_path, src, root)

    packed = root / "lib" / "modules" / KVER
    assert (packed / "kernel/fs/mbcache.ko.xz").is_file(), "mbcache לא נגרר"
    assert (packed / "kernel/fs/jbd2/jbd2.ko.xz").is_file(), "jbd2 לא נגרר"
    assert (packed / "kernel/lib/crc16.ko.xz").is_file(), "crc16 לא נגרר"


def test_the_filesystem_modules_are_loaded_and_not_left_to_autoload(tmp_path: Path):
    """טעינה מפורשת ולא הסתמכות על ‏`fs-ext4` דרך modules.alias.

    השרשרת ההיא (‏depmod → modules.alias → /proc/sys/kernel/modprobe →
    busybox modprobe) נכשלת בשקט בכל חוליה. ברשימה, כישלון נספר
    ומדווח כ-`N modules did not load`.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in ("efivarfs", "fat", "vfat", "nls_cp437", "nls_ascii", "ext4", "btrfs"):
        assert name in mods, f"{name} אינו ברשימת הטעינה"
    # ‏efivarfs נטען לפני שה-init מנסה לעגן אותו — ראו agent/init.
    assert len(mods) == len(set(mods))


# --- כיסוי לפלטפורמות היעד המוצהרות (#78) ------------------------------------

#: איפה כל דרייבר יושב באמת בעץ של דביאן. שניים מהם הם קבצים בודדים
#: ישירות תחת `kernel/drivers/net` — בלי תיקייה משלהם — וזו כל הנקודה:
#: לולאת התיקיות לעולם לא תיגע בהם, בדיוק כמו `mbcache` ב-#84.
PLATFORM_LAYOUT = {
    "hv_netvsc": "kernel/drivers/net/hyperv",
    "hv_storvsc": "kernel/drivers/scsi",
    "vmxnet3": "kernel/drivers/net/vmxnet3",
    "vmw_pvscsi": "kernel/drivers/scsi",
    "virtio_net": "kernel/drivers/net",
    "virtio_blk": "kernel/drivers/block",
    "xen-netfront": "kernel/drivers/net",
    "xen-blkfront": "kernel/drivers/block",
}

#: ומערכות הקבצים, שעד #121 לא היו מוצהרות כלל אלא הגיעו כתוצר לוואי
#: של `MODULE_SUBDIRS` — ולכן נשירה שלהן הסתיימה ב-exit 0.
FS_LAYOUT = {
    "ext4": "kernel/fs/ext4",
    "btrfs": "kernel/fs/btrfs",
    "vfat": "kernel/fs/fat",
    "fat": "kernel/fs/fat",
    "nls_cp437": "kernel/fs/nls",
    "nls_ascii": "kernel/fs/nls",
    "efivarfs": "kernel/fs/efivarfs",
}

LAYOUT = {**PLATFORM_LAYOUT, **FS_LAYOUT}


def declared(array: str) -> list[str]:
    """שמות המודולים במערך מוצהר בסקריפט — מקור האמת הוא הסקריפט."""
    text = BUILDER.read_text(encoding="utf-8")
    body = text.split(f"{array}=(", 1)[1].split(")", 1)[0]
    names = []
    for line in body.split("\n"):
        names += line.split("#", 1)[0].split()
    return names


def required_snippet() -> str:
    """הצהרת הפלטפורמות וההעתקה לפי שם, כפי שהן בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("REQUIRED_MODULES=("))
    exit_at = next(i for i, l in enumerate(lines[start:], start) if l.strip() == "exit 1")
    end = next(i for i, l in enumerate(lines[exit_at:], exit_at) if l == "fi")
    return "\n".join(lines[start:end + 1])


def run_required(tmp_path: Path, present):
    """מריץ את ההעתקה-לפי-שם האמיתית על עץ שבו קיימים רק `present`."""
    src, root = tmp_path / "src", tmp_path / "root"
    for mod in present:
        directory = src / LAYOUT[mod]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{mod}.ko.xz").write_bytes(b"")
    (root / "lib" / "modules" / KVER).mkdir(parents=True)

    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\nWITH_GUI=0\nMODULE_SUBDIRS=()\n"
              + required_snippet())
    done = subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=90)
    return done, root / "lib" / "modules" / KVER


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_every_declared_platform_gets_its_nic_and_its_disk_controller(tmp_path: Path):
    """‏ESXi, ‏KVM ו-Xen — לא רק ה-Hyper-V שעליו נבנה האימג'.

    מכונה על ESXi עלתה בלי כרטיס רשת, ועל KVM ו-Xen גם בלי דיסק.
    ‏`vmw_pvscsi` ו-`virtio_scsi` שרדו רק במקרה, כי הם יושבים תחת
    `scsi` שכן נארזת.
    """
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in PLATFORM_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} לא נארז"


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_a_driver_that_sits_loose_under_drivers_net_is_still_packed(tmp_path: Path):
    """‏`virtio_net` ו-`xen-netfront` אינם בתיקייה משלהם.

    לולאת התיקיות מעתיקה `kernel/drivers/net/<תת-תיקייה>`, והם קבצים
    ישירות תחת `net`. רשימה לפי תיקיות לא יכולה להגיע אליהם בכלל —
    ולכן ההצהרה היא לפי שם.
    """
    done, packed = run_required(tmp_path, ["virtio_net", "xen-netfront"] +
                                [m for m in LAYOUT
                                 if m not in ("virtio_net", "xen-netfront")])
    assert done.returncode == 0, done.stderr
    assert (packed / "kernel/drivers/net/virtio_net.ko.xz").is_file()
    assert (packed / "kernel/drivers/net/xen-netfront.ko.xz").is_file()


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_build_stops_when_a_declared_platform_driver_is_missing(tmp_path: Path):
    """שכחה נתפסת בבנייה ולא מול מכונה — זו כל תוחלת ההצהרה.

    ‏initramfs שנבנה בלי דרייבר של פלטפורמה מוצהרת נראה תקין לחלוטין,
    והכשל מגיע רק כשמחשב אמיתי עולה ומודיע `no DHCP lease on any
    interface` — בלי רמז לאיזה מודול חסר. ככה נראו #76, ‏#77 ו-#84.
    """
    present = [m for m in LAYOUT if m != "vmxnet3"]
    done, _ = run_required(tmp_path, present)
    assert done.returncode != 0, "בנייה חסרה הצליחה — ההצהרה חסרת ערך"
    assert "vmxnet3" in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_missing_report_names_all_of_them_at_once(tmp_path: Path):
    """שלושה חסרים = הודעה אחת, לא שלוש בנייות."""
    absent = {"vmxnet3", "virtio_blk", "xen-netfront"}
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m not in absent])
    assert done.returncode != 0
    for mod in absent:
        assert mod in done.stderr, f"{mod} לא הוזכר: {done.stderr}"


# --- מערכות הקבצים מוצהרות, ולא נוכחות במקרה (#121) ---------------------------


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
@pytest.mark.parametrize("absent", sorted(FS_LAYOUT))
def test_the_build_stops_when_a_declared_filesystem_module_is_missing(
    tmp_path: Path, absent: str
):
    """הבקרה השלילית של #121, מודול-מודול.

    ‏`exfat` ו-`isofs` נשרו מהעץ בין שתי גרסאות והבנייה יצאה 0, כי
    מערכות הקבצים לא היו מוצהרות בשום מקום — הן הגיעו כתוצר לוואי של
    ‏`MODULE_SUBDIRS` וסגירת התלויות. ‏`ext4` שנושר כך הוא #62 ו-#84
    מחדש: שם המחשב לא נכתב, ‏`used_bytes` אפס בכל מניפסט — ושוב, רק
    מול מכונה אמיתית.
    """
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != absent])
    assert done.returncode != 0, f"{absent} נשר והבנייה הצליחה — ההצהרה חסרת ערך"
    assert absent in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
def test_a_declared_filesystem_module_is_packed_even_from_a_tree_nobody_listed(
    tmp_path: Path,
):
    """ההצהרה גם מרפאת: המודול מועתק לפי שם, לא לפי תיקייה.

    ‏`MODULE_SUBDIRS=()` כאן — אף עץ לא הועתק — ובכל זאת כל מודול
    מוצהר חייב להימצא בתוצר. אותה תכונה בדיוק כמו ‏`virtio_net`,
    שיושב ישירות תחת `kernel/drivers/net` ולולאת התיקיות לא נוגעת בו.
    """
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in FS_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} לא נארז"


def test_the_declared_filesystems_are_exactly_the_ones_in_the_load_list(tmp_path: Path):
    """מודול שנארז ולא נטען הוא מודול שלא קיים, ולהפך.

    שתי רשימות באותו קובץ שמתפצלות הן איך ההצהרה נשחקת בשקט — ולכן
    שתיהן נקראות מהסקריפט, ולא נכתבות כאן בידיים.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in declared("REQUIRED_FS_MODULES"):
        assert name in mods, f"{name} מוצהר אבל אינו ברשימת הטעינה"


def test_exfat_and_isofs_stay_out_until_something_actually_asks_for_them(tmp_path: Path):
    """ההכרעה של #121, מוצמדת: אין להם קורא ברפו.

    הסוכן עולה מהרשת ולא ממדיה אופטית, ומחיצת exFAT נשלחת
    ל-`partclone.dd` — שקורא בלוקים ולא מערכת קבצים. אם מישהו יוסיף
    קורא, הטסט הזה הוא המקום שבו ההחלטה נפתחת מחדש ולא מוחמצת.
    """
    assert "exfat" not in declared("REQUIRED_FS_MODULES")
    assert "isofs" not in declared("REQUIRED_FS_MODULES")
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    assert "exfat" not in mods
    assert "isofs" not in mods


def test_the_paravirtual_disk_controllers_are_in_the_load_list(tmp_path: Path):
    """דרייבר שנארז ולא נטען הוא דרייבר שלא קיים."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in ("vmw_pvscsi", "virtio_scsi", "virtio_blk", "xen-blkfront"):
        assert name in mods, f"{name} אינו ברשימת הטעינה"
    assert len(mods) == len(set(mods))
