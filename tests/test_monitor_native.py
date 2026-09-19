"""‏#833: המוניטור ב-view-only חייב לשרוד לקוח ששולח קלט.

‏`monitor.sh` נותן `--input` רק למחשב הבנייה; משכפל רץ view-only. שם
‏`agent/monitor.c` השאיר את ‏`kbdAddEvent`/`ptrAddEvent` כ-`NULL`,
‏ו-LibVNCServer קורא להם **בלי בדיקה** — הלקוח של הקונסולה שולח
‏PointerEvent מיד אחרי ה-handshake, והתהליך קפץ לכתובת 0 (‏`segfault at 0
ip 0`, ‏rc=139, נמדד על .118 ב-15/09). ה-supervisor הפעיל מחדש אחרי 5ש',
ולכן מהדפדפן זה נראה "פורט פתוח אבל החיבור נסגר".

מה שנבדק כאן הוא **ההתנהגות**, לא הקוד: בונים את הבינארי מול ספריית
‏libvncserver של המערכת, מריצים אותו על קובץ שמחליף את ‏`/dev/fb0`
‏(`--fb FILE --geometry WxH`), ולקוח RFB מינימלי מבצע handshake, מבקש
‏FramebufferUpdate, שולח ‏KeyEvent + PointerEvent, ומבקש שוב. התהליך חייב
להישאר חי ולהגיש את שתי התמונות. הראיה החיובית היא ה-FramebufferUpdate
**השני** — אחרי הקלט (עיקרון 5): "החיבור לא נסגר" לבדו אינו ראיה.

בקרה שלילית: על ‏`= NULL` (‏main ‏2ae4b11) הלקוח מקבל את התמונה הראשונה,
ואחרי ה-KeyEvent הסוקט נסגר — ‏`_recv_exact` נופל בשם, והתהליך יוצא
ב-SIGSEGV (‏rc=-11, ‏139 במעטפת). מה שנמדד על הברזל ב-15/09 הוא בדיוק
הרצף הזה; הבקרה עצמה רצה במעבדה, לא בווינדוס.

‏gcc + libvncserver-dev יש רק במעבדה (‏`build_initramfs.sh` מתקין אותם);
בווינדוס הבדיקה מדלגת בהצהרה — ‏`requires_native`, כמו ‏`test_cloner_gui`.

‏#839 — האימות: ‏5900 קשוב לכל וילן ההפצה, ולכן לחיצת-היד של ה-RFB היא
השער. המוניטור מציע **רק** סוג-אבטחה 2 (מסגור VNC Authentication); התשובה
ל-challenge היא 16 בייטי סוד-האתחול (`--secret-file`, 32 hex). לקוח בלי
הסוד לא מקבל מסך, לא מזריק קלט, ו**לא מפעיל reboot** — גם אם דחף
ClientCutText מיד אחרי התשובה השגויה. ההוכחה היא סטאב `reboot` ב-PATH
שכותב קובץ: בלי סוד הקובץ לא נוצר, עם סוד הוא נוצר.

בקרה שלילית (מעבדה, על `origin/main` שלפני #839, ‏f83eb4f): שם אין
`--secret-file` (הבינארי מסרב לדגל — משמיטים אותו), הרשימה מציעה `1`,
ו-`test_power_cut_text_needs_an_authenticated_session` נופל בשלב הראשון:
הקובץ **נוצר** מחיבור שלא הזדהה. זו בדיוק החולשה.
"""

from __future__ import annotations

import hashlib
import hmac as hmaclib
import os
import shutil
import socket
import struct
import subprocess
import time
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
MONITOR_C = REPO / "agent" / "monitor.c"
HMAC_C = REPO / "agent" / "hmac_sha256.c"

WIDTH, HEIGHT = 64, 32
SECRET = "00112233445566778899aabbccddeeff"
CHALLENGE = bytes(range(16))                     # אותו וקטור כמו test_monitor.py
KNOWN_HMAC = bytes.fromhex("a5ce9cbf7c63cbedf3403e594d04b0ef")

SECRET_BYTES = bytes.fromhex(SECRET)
WRONG_SECRET = bytes(16)
POWER_REBOOT = b"imagectl-power:reboot"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    posix=True,
    why="imagectl-monitor נבנה על המעבדה בלבד",
)


def _hmac16(secret: bytes, challenge: bytes) -> bytes:
    return hmaclib.new(secret, challenge, hashlib.sha256).digest()[:16]


def _build(tmp_path: Path) -> Path:
    """אותה שורת הידור כמו ב-`tools/build_initramfs.sh`."""
    binary = tmp_path / "imagectl-monitor"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(binary),
         str(MONITOR_C), str(HMAC_C), "-lvncserver"],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    return binary


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """קורא בדיוק n בייטים; EOF מוקדם הוא כישלון בשם — לא bytes קצר."""
    chunks = bytearray()
    while len(chunks) < n:
        piece = sock.recv(n - len(chunks))
        assert piece, f"the monitor closed the socket after {len(chunks)}/{n} bytes"
        chunks += piece
    return bytes(chunks)


def _security_types(sock: socket.socket) -> bytes:
    """גרסה + רשימת סוגי האבטחה שהמוניטור מציע ללקוח 3.8."""
    banner = _recv_exact(sock, 12)
    assert banner.startswith(b"RFB 003."), banner
    sock.sendall(b"RFB 003.008\n")
    count = _recv_exact(sock, 1)[0]
    assert count, "the server offered no security types"
    return _recv_exact(sock, count)


def _answer_challenge(sock: socket.socket, response: bytes | None = None,
                      trailing: bytes = b"",
                      secret: bytes = SECRET_BYTES) -> int:
    """בוחר סוג 2, עונה ל-challenge ב-HMAC (או ב-`response` מפורש —
    סוד גולמי לבקרה שלילית), ומחזיר את SecurityResult."""
    sock.sendall(b"\x02")
    challenge = _recv_exact(sock, 16)
    assert len(challenge) == 16
    if response is None:
        response = _hmac16(secret, challenge)
    sock.sendall(response + trailing)
    return struct.unpack(">I", _recv_exact(sock, 4))[0]


def _handshake(sock: socket.socket) -> tuple[int, int]:
    """‏RFB 3.8, סוג 2 עם HMAC-SHA256(סוד, challenge), ‏ClientInit משותף.
    מחזיר את גודל המסך. ‏None אסור שיוצע בכלל — זה השער של #839."""
    types = _security_types(sock)
    assert 1 not in types, f"None security offered: {list(types)}"
    assert 2 in types, f"no secret security in {list(types)}"
    assert _answer_challenge(sock) == 0, "security failed"
    sock.sendall(b"\x01")                      # ClientInit: shared
    width, height = struct.unpack(">HH", _recv_exact(sock, 4))
    _recv_exact(sock, 16)                      # pixel format
    name_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
    _recv_exact(sock, name_len)
    return width, height


def _request_update(sock: socket.socket, width: int, height: int) -> int:
    """‏FramebufferUpdateRequest מלא (לא incremental), וקריאת ה-Update.
    מחזיר כמה בייטי פיקסלים הגיעו ב-Raw — הראיה שהתמונה הוגשה."""
    sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, width, height))
    kind = _recv_exact(sock, 1)[0]
    assert kind == 0, f"expected FramebufferUpdate (0), got message type {kind}"
    _recv_exact(sock, 1)
    rects = struct.unpack(">H", _recv_exact(sock, 2))[0]
    pixel_bytes = 0
    for _ in range(rects):
        _x, _y, w, h, encoding = struct.unpack(">HHHHi", _recv_exact(sock, 12))
        assert encoding == 0, f"unexpected encoding {encoding}; the client asked for none"
        pixel_bytes += len(_recv_exact(sock, w * h * 4))
    return pixel_bytes


def _key(sock: socket.socket, keysym: int, down: bool) -> None:
    sock.sendall(struct.pack(">BBHI", 4, 1 if down else 0, 0, keysym))


def _pointer(sock: socket.socket, buttons: int, x: int, y: int) -> None:
    sock.sendall(struct.pack(">BBHH", 5, buttons, x, y))


def _cut_text(text: bytes) -> bytes:
    """‏ClientCutText (msg 6), כמו ש-monitor.js בונה אותו."""
    return struct.pack(">BxxxI", 6, len(text)) + text


class Monitor:
    """מוניטור חי על קובץ-framebuffer, עם קובץ סוד, ולוג. ‏`env` מאפשר
    להחליף PATH — כך `reboot` הוא סטאב ולא המכונה."""

    def __init__(self, tmp_path: Path, binary: Path, *, secret: str | None = SECRET,
                 env: dict | None = None, extra: tuple[str, ...] = (),
                 allow_from: str | None = "127.0.0.1"):
        fb = tmp_path / "fb0.raw"
        fb.write_bytes(bytes([0x10, 0x20, 0x30, 0x00]) * (WIDTH * HEIGHT))
        self.port = _free_port()
        self.log_path = tmp_path / "monitor.log"
        self.log = open(self.log_path, "wb")
        self.secret_file = tmp_path / "monitor.secret"
        argv = [str(binary), "--bind", "127.0.0.1", "--port", str(self.port),
                "--fps", "10", "--fb", str(fb), "--geometry", f"{WIDTH}x{HEIGHT}"]
        if secret is not None:
            self.secret_file.write_text(secret + "\n")
            argv += ["--secret-file", str(self.secret_file)]
        if allow_from is not None:
            argv += ["--allow-from", allow_from]
        argv += list(extra)
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            env=env,
        )

    def connect(self) -> socket.socket:
        deadline = time.monotonic() + 10
        while True:
            assert self.proc.poll() is None, (
                f"the monitor exited before listening, rc={self.proc.returncode}")
            try:
                return socket.create_connection(("127.0.0.1", self.port), timeout=5)
            except OSError:
                assert time.monotonic() < deadline, "no RFB listener within 10s"
                time.sleep(0.1)

    def alive(self) -> bool:
        time.sleep(0.3)
        return self.proc.poll() is None

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.log.close()
        return self.log_path.read_text(errors="replace")


@NATIVE
def test_view_only_monitor_survives_keyboard_and_pointer_events(tmp_path):
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    proc = monitor.proc
    try:
        sock = monitor.connect()
        with sock:
            width, height = _handshake(sock)
            assert (width, height) == (WIDTH, HEIGHT)

            # (א) ‏FramebufferUpdateRequest לבד — לא מפיל (השאלה הפתוחה ב-#833).
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4

            # (ב) קלט, כמו שהלקוח של הקונסולה שולח: מקש ולחיצת עכבר.
            _key(sock, 0xFF0D, True)            # Enter down
            _key(sock, 0xFF0D, False)
            _pointer(sock, 1, 5, 5)             # left button down at (5,5)
            _pointer(sock, 0, 6, 6)

            # (ג) הראיה החיובית: התהליך עדיין מגיש תמונה **אחרי** הקלט.
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4

        assert monitor.alive(), (
            f"the monitor died after client input, rc={proc.returncode}")
    finally:
        text = monitor.stop()
    assert "view-only mode; input disabled" in text, text


# --- #839: השער — לחיצת-היד, לא ה-WebSocket -----------------------------------


def _reboot_stub(tmp_path: Path) -> tuple[dict, Path]:
    """‏`reboot` מזויף ראשון ב-PATH: כותב את הארגומנטים שלו לקובץ. הקובץ
    הוא הראיה החיובית ש-run_power רץ — ולא-קיומו הוא הראיה שלא."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    marker = tmp_path / "reboot.called"
    (stubs / "reboot").write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$REBOOT_MARKER"\n')
    (stubs / "reboot").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{stubs}:{env.get('PATH', '')}"
    env["REBOOT_MARKER"] = str(marker)
    return env, marker


@NATIVE
def test_monitor_offers_only_the_secret_type_and_rejects_a_wrong_secret(tmp_path):
    """סוד שגוי → SecurityResult 1 + סיבה, והחיבור נסגר; בחירת None (1)
    → אין SecurityResult 0 בכלל. ואחרי שניהם המוניטור חי ומגיש מסך
    ללקוח עם הסוד הנכון — הדחייה אינה קריסה ואינה נעילה."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    try:
        with monitor.connect() as sock:
            types = _security_types(sock)
            assert 1 not in types and 2 in types, list(types)
            assert _answer_challenge(sock, SECRET_BYTES) == 1   # גולמי ≠ HMAC
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            _recv_exact(sock, reason_len)
        assert monitor.alive()

        with monitor.connect() as sock:
            types = _security_types(sock)
            assert _answer_challenge(sock, WRONG_SECRET) == 1
            reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
            reason = _recv_exact(sock, reason_len)
            assert reason, "a 3.8 rejection carries a reason string"
            sock.settimeout(5)
            assert sock.recv(1) == b"", "the socket must close after a rejection"
        assert monitor.alive()

        with monitor.connect() as sock:
            _security_types(sock)
            sock.sendall(b"\x01")                  # None -- not offered
            sock.settimeout(5)
            head = b""
            while len(head) < 4:
                piece = sock.recv(4 - len(head))
                if not piece:
                    break
                head += piece
            # ‏3.8: ‏0 = "החיבור נכשל" + סיבה, או EOF. לעולם לא 0-אחרי-בחירה
            # ‏= הצלחה, כי ההצלחה היחידה היא SecurityResult אחרי challenge.
            assert head in (b"", b"\x00\x00\x00\x00"), head
            if head:
                reason_len = struct.unpack(">I", _recv_exact(sock, 4))[0]
                assert reason_len, "connection-failed carries a reason"
        assert monitor.alive()

        with monitor.connect() as sock:
            width, height = _handshake(sock)
            assert _request_update(sock, width, height) == WIDTH * HEIGHT * 4
    finally:
        text = monitor.stop()
    assert "rejected client" in text, text


@NATIVE
def test_power_cut_text_needs_an_authenticated_session(tmp_path):
    """(א) לקוח עם סוד שגוי שדוחף ClientCutText באותה כתיבה → `reboot`
    לא רץ (אין קובץ). (ב) לקוח מאומת ששולח אותו טקסט → `reboot -f` רץ.
    (ב) הוא השומר מפני תיקון-יתר: השער דוחה זרים, לא את כולם."""
    binary = _build(tmp_path)
    env, marker = _reboot_stub(tmp_path)
    monitor = Monitor(tmp_path, binary, env=env)
    try:
        with monitor.connect() as sock:
            _security_types(sock)
            assert _answer_challenge(sock, WRONG_SECRET,
                                     trailing=_cut_text(POWER_REBOOT)) == 1
        time.sleep(1.0)
        assert not marker.exists(), (
            f"reboot ran for an unauthenticated client: {marker.read_text()}")
        assert monitor.alive()

        with monitor.connect() as sock:
            _handshake(sock)
            sock.sendall(_cut_text(POWER_REBOOT))
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        assert marker.exists(), "reboot did not run for the authenticated client"
        assert marker.read_text().split() == ["-f"]
        assert monitor.alive(), "run_power must not take the monitor down"
    finally:
        text = monitor.stop()
    assert "remote reboot requested" in text, text


@NATIVE
def test_monitor_refuses_to_start_without_a_usable_secret(tmp_path):
    """בלי `--secret-file` — usage ויציאה; קובץ שאינו 32 hex — יציאה עם
    הסיבה. בשני המקרים 5900 לא נפתח: פתיחה בלי אימות אינה ברירת מחדל."""
    binary = _build(tmp_path)
    fb = tmp_path / "fb0.raw"
    fb.write_bytes(bytes(WIDTH * HEIGHT * 4))
    base = [str(binary), "--bind", "127.0.0.1", "--port", str(_free_port()),
            "--fb", str(fb), "--geometry", f"{WIDTH}x{HEIGHT}",
            "--allow-from", "127.0.0.1"]

    without = subprocess.run(base, capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL)
    assert without.returncode != 0
    assert "--secret-file" in without.stderr, without.stderr

    bad = tmp_path / "bad.secret"
    bad.write_text("not-a-secret\n")
    malformed = subprocess.run(base + ["--secret-file", str(bad)],
                               capture_output=True, text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
    assert malformed.returncode != 0
    assert "hex" in malformed.stderr, malformed.stderr


def test_view_only_source_never_assigns_null_input_callbacks():
    """שומר-מקור שרץ בכל תחנה, גם בלי gcc (ולכן בלי ‏NATIVE): ‏`kbdAddEvent`/`ptrAddEvent`
    לעולם אינם `NULL`. זה בדיוק השורש של #833 — ‏LibVNCServer אינו בודק.
    בקרה שלילית: החזרת ‏`screen->kbdAddEvent = NULL;` מפילה כאן."""
    import re
    source = MONITOR_C.read_text(encoding="utf-8")
    offenders = re.findall(r"(?:kbdAddEvent|ptrAddEvent)\s*=\s*NULL", source)
    assert offenders == [], offenders



# --- שומרי-מקור (#839) — רצים בכל תחנה, גם בלי gcc ---------------------------
#
# כמו `test_view_only_source_never_assigns_null_input_callbacks`: לא תחליף
# להרצה במעבדה, אלא רשת-ביטחון לרגרסיה שמישהו יעשה בווינדוס בלי לקמפל.


def _source() -> str:
    return MONITOR_C.read_text(encoding="utf-8")


def test_source_gates_rfb_on_the_secret_not_on_none():
    """‏authPasswdData לא-NULL + passwordCheck משלנו = None אינו מוצע.
    בקרה שלילית: מחיקת שתי השורות מחזירה את 5900 הפתוח."""
    c = _source()
    assert "screen->authPasswdData = secret;" in c
    assert "screen->passwordCheck = secret_check;" in c
    assert "--secret-file" in c
    assert "--allow-from" in c
    assert "hmac_sha256" in c
    assert "RFB_CLIENT_REFUSE" in c
    builder = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert "hmac_sha256.c" in builder


def test_source_uses_hmac_not_the_raw_secret():
    """התשובה היא HMAC(secret, challenge), לא memcmp לסוד עצמו.
    בקרה שלילית: החזרת ההשוואה הגולמית מפילה את test_hmac למטה."""
    c = _source()
    assert "hmac_sha256(secret, SECRET_LEN" in c
    assert "authChallenge" in c
    assert "response[i] ^ secret[i]" not in c


def test_source_refuses_a_peer_that_is_not_the_server():
    c = _source()
    assert "peer_is_server" in c
    assert "getpeername" in c
    assert "not the server" in c


def test_source_never_exits_from_a_client_callback():
    """‏emit_event רץ מתוך callback של LibVNCServer; ‏fatal() שם היה
    קריסה מהצפת אירועים."""
    c = _source()
    assert 'fatal("write /dev/uinput")' not in c


def test_source_cleans_clients_before_destroying_uinput():
    c = _source()
    assert c.index("rfbScreenCleanup(screen);") < c.index("UI_DEV_DESTROY);")


def test_source_releases_keys_when_a_client_goes_and_reaps_power_children():
    c = _source()
    assert "clientGoneHook = client_gone" in c
    assert "signal(SIGCHLD, SIG_IGN)" in c


CC_ONLY = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    why="gcc for hmac_sha256.c unit test",
)


@CC_ONLY
def test_hmac_sha256_c_matches_the_known_vector(tmp_path):
    """ה-C והפייתון מסכימים על אותו וקטור. בלי libvncserver — רק hmac_sha256.c."""
    known = "a5ce9cbf7c63cbedf3403e594d04b0ef"

    driver = tmp_path / "hmac_drv.c"
    driver.write_text(
        '#include "hmac_sha256.h"\n'
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    unsigned char key[16] = {"
        + ",".join(str(b) for b in bytes.fromhex(SECRET))
        + "};\n"
        "    unsigned char msg[16] = {"
        + ",".join(str(b) for b in CHALLENGE)
        + "};\n"
        "    unsigned char out[32];\n"
        "    int i;\n"
        "    hmac_sha256(key, 16, msg, 16, out);\n"
        "    for (i = 0; i < 16; i++) printf(\"%02x\", out[i]);\n"
        "    printf(\"\\n\");\n"
        "    return 0;\n"
        "}\n"
    )
    binary = tmp_path / "hmac_drv"
    cc = shutil.which("cc") or shutil.which("gcc")
    build = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(binary),
         str(driver), str(HMAC_C), "-I", str(REPO / "agent")],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True,
                         timeout=10, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == known


@NATIVE
def test_wrong_peer_is_closed_before_the_handshake(tmp_path):
    """peer שאינו --allow-from נסגר מיד. בקרה שלילית: מחיקת RFB_CLIENT_REFUSE
    נותנת באנר RFB לזר."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, allow_from="10.0.0.1")
    try:
        sock = monitor.connect()
        sock.settimeout(3)
        with sock:
            try:
                sock.recv(64)
            except OSError:
                pass
        assert monitor.alive()
    finally:
        text = monitor.stop()
    assert "not the server" in text, text


@NATIVE
def test_secret_file_rotates_after_an_authenticated_client_leaves(tmp_path):
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary)
    before = monitor.secret_file.read_text().strip()
    try:
        with monitor.connect() as sock:
            _handshake(sock)
        deadline = time.monotonic() + 5
        after = before
        while time.monotonic() < deadline:
            after = monitor.secret_file.read_text().strip()
            if after != before:
                break
            time.sleep(0.05)
        assert after != before, after
        assert len(after) == 32
    finally:
        text = monitor.stop()
    assert "rotated session secret" in text, text


# --- ‏#949: מצביע מוחלט -----------------------------------------------------
#
# הדפדפן שולח PointerEvent עם מיקום **מוחלט** בפריימבאפר (‏`monitor.js`,
# ‏`fbCoords`). עד #949 ‏`monitor.c` הפך אותו לתזוזה יחסית (‏EV_REL) על התקן
# "עכבר", והסמן של הגואי — שמצטבר בנפרד, עם gain וגבולות משלו — סטה ממנו:
# "העכבר קופץ" (נדב, על מחשב הבנייה, 17/09). התיקון: ההתקן מצהיר ABS_X/ABS_Y
# בטווח 0..w-1 / 0..h-1, וכל PointerEvent (x,y) נכתב כ-ABS_X=x, ABS_Y=y.
#
# הראיה כאן היא ההתקן **בקרנל**, לא הקוד: קוראים את `/dev/input/eventN`
# שהמוניטור יצר, שואלים אותו (‏EVIOCGABS) על הטווח שהצהיר, ומקבלים ממנו את
# האירועים שהמוניטור הזריק. דורש `/dev/uinput` כתיב — כלומר root במעבדה.

import evdev_lab

UINPUT_NAME = "ImageCtl remote monitor"

WITH_UINPUT = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("libvncserver", _pkgconfig("libvncserver")),
    ("/dev/uinput (root)", evdev_lab.uinput_available()),
    posix=True,
    why="uinput של המוניטור נבדק במעבדה בלבד, כ-root",
)


def _open_monitor_input_device() -> int:
    node = evdev_lab.find_event_device(UINPUT_NAME)
    return os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)


@WITH_UINPUT
def test_input_device_is_an_absolute_pointer_over_the_framebuffer(tmp_path):
    """ההתקן שנוצר עם `--input` הוא ABS, לא REL, והטווח שלו הוא מידות ה-fb.
    בקרה שלילית: על main שלפני #949 ההתקן מצהיר EV_REL ואין לו EV_ABS."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, extra=("--input",))
    try:
        monitor.connect().close()                  # מאזין = ההתקן כבר נוצר
        fd = _open_monitor_input_device()
        try:
            assert evdev_lab.has_bit(fd, 0, evdev_lab.EV_ABS), "no EV_ABS on the device"
            assert not evdev_lab.has_bit(fd, 0, evdev_lab.EV_REL), "still a relative device"
            assert evdev_lab.has_bit(fd, evdev_lab.EV_ABS, evdev_lab.ABS_X)
            assert evdev_lab.has_bit(fd, evdev_lab.EV_ABS, evdev_lab.ABS_Y)
            assert evdev_lab.has_bit(fd, evdev_lab.EV_KEY, evdev_lab.BTN_LEFT)
            # לא מסך מגע: native-gui מצייר סמן רק להתקן מוחלט בלי BTN_TOUCH.
            assert not evdev_lab.has_bit(fd, evdev_lab.EV_KEY, evdev_lab.BTN_TOUCH)
            _, min_x, max_x = evdev_lab.absinfo(fd, evdev_lab.ABS_X)
            _, min_y, max_y = evdev_lab.absinfo(fd, evdev_lab.ABS_Y)
            assert (min_x, max_x) == (0, WIDTH - 1), (min_x, max_x)
            assert (min_y, max_y) == (0, HEIGHT - 1), (min_y, max_y)
        finally:
            os.close(fd)
    finally:
        text = monitor.stop()
    assert "input enabled through" in text, text


@WITH_UINPUT
def test_pointer_event_lands_as_the_same_absolute_position(tmp_path):
    """‏PointerEvent ב-(x,y) → ‏`EV_ABS ABS_X=x, ABS_Y=y` על ההתקן, וכפתור
    שמאלי → ‏BTN_LEFT; מיקום מעבר לקצה נצמד לפיקסל האחרון (#850). בקרה
    שלילית: הקוד הישן מזריק EV_REL (הפרשים) ואף ABS_X אינו מגיע."""
    binary = _build(tmp_path)
    monitor = Monitor(tmp_path, binary, extra=("--input",))
    try:
        sock = monitor.connect()
        fd = _open_monitor_input_device()           # לפני הקלט: evdev אינו שומר לקורא שטרם פתח
        try:
            with sock:
                width, height = _handshake(sock)
                assert (width, height) == (WIDTH, HEIGHT)
                _pointer(sock, 0, 17, 9)
                _pointer(sock, 1, 40, 20)           # לחיצה שמאלית ב-(40,20)
                _pointer(sock, 0, WIDTH + 5, HEIGHT + 5)
                events = evdev_lab.read_events(fd, timeout=2.0)
        finally:
            os.close(fd)
    finally:
        text = monitor.stop()

    EV_ABS, EV_KEY, EV_REL = evdev_lab.EV_ABS, evdev_lab.EV_KEY, evdev_lab.EV_REL
    ABS_X, ABS_Y, BTN_LEFT = evdev_lab.ABS_X, evdev_lab.ABS_Y, evdev_lab.BTN_LEFT
    relative = [e for e in events if e[0] == EV_REL]
    assert relative == [], f"relative motion injected: {relative}"
    positions = [(c, v) for t, c, v in events if t == EV_ABS]
    assert positions == [
        (ABS_X, 17), (ABS_Y, 9),
        (ABS_X, 40), (ABS_Y, 20),
        (ABS_X, WIDTH - 1), (ABS_Y, HEIGHT - 1),
    ], f"events: {events}\nlog: {text}"
    buttons = [(c, v) for t, c, v in events if t == EV_KEY]
    assert buttons == [(BTN_LEFT, 1), (BTN_LEFT, 0)], buttons
