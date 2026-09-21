"""‏ISO ההתקנה (#1139): הרשימה האחת, ה-preseed, ומה ש-firstboot אסור לו.

הלקח של R25: ההתקנה מה-ISO רצה **בלי אינטרנט**, ולכן חבילה שנוספה
ל-`PKGS` של המתקין או לרשימות `build_initramfs.sh` ולא ל-`tools/iso/
packages.txt` מתגלה רק מול שרת אמיתי, כשה-pool כבר נצרב. הטסט כאן קורא
את **המקורות עצמם** (לא עותק) ונופל בשני הכיוונים — חבילה שחסרה
ב-packages.txt, וחבילה ב-packages.txt שאין לה מקור.

ועוד שלושה חוזים שאסור שיזוזו בשקט:
* ‏preseed.cfg: mirror כבוי, החבילות מה-ISO (מסלול ה-cdrom), ‏late_command
  מפעיל את late-command.sh, root בלבד, הדיסק נבחר ב-early_command.
* ‏firstboot.sh לעולם אינו מעביר `--deploy-if` (R25 §2.5: זה המוקש —
  ‏dnsmasq שנדלק על כרטיס שנוחש), ובונה עם `--skip-apt`.
* ‏late-command.sh רושם עובדות (installer-nic/installer-role/iso-release)
  ו-firstboot.sh קורא אותן — לא heuristic (R62).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_installer_packages import installer_pkgs

REPO = Path(__file__).resolve().parent.parent
ISO_DIR = REPO / "tools" / "iso"
INITRAMFS = REPO / "tools" / "build_initramfs.sh"
PACKAGES_TXT = ISO_DIR / "packages.txt"
PRESEED = ISO_DIR / "preseed.cfg"
FIRSTBOOT = ISO_DIR / "firstboot.sh"
LATE_COMMAND = ISO_DIR / "late-command.sh"
WIZARD_SERVICE = REPO / "install" / "imagectl-wizard.service"
BUILD_ISO = ISO_DIR / "build-iso.sh"
GRUB_TEMPLATE = ISO_DIR / "grub.cfg.in"
ISOLINUX_MENU_TEMPLATE = ISO_DIR / "isolinux-menu.cfg.in"
ISOLINUX_ENTRY_TEMPLATE = ISO_DIR / "imagectl.cfg.in"
BASH_SCRIPTS = ("make-pool.sh", "build-iso.sh", "firstboot.sh", "test-iso.sh")

#: מה שרק ה-ISO מוסיף — אין לו מקור בקבצים האחרים, ולכן מוצהר כאן.
ISO_ONLY = {"linux-image-amd64", "nftables", "sqlite3"}
#: תיקיית קושחה ש-build_initramfs.sh דורש (#1125) → החבילה שמביאה אותה בדביאן 13.
FIRMWARE_PACKAGE = {"rtl_nic": "firmware-realtek", "i915": "firmware-intel-graphics"}


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.split("\n"))


def packages_txt() -> set[str]:
    return set(_strip_comments(PACKAGES_TXT.read_text(encoding="utf-8")).split())


def _paren_list(text: str, opener: str) -> list[str]:
    """התוכן של `NAME=( ... )` בלי הערות; הערות עלולות להכיל סוגריים."""
    body = text.split(opener, 1)[1]
    names: list[str] = []
    for line in body.split("\n"):
        code = line.split("#", 1)[0]
        done = ")" in code
        names += code.split(")", 1)[0].split()
        if done:
            break
    return names


def _continued_command(text: str, first_line: str) -> list[str]:
    """הטוקנים של פקודה שממשיכה ב-`\\` מהשורה שמכילה `first_line`."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if first_line in l)
    tokens: list[str] = []
    for line in lines[start:]:
        code = line.split("#", 1)[0].rstrip()
        cont = code.endswith("\\")
        tokens += code.rstrip("\\").split()
        if not cont:
            break
    return tokens


def initramfs_lists() -> dict[str, set[str]]:
    """ארבע הרשימות של build_initramfs.sh, מהקובץ עצמו."""
    text = INITRAMFS.read_text(encoding="utf-8")
    base = _continued_command(text, "apt-get install -y --no-install-recommends \\")
    gui_build = _continued_command(text, "apt-get install -y --no-install-recommends make pkg-config")
    tool_pkgs = {
        m.group(1) for m in re.finditer(r"\[[\w.]+\]=([\w.+-]+)", text.split("declare -A TOOL_PKG=(", 1)[1].split(")", 1)[0])
    }
    return {
        "build_initramfs.sh base apt list": set(base[3:]) - {"apt-get", "install", "-y", "--no-install-recommends"},
        "build_initramfs.sh GUI_PACKAGES": set(_paren_list(text, "\nGUI_PACKAGES=(")),
        "build_initramfs.sh GUI build deps": set(gui_build) - {"apt-get", "install", "-y", "--no-install-recommends"},
        "build_initramfs.sh TOOL_PKG": tool_pkgs,
    }


def test_the_sources_were_actually_parsed() -> None:
    """פרסור שהחזיר רשימה ריקה היה הופך את הטסט הבא לירוק על כלום."""
    lists = initramfs_lists()
    for name, pkgs in lists.items():
        assert len(pkgs) >= 5, f"{name}: נקראו {sorted(pkgs)} — הפרסור נשבר"
    assert "busybox-static" in lists["build_initramfs.sh base apt list"]
    assert "fonts-ibm-plex" in lists["build_initramfs.sh GUI_PACKAGES"]
    assert "libpango1.0-dev" in lists["build_initramfs.sh GUI build deps"]
    assert "testdisk" in lists["build_initramfs.sh TOOL_PKG"]
    assert "python3-fastapi" in installer_pkgs()


def test_packages_txt_is_the_union_of_every_apt_list() -> None:
    have = packages_txt()
    sources = {"setup-boot-server.sh PKGS": set(installer_pkgs()), **initramfs_lists()}
    missing = {name: sorted(pkgs - have) for name, pkgs in sources.items() if pkgs - have}
    assert not missing, f"חסר ב-tools/iso/packages.txt (ההתקנה offline תיכשל עליהן): {missing}"


def test_every_package_in_packages_txt_has_a_source() -> None:
    """הכיוון ההפוך: שורה ב-packages.txt שאין לה מקור היא רשימה שנסחפה."""
    known = set().union(*initramfs_lists().values(), installer_pkgs(), ISO_ONLY, FIRMWARE_PACKAGE.values())
    orphans = sorted(packages_txt() - known)
    assert not orphans, f"ב-packages.txt בלי מקור ובלי הצהרה ב-ISO_ONLY: {orphans}"


def test_the_iso_only_packages_are_declared_in_packages_txt() -> None:
    missing = sorted(ISO_ONLY - packages_txt())
    assert not missing, f"חסר ב-packages.txt: {missing}"
    assert "linux-image-cloud-amd64" not in packages_txt(), "קרנל cloud — build_initramfs.sh מסרב לו (#904)"


def firmware_gate_dirs() -> set[str]:
    """התיקיות ש-build_initramfs.sh עוצר בלעדיהן: FIRMWARE_DIRS=(...) והצטרפות i915."""
    text = INITRAMFS.read_text(encoding="utf-8")
    dirs = {name.strip("\"'") for name in _paren_list(text, "\nFIRMWARE_DIRS=(")}
    dirs.update(re.findall(r"FIRMWARE_DIRS\+=\((\w+)\)", text))
    return dirs


def test_firmware_the_initramfs_gate_demands_is_on_the_iso() -> None:
    """‏#1125 הפך תיקיית קושחה חסרה לכישלון בנייה; שרת שהותקן מה-ISO בלי
    ‏firmware-realtek נפל payload-failed בשלב א' של האתחול הראשון (QEMU, 19/09)."""
    dirs = firmware_gate_dirs()
    assert {"rtl_nic", "i915"} <= dirs, dirs
    unknown = sorted(dirs - FIRMWARE_PACKAGE.keys())
    assert not unknown, f"תיקיית קושחה בלי חבילה ידועה ב-FIRMWARE_PACKAGE: {unknown}"
    missing = sorted({FIRMWARE_PACKAGE[d] for d in dirs} - packages_txt())
    assert not missing, f"build_initramfs.sh ידרוש קושחה שה-ISO לא מתקין: {missing}"


def test_no_ssh_server_on_the_product_server() -> None:
    """הכרעת נדב 19/09: אין SSH על השרת האמיתי — כלי מעבדה, מותקן ידנית.
    ‏ISO שמביא sshd הוא ISO שמפר את ההכרעה בשקט."""
    assert "openssh-server" not in packages_txt()
    for name in ("preseed.cfg", "late-command.sh", "firstboot.sh", "imagectl-firstboot.service"):
        code = _strip_comments((ISO_DIR / name).read_text(encoding="utf-8"))
        assert not re.search(r"\bsshd?\b|openssh", code), f"{name} נוגע ב-SSH מחוץ להערה"


# --- preseed -----------------------------------------------------------------

def preseed_entries() -> dict[str, tuple[str, str]]:
    """‏`owner key type value` → key: (type, value); שורות `\\` מאוחדות."""
    text = PRESEED.read_text(encoding="utf-8")
    text = re.sub(r"\\\n", " ", text)
    entries: dict[str, tuple[str, str]] = {}
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 3)
        assert len(parts) >= 3, f"שורת preseed לא תקינה: {raw!r}"
        _owner, key, qtype = parts[:3]
        entries[key] = (qtype, parts[3].strip() if len(parts) == 4 else "")
    return entries


def test_preseed_installs_offline_from_the_iso_itself() -> None:
    p = preseed_entries()
    assert p["apt-setup/use_mirror"] == ("boolean", "false")
    assert p["apt-setup/cdrom/set-first"] == ("boolean", "true"), "החבילות מגיעות דרך apt-cdrom (מסלול כל התקנה מ-CD)"
    assert p["apt-setup/cdrom/set-next"] == ("boolean", "false")
    # שתי השאלות שעצרו התקנה אמיתית ב-QEMU (19/09) — בלעדיהן ה-ISO ממתין לאדם.
    assert p["apt-setup/cdrom/set-double"] == ("boolean", "false")
    assert p["apt-setup/no_mirror"] == ("boolean", "true")
    assert p["apt-setup/services-select"][1] == "", "עדכוני אבטחה מהרשת = בקשה שתיכשל offline"
    assert "mirror/http/hostname" not in p
    assert "apt-setup/local0/repository" not in p, "file:/cdrom בתוך ה-chroot אינו המסלול — ראה preseed.cfg"
    assert "debian-installer/allow_unauthenticated" not in p, "ה-cdrom נחשב מהימן (TrustCDROM); בלי מתג שמכבה אימות"
    assert p["apt-setup/disable-cdrom-entries"] == ("boolean", "true")
    assert p["pkgsel/include"] == ("string", "@PKGSEL_INCLUDE@"), "הרשימה מוזרקת מ-packages.txt בבנייה"
    assert p["clock-setup/ntp"] == ("boolean", "false")


def test_preseed_takes_root_only_and_asks_which_disk_to_erase() -> None:
    """‏#1189 (נדב 21/09): "אמור להיות לי בחירה של הדיסק" — שרת עם שלושה
    כוננים; "הראשון שאינו USB" היה מוחק את הלא-נכון. הרשימה מוצגת, והכתיבה
    מאושרת במפורש גם עם דיסק אחד (ISO שנשכח בכונן)."""
    p = preseed_entries()
    assert p["passwd/root-login"] == ("boolean", "true")
    assert p["passwd/make-user"] == ("boolean", "false")
    assert p["passwd/root-password-crypted"] == ("password", "@ROOT_PASSWORD_HASH@"), "הגיבוב נקבע בבנייה, לא ב-git"
    assert "passwd/root-password" not in p
    assert "partman-auto/disk" not in p, "דיסק קבוע מראש = אין בחירה; המתקין מציג את הרשימה"
    assert "partman/early_command" not in p, "early_command שבוחר דיסק לבד הוסר (#1189)"
    assert p["partman/confirm"] == ("boolean", "false"), "‏\"Write the changes to disks?\" חייב להופיע — המחיקה מאחורי לחיצה"
    assert p["partman-auto/method"] == ("string", "regular")
    assert p["grub-installer/force-efi-extra-removable"] == ("boolean", "true")


def test_preseed_late_command_runs_the_script_on_the_iso() -> None:
    p = preseed_entries()
    assert p["preseed/late_command"][1] == "sh /cdrom/imagectl/late-command.sh"
    late = _strip_comments(LATE_COMMAND.read_text(encoding="utf-8"))
    assert "cp -a /cdrom/imagectl-src /target/opt/imagectl-src" in late
    assert "systemctl enable imagectl-firstboot.service" in late
    assert "installer-nic" in late and "installer-role" in late and "iso-release.json" in late
    assert "chage -d 0 root" in late
    # ‏d-i מסיר grub-pc-bin בהתקנת UEFI (נמדד 19/09); המתקין באתחול הראשון
    # מתקין אותו מחדש — בלי אינטרנט זה עובד רק מ-repo שנשאר על הדיסק.
    assert "/var/lib/imagectl/apt-repo" in late and "sources.list.d/imagectl-iso.list" in late
    first = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    assert "apt-repo-missing" in first and "grub-pc-bin" in first
    assert LATE_COMMAND.read_text(encoding="utf-8").startswith("#!/bin/sh\n"), "רץ ב-busybox של d-i — POSIX sh"


def test_build_iso_substitutes_every_placeholder_the_preseed_has() -> None:
    placeholders = set(re.findall(r"@[A-Z_]+@", PRESEED.read_text(encoding="utf-8")))
    assert placeholders == {"@PKGSEL_INCLUDE@", "@ROOT_PASSWORD_HASH@"}
    build = BUILD_ISO.read_text(encoding="utf-8")
    for ph in placeholders:
        assert ph in build, f"build-iso.sh אינו מחליף את {ph}"
    assert "auto=true priority=critical preseed/file=/cdrom/preseed.cfg" in build
    # ‏#1180: הערכים עברו לתבניות — הערך המשני חי בשתיהן (UEFI ו-BIOS), לא בסקריפט.
    for tpl in ("grub.cfg.in", "imagectl.cfg.in"):
        assert "imagectl.role=secondary" in (ISO_DIR / tpl).read_text(encoding="utf-8"), tpl
    assert "-boot_image any replay" in build, "בלי replay ה-shim/GRUB החתומים לא נשמרים (R57)"
    assert "-report_el_torito" in build


def test_grub_template_has_only_the_two_imagectl_entries() -> None:
    grub = GRUB_TEMPLATE.read_text(encoding="utf-8")
    entries = re.findall(r"^menuentry .*", grub, re.MULTILINE)
    assert entries == [
        "menuentry --hotkey=m 'ImageCtl server install (erases disk 1)' {",
        "menuentry --hotkey=s 'ImageCtl secondary server install (erases disk 1)' {",
    ]
    assert "set timeout=0" in grub, "‏#1189: ישר להתקנה, בלי תפריט"
    assert "set default=0" in grub
    assert "ImageCtl @TAG@ installer" in grub
    assert "set color_normal=white/black" in grub and "background_color '#1b2a41'" in grub
    assert all(word not in grub for word in ("Graphical", "Install", "Advanced", "Accessible"))
    assert grub.count("/install.amd/vmlinuz") == 2
    assert grub.count("/install.amd/initrd.gz") == 2


def test_isolinux_templates_hide_every_debian_menu() -> None:
    menu = ISOLINUX_MENU_TEMPLATE.read_text(encoding="utf-8").splitlines()
    assert menu == ["include stdmenu.cfg", "include imagectl.cfg", "timeout 1"], "‏#1189: ישר להתקנה גם ב-BIOS"
    entries = ISOLINUX_ENTRY_TEMPLATE.read_text(encoding="utf-8")
    assert re.findall(r"^label (.+)$", entries, re.MULTILINE) == ["imagectl", "imagectl-secondary"]
    assert entries.count("menu default") == 1
    assert "I^mageCtl server install (erases disk 1)" in entries
    assert "ImageCtl ^secondary server install (erases disk 1)" in entries
    assert all(word not in entries for word in ("Graphical", "Install", "Advanced", "Accessible"))


# --- firstboot -----------------------------------------------------------------

def test_firstboot_never_passes_deploy_if_builds_payload_and_starts_wizard() -> None:
    code = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    assert "--deploy-if" not in code, "כרטיס הפצה שנוחש = dnsmasq על הרשת הלא נכונה (R25 §2.5)"
    assert "setup-boot-server.sh" not in code, "שלב ב' שייך לאשף ואינו עוד התקנה לא-אינטראקטיבית"
    assert code.count("--skip-apt") == 2, "שני initrd (טקסט + GUI), שניהם בלי apt — החבילות מה-ISO"
    # ‏#1125: build_initramfs.sh נופל בלי SOURCE_DATE_EPOCH כשאין .git — ו-/opt/imagectl-src
    # הוא git archive. הזמן מגיע ממניפסט ה-ISO (נמדד ב-QEMU 19/09: payload-failed).
    assert code.count("--source-date-epoch") == 2, "שני ה-initrd חייבים לקבל --source-date-epoch"
    assert "source_date_epoch" in code and "iso-release.json" in code
    assert "--with-gui" in code
    assert 'systemctl start imagectl-wizard' in code
    assert 'install/imagectl-wizard.service' in code
    unit = WIZARD_SERVICE.read_text(encoding="utf-8")
    assert "After=network-online.target" in unit
    assert "--installer-nic /etc/imagectl/installer-nic" in unit
    assert "--installer-role /etc/imagectl/installer-role" in unit


def test_firstboot_reads_the_installer_facts_and_never_guesses_a_nic() -> None:
    code = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    assert "/etc/imagectl" in code and "installer-nic" in code and "installer-role" in code
    for state in ("network-nic-undecidable", "check-error", "secondary-needs-primary"):
        assert state in code, f"המצב {state} אינו מדווח"
    assert "firstboot.status" in code
    # ה-heuristic של R25 — "הראשון עם IPv4" — אסור שיחזור.
    assert not re.search(r"addr show scope global.*\|\s*awk.*exit", code), "בחירת 'הראשון עם IPv4' חזרה"


def test_installer_console_runtime_text_is_ascii_only() -> None:
    for script in (FIRSTBOOT, LATE_COMMAND):
        runtime_text = _strip_comments(script.read_text(encoding="utf-8"))
        assert runtime_text.isascii(), f"{script.name} has non-ASCII runtime text"


def test_firstboot_brings_up_dhcp_and_dcui_before_the_wizard() -> None:
    code = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    dhcp = code.index('dhcpcd -4 -1 -t 20 "$nic"')
    dcui = code.index("systemctl restart imagectl-dcui.service")
    wizard = code.index("systemctl start imagectl-wizard")
    assert dhcp < dcui < wizard
    assert 'for nic_path in /sys/class/net/*' in code
    assert 'temporary_dhcp "$nic" &' in code and 'wait "$dhcp_pid"' in code
    assert '/sys/class/net/$nic/carrier' in code and "sleep 1" in code
    assert "for _ in 1 2 3" in code  # ‏shellcheck SC2034: המשתנה אינו בשימוש
    for outcome in ("(dhcp)", "no carrier", "no dhcp offer"):
        assert outcome in code
    assert code.count('>>"$BUILD_LOG" 2>&1') >= 3
    assert "WorkingDirectory=/opt/imagectl-src" in code
    assert "imagectl-dcui.service.d/firstboot.conf" in code
    assert 'systemctl mask getty@tty1.service' in code
    assert 'systemctl is-active --quiet imagectl-dcui.service' in code


def test_permanent_setup_stops_other_temporary_clients_and_removes_dcui_dropin() -> None:
    setup = (REPO / "install" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert 'dhcpcd -k "$temporary_nic"' in setup
    assert '"$temporary_nic" == "$SERVERS_IF"' in setup
    assert "rm -f /etc/systemd/system/imagectl-dcui.service.d/firstboot.conf" in setup
    assert "systemctl restart imagectl-dcui.service" in setup


# --- תחביר -------------------------------------------------------------------

@requires_native(("bash", shutil.which("bash") or shutil.which("bash.exe")))
@pytest.mark.parametrize("name", BASH_SCRIPTS + ("late-command.sh",))
def test_scripts_parse(name: str) -> None:
    bash = shutil.which("bash") or shutil.which("bash.exe")
    proc = subprocess.run([bash, "-n", str(ISO_DIR / name)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_the_iso_source_is_the_public_repo_and_private_paths_stop_the_build() -> None:
    """‏20/09: ISO v0.51.0 הראשון נבנה מ-`git archive` של הריפו **הפרטי** —
    62 תשובות מחקר, `tools/agents/`, `.agents/`, `logs/` נסעו בתוך `imagectl-src/`
    לכל שרת שיותקן. ברירת המחדל היא clone של הציבורי בתג, ושומר ב-ISO_TREE
    מסרב לארוז נתיבים פרטיים גם כשנותנים `--source` מקומי — אותה רשימה כמו
    HARD_DENY ב-publish-to-public.sh, ועוד מה שרק הפרטי מחזיק."""
    code = BUILD_ISO.read_text(encoding="utf-8")
    assert 'https://github.com/NadavOked/ImageCtl.git' in code, "ברירת המחדל אינה הריפו הציבורי"
    assert 'git clone -q --branch "$REF" --depth 1 "$PUBLIC_URL"' in code
    for deny in (".claude", ".agents", ".otogit", "tools/agents", "AGENTS.md", "logs", "docs/research"):
        assert f" {deny} " in code or f" {deny};" in code, f"השומר אינו מכסה {deny}"
    assert 'die "המקור אינו העץ הציבורי' in code


# --- #1185: כפתור העדכון משרת שהותקן מה-ISO --------------------------------
# השרת הראשון שהותקן מה-ISO (20/09) הציג "לא ידועה (אין תגית git על העץ)"
# ו-"[Errno 2] No such file or directory: 'git'": ה-ISO ארז את העץ ב-git
# archive (בלי .git) ולא נשא git. שלושת התנאים שהעדכון דורש נבדקים כאן
# בשמם, כי כל אחד מהם לבדו נכשל בשקט עד מול שרת אמיתי.


def test_the_installer_always_installs_git_so_the_update_button_works() -> None:
    assert "git" in installer_pkgs(), "git ב-PKGS של המתקין תמיד — לא רק כשמושכים קוד מהרשת"
    src = (REPO / "install" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert "PKGS+=(git)" not in src, "git מותנה בהיעדר server/main.py — מה-ISO הוא קיים ו-git לא הותקן"
    assert "git" in packages_txt(), "git חייב להיות ב-pool של ה-ISO (ההתקנה offline)"


def test_build_iso_packs_the_public_clone_with_its_git_dir_and_never_a_private_one() -> None:
    src = BUILD_ISO.read_text(encoding="utf-8")
    code = src.split("--- הקוד ---", 1)[1]
    assert 'cp -a "$REPO/.git" "$ISO_TREE/imagectl-src/.git"' in code, "ה-.git של ה-clone מהציבורי לא נארז"
    guard = code.split('cp -a "$REPO/.git"', 1)[0]
    assert '"$origin" == "$PUBLIC_URL"' in guard, "אין בדיקה ש-origin של ה-.git הנארז הוא הציבורי"
    assert 'if [[ -z "$SOURCE" ]]' in guard, "‏--source (עץ מקומי, היסטוריה פרטית) חייב להישאר בלי .git"


def test_dcui_unit_starts_on_a_debian_13_server() -> None:
    """‏21/09, ההתקנה הראשונה על ESXi: ‏`ReadWritePaths=/var/lib/dhcp` — תיקייה שאין
    בדביאן 13 (dhcpcd, לא isc-dhcp-client) — הפילה את ה-namespace (226/NAMESPACE)
    ו-DCUI לא עלה 156 פעמים; ‏`Type=simple` היה "active" מרגע ה-fork ו-firstboot
    האמין לו. כל נתיב ב-ReadWritePaths הוא או כזה שהמתקין יוצר, או אופציונלי (-)."""
    unit = (REPO / "install" / "imagectl-dcui.service").read_text(encoding="utf-8")
    assert "Type=exec" in unit and "Type=simple" not in unit
    paths = re.search(r"^ReadWritePaths=(.*)$", unit, re.M).group(1).split()
    created_by_installer = {"/var/lib/imagectl", "/etc/network/interfaces.d", "/etc"}
    for p in paths:
        assert p in created_by_installer or p.startswith("-"), f"{p}: לא קיים בהכרח בשרת טרי ואינו אופציונלי"
    assert "/var/lib/dhcp" not in paths, "isc-dhcp-client אינו בדביאן 13"
    fb = FIRSTBOOT.read_text(encoding="utf-8")
    assert '"$address" == 169.254.*' in fb, "link-local אינו offer — firstboot חייב לקרוא לזה 'no dhcp offer'"
