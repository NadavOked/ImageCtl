"""מוניטור וגואי בלי מסך מחובר (#835) — framebuffer בזיכרון.

מחשב שיכפול 2 (.59): כל המחברים מנותקים, ‏i915 לא יוצר ‏`/dev/fb0`,
הגואי נופל ב-"cannot open a display" והמוניטור — שנועד בדיוק למכונה
בלי מסך — נופל ב-"open framebuffer" בלולאת restart. הפתרון (הכרעת נדב
15/09): הגואי מצייר לקובץ ממופה, ‏`imagectl-monitor --fb <קובץ>` מגיש
אותו.

מה נבדק כאן, ואיפה:

* **החוזה בין הרכיבים** (‏`docs/interfaces.md` סעיף 15) — כותרת של 4096
  בייטים ואז פיקסלים. הקסם, גודל הכותרת והנתיב מופיעים בשני קובצי ה-C
  ובשני קובצי ה-sh; הטסטים **נועלים אותם זהים**, כי סטייה של אחד
  מהם היא מוניטור שקורא זבל ולא נופל.
* **הסוכן (sh)** — ‏`monitor_start` ו-`gui_backend_args` מול ‏`FB_DEV`
  שקיים (‏`/dev/null` הוא התקן תווים בכל מקום) או חסר: עם מסך המסלול
  זהה להיום (בלי `--fb`, ‏`--backend fbdev`); בלי מסך — ‏`mem` והקובץ.
* **הצד המקומפל** (‏`requires_native`, מעבדה בלבד): הגואי ב-`--backend
  mem` כותב כותרת תקינה ופיקסלים שאינם אפס; ‏`imagectl-monitor --fb`
  על הקובץ מאזין ב-RFB ומגיש את הבאנר, ונופל בגלוי על קובץ פגום.

בקרה שלילית לכל אחד — ראה גוף ה-PR.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
# #839: the binary refuses to start without --secret-file, and a client
# must pass security type 2 before it sees ServerInit. Same client as
# test_monitor_native -- imported, not copied.
from test_monitor_native import SECRET, _handshake

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
GUI = REPO / "native-gui"
MONITOR_C = AGENT / "monitor.c"
BACKEND_C = GUI / "src" / "backend.c"
MONITOR_SH = AGENT / "lib" / "monitor.sh"
GUIBRIDGE_SH = AGENT / "lib" / "guibridge.sh"
INTERFACES = REPO / "docs" / "interfaces.md"

#: החוזה (‏interfaces.md סעיף 15). מקור יחיד לצורה בטסט — שני צידי ה-C
#: חייבים להסכים עליו בדיוק.
MAGIC = b"IMCTLFB1"
HEADER_BYTES = 4096
FORMAT_XRGB8888 = 1
WIDTH, HEIGHT = 1280, 800
STRIDE = WIDTH * 4
FILE_NAME = "fb.mem"
DEFAULT_PATH = "/run/imagectl/" + FILE_NAME


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


def _c_defines(text: str) -> dict[str, str]:
    return dict(re.findall(r"^#define\s+(MEMFB_\w+)\s+(\S+)", text, re.M))


# --- (א) החוזה: אותם קבועים בשני קובצי ה-C, ואותו קובץ בשני ה-sh ----------


def test_monitor_and_gui_agree_on_the_header():
    """הקסם, גודל הכותרת והפורמט זהים ב-monitor.c וב-backend.c. הטסט
    משווה ל-**ערך** שבחוזה, לא רק "שווים זה לזה" — שניהם יכולים לסטות יחד."""
    mon = _c_defines(MONITOR_C.read_text(encoding="utf-8"))
    gui = _c_defines(BACKEND_C.read_text(encoding="utf-8"))
    for key, want in (("MEMFB_MAGIC", '"IMCTLFB1"'),
                      ("MEMFB_HEADER_BYTES", str(HEADER_BYTES)),
                      ("MEMFB_FORMAT_XRGB8888", str(FORMAT_XRGB8888))):
        assert mon.get(key) == want, f"monitor.c {key}={mon.get(key)!r}"
        assert gui.get(key) == want, f"backend.c {key}={gui.get(key)!r}"
    # הרזולוציה של ה-mem backend — הכרעה נעולה ב-#835 (ברירת המחדל של --png).
    assert gui.get("MEMFB_W") == str(WIDTH) and gui.get("MEMFB_H") == str(HEIGHT)
    assert gui.get("MEMFB_DEFAULT_PATH") == f'"{DEFAULT_PATH}"'


def test_the_memory_framebuffer_is_one_file_everywhere():
    """הגואי כותב ל-`$RUN_DIR/fb.mem`, המוניטור מקבל את **אותו** נתיב,
    וברירת המחדל ב-C היא `/run/imagectl/fb.mem` (‏`RUN_DIR` של common.sh).
    שם אחר באחד מהם = מוניטור שמחכה לקובץ שלא ייווצר לעולם."""
    for path in (MONITOR_SH, GUIBRIDGE_SH):
        assert f'"$RUN_DIR/{FILE_NAME}"' in path.read_text(encoding="utf-8"), path.name
    assert DEFAULT_PATH in INTERFACES.read_text(encoding="utf-8")


def test_monitor_c_serves_a_regular_file_without_ioctl():
    """‏monitor.c מבחין בין התקן (ioctl) לקובץ רגיל (כותרת) לפי `fstat`,
    ובודק את הקסם — לא מניח שכל קובץ הוא framebuffer."""
    text = MONITOR_C.read_text(encoding="utf-8")
    assert "S_ISREG" in text
    assert "MEMFB_MAGIC" in text and "memcmp" in text
    # ה-seqlock: המוניטור מדלג על פריים קרוע במקום להגיש חצי ציור.
    assert "seq" in text and "__atomic_load_n" in text


def test_backend_auto_falls_through_to_mem():
    """‏`auto` = drm → fbdev → mem: בלי מסך הגואי לא נופל אלא מצייר לקובץ.
    ‏`mem` מפורש קיים גם הוא, ו-`--fb-file` בוחר את הקובץ."""
    text = BACKEND_C.read_text(encoding="utf-8")
    assert "KIND_MEM" in text
    assert re.search(r'strcmp\(kind,\s*"mem"\)', text)
    auto = text[text.index('strcmp(kind, "auto")'):]
    assert auto.index("drm_open(") < auto.index("fb_open(") < auto.index("mem_open("), \
        "auto must try drm, then fbdev, then mem"
    # seqlock סביב ההעתקה: המוניטור יודע מתי הפריים שלם.
    assert "__atomic" in text
    main = (GUI / "src" / "main.c").read_text(encoding="utf-8")
    assert "--fb-file" in main and "auto|drm|fbdev|mem" in main


def test_interfaces_documents_the_memory_framebuffer():
    text = INTERFACES.read_text(encoding="utf-8")
    assert "IMCTLFB1" in text and "4096" in text and "seq" in text


# --- (ב) הסוכן: monitor.sh ו-guibridge.sh מול FB_DEV ------------------------


def _monitor_start(tmp_path, fb_dev, role="cloner", mem_file=False):
    """מריץ monitor_start עם `_monitor_spawn` מזויף (כמו test_agent) ומחזיר
    ‏(ארגומנטים או None, מה שנרשם ליומן)."""
    out = tmp_path / "spawn-args"
    logf = tmp_path / "log"
    run_dir = tmp_path / "run"
    run_dir.mkdir(exist_ok=True)
    # #839: no boot secret, no monitor -- seed it like build_hello does.
    (run_dir / "monitor.secret").write_text("00112233445566778899aabbccddeeff\n")
    if mem_file:
        (run_dir / FILE_NAME).write_bytes(b"x" * 64)
    script = (
        'OUT=' + repr(posix(out)) + '; LOGF=' + repr(posix(logf)) + '; '
        'export IMAGECTL_MONITOR=1; export IP=10.0.0.9; export MONITOR_BIN=/bin/sh; '
        'export RUN_DIR=' + repr(posix(run_dir)) + '; '
        'export FB_DEV=' + repr(fb_dev) + '; '
        f'export D_ROLE={role}; '
        'log() { echo "$*" >> "$LOGF"; }; '
        '. ' + posix(AGENT) + '/lib/monitor.sh; '
        '_monitor_spawn() { echo "$@" > "$OUT"; _monitor_pid=4242; }; '
        'monitor_start; monitor_start')
    subprocess.run([BASH, "-c", script], capture_output=True,
                   cwd=str(REPO), stdin=subprocess.DEVNULL)
    args = out.read_text(encoding="utf-8").strip() if out.exists() else None
    log = logf.read_text(encoding="utf-8") if logf.exists() else ""
    return args, log


@requires_native(("bash", BASH))
def test_monitor_with_a_screen_is_unchanged(tmp_path):
    """יש `/dev/fb0` (כאן: `/dev/null`, התקן תווים): בלי `--fb` — ‏monitor.c
    פותח את `/dev/fb0` שלו כמו היום. המסלול הקיים לא נשבר."""
    args, _ = _monitor_start(tmp_path, "/dev/null", role="build")
    assert args is not None
    assert "--fb" not in args.split() and "--input" in args.split(), args


@requires_native(("bash", BASH))
def test_monitor_without_a_screen_waits_for_the_gui_file(tmp_path):
    """אין fb0 ואין עדיין `fb.mem`: המוניטור **לא** עולה (אין מה להגיש), ומה
    שנרשם אומר את זה פעם אחת — לא לולאת restart של FATAL כל 5 שניות."""
    args, log = _monitor_start(tmp_path, posix(tmp_path / "no-such-fb0"))
    assert args is None, args
    assert log.count("monitor:") == 1, log
    assert FILE_NAME in log


@requires_native(("bash", BASH))
def test_monitor_without_a_screen_serves_the_gui_file(tmp_path):
    """אין fb0 אבל הגואי כבר כותב `fb.mem`: המוניטור עולה עם `--fb` על
    הקובץ, ושער התפקיד לא זז — משכפל עדיין בלי `--input`."""
    args, _ = _monitor_start(tmp_path, posix(tmp_path / "no-such-fb0"), mem_file=True)
    assert args is not None, "המוניטור לא עלה למרות שהקובץ קיים"
    words = args.split()
    assert "--fb" in words and words[words.index("--fb") + 1].endswith("/run/" + FILE_NAME)
    assert "--input" not in words, args


def _gui_backend_args(tmp_path, fb_dev, backend_env=None):
    env = f'export GUI_BACKEND={backend_env}; ' if backend_env else 'unset GUI_BACKEND; '
    script = (
        env + 'export RUN_DIR=/run/imagectl; export FB_DEV=' + repr(fb_dev) + '; '
        '. ' + posix(AGENT) + '/lib/guibridge.sh; gui_backend_args')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@requires_native(("bash", BASH))
def test_gui_keeps_fbdev_when_a_screen_is_attached(tmp_path):
    """‏#641: על i915 עם מסך ‏fbdev הוא הפלט המוכח — נשאר ברירת המחדל, בלי
    ‏`--fb-file` (שורת הפקודה של המסלול הקיים זהה)."""
    assert _gui_backend_args(tmp_path, "/dev/null") == "--backend fbdev"


@requires_native(("bash", BASH))
def test_gui_draws_to_memory_without_a_screen(tmp_path):
    assert _gui_backend_args(tmp_path, posix(tmp_path / "no-such-fb0")) == \
        f"--backend mem --fb-file /run/imagectl/{FILE_NAME}"


@requires_native(("bash", BASH))
def test_gui_backend_override_still_wins(tmp_path):
    """‏GUI_BACKEND=drm|auto|mem מפורש גובר על בדיקת ההתקן, כמו היום."""
    assert _gui_backend_args(tmp_path, "/dev/null", "drm") == "--backend drm"
    assert _gui_backend_args(tmp_path, "/dev/null", "mem") == \
        f"--backend mem --fb-file /run/imagectl/{FILE_NAME}"


# --- (ג) הצד המקומפל — מעבדה בלבד --------------------------------------------


def _read_header(path: Path):
    data = path.read_bytes()
    assert len(data) == HEADER_BYTES + STRIDE * HEIGHT, len(data)
    magic = data[:8]
    w, h, stride, fmt, seq = struct.unpack("<5I", data[8:28])
    return magic, w, h, stride, fmt, seq, data[HEADER_BYTES:]


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native-gui נבנה על המעבדה בלבד",
)
def test_native_gui_mem_backend_writes_a_valid_frame(tmp_path):
    """‏`--backend mem` בלי מסך, בלי tty0, בלי קלט: הקובץ נוצר בגודל הנכון,
    הכותרת תקינה, ‏`seq` זוגי (פריים שלם) והפיקסלים אינם כולם אפס — משהו
    צויר. בקרה שלילית: backend.c בלי `mem` → "cannot open a display", rc=1."""
    binary = tmp_path / "gui-bin"
    build = subprocess.run(
        [BASH, "-c",
         'set -e; cd "$1"; '
         'cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE '
         '$(pkg-config --cflags pangocairo cairo libdrm) '
         '-o "$2" $(make -s -f Makefile print-gui-src) '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    state = tmp_path / "state.txt"
    state.write_text("cloner=Office 2024\ndrawer=1|sda|writing|45|100|\n", newline="\n")
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    fb = tmp_path / FILE_NAME
    err = tmp_path / "gui.err"
    with err.open("wb") as errf:
        proc = subprocess.Popen(
            [str(binary), "--backend", "mem", "--fb-file", str(fb), "--no-input",
             "--screen", "cloner", "--state", str(state),
             "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31"],
            stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL, env=env)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if fb.exists() and fb.stat().st_size == HEADER_BYTES + STRIDE * HEIGHT:
                    _, _, _, _, _, seq, pixels = _read_header(fb)
                    if seq >= 2 and seq % 2 == 0 and any(pixels):
                        break
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    assert proc.returncode == 1, f"stopped by a signal exits 1; got {proc.returncode}: {err.read_text()}"
    magic, w, h, stride, fmt, seq, pixels = _read_header(fb)
    assert magic == MAGIC and (w, h, stride, fmt) == (WIDTH, HEIGHT, STRIDE, FORMAT_XRGB8888)
    assert seq >= 2 and seq % 2 == 0, seq
    assert any(pixels), "the frame is all black -- nothing was drawn"
    assert "display 1280x800" in err.read_text(encoding="utf-8", errors="replace")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_monitor(tmp_path: Path) -> Path:
    binary = tmp_path / "imagectl-monitor"
    build = subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-o", str(binary), str(MONITOR_C), "-lvncserver"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _secret_file(tmp_path: Path) -> Path:
    """סוד-אתחול תקין (32 hex) — הבינארי מסרב לעלות בלעדיו (#839)."""
    path = tmp_path / "monitor.secret"
    path.write_text(SECRET + "\n")
    return path


def _write_memfb(path: Path, magic: bytes = MAGIC, seq: int = 0) -> None:
    header = magic + struct.pack("<5I", WIDTH, HEIGHT, STRIDE, FORMAT_XRGB8888, seq)
    path.write_bytes(header.ljust(HEADER_BYTES, b"\0") + bytes([0x40]) * (STRIDE * HEIGHT))


@requires_native(
    ("gcc", shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    posix=True, why="imagectl-monitor נבנה על המעבדה בלבד",
)
def test_monitor_serves_a_memory_framebuffer_file_over_rfb(tmp_path):
    """‏`--fb <קובץ רגיל>`: בלי ioctl — הגיאומטריה מהכותרת, ‏RFB מאזין ומגיש
    את הבאנר. זה מה שנכשל היום על .59 (FATAL: open framebuffer)."""
    binary = _build_monitor(tmp_path)
    fb = tmp_path / FILE_NAME
    _write_memfb(fb)
    port = _free_port()
    err = tmp_path / "mon.err"
    with err.open("wb") as errf:
        proc = subprocess.Popen(
            [str(binary), "--bind", "127.0.0.1", "--port", str(port),
             "--fb", str(fb), "--secret-file", str(_secret_file(tmp_path))],
            stdout=subprocess.DEVNULL, stderr=errf, stdin=subprocess.DEVNULL)
        try:
            size = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
                except OSError:
                    time.sleep(0.2)
                    continue
                with sock:
                    size = _handshake(sock)     # type 2 + the secret (#839)
                break
        finally:
            proc.terminate()
            proc.wait(timeout=10)
    text = err.read_text(encoding="utf-8", errors="replace")
    assert size == (WIDTH, HEIGHT), (size, text)
    assert "1280x800" in text and "memory framebuffer" in text, text


@requires_native(
    ("gcc", shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    posix=True, why="imagectl-monitor נבנה על המעבדה בלבד",
)
def test_monitor_refuses_a_file_that_is_not_a_framebuffer(tmp_path):
    """קובץ רגיל בלי הקסם — נופל בגלוי, לא מגיש זבל (עיקרון 5)."""
    binary = _build_monitor(tmp_path)
    fb = tmp_path / FILE_NAME
    _write_memfb(fb, magic=b"NOTAFB!!")
    proc = subprocess.run(
        [str(binary), "--bind", "127.0.0.1", "--port", str(_free_port()),
         "--fb", str(fb), "--secret-file", str(_secret_file(tmp_path))],
        capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    assert proc.returncode != 0
    assert "memory framebuffer" in proc.stderr, proc.stderr
    # The refusal is about the file, not about the secret (#839 usage).
    assert "usage:" not in proc.stderr, proc.stderr
