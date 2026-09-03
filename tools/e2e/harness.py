"""תשתית הסימולציה: לקוח HTTP, בדיקות, המתנות, והקמת השרת.

הסימולציה מדברת עם השרת אך ורק בממשקים המתועדים (docs/interfaces.md):
ממשק 2 (hello), 3 (התשובה), 4 (התקדמות) ו-7 (זרם הקבצים). אין כאן
ייבוא של קוד השרת מלבד יצירת משתמש ההתחלה — כמו בהתקנה אמיתית.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORT = 8199
BASE = f"http://127.0.0.1:{PORT}"
#: אף אחד לא מאזין שם — זה "השרת השקט" של תרחיש ברירת המחדל.
DEAD_BASE = f"http://127.0.0.1:{PORT + 1}"

#: הפורט שהשרת של הסימולציה משדר עליו — ‏30199 ו-30200, ולא 9000/9001.
#: זה **לא** נוחות: השרת שהסימולציה מרימה הוא שרת אמיתי, ובמעבדה מותקן
#: `udp-sender` אמיתי ומחוברות אליה שתי מכונות פיזיות. שידור על 9000
#: הוא שידור על וילן ההפצה, ויתום ששרד ריצה שנקטעה מפיל את ההפצה הבאה
#: וזה נראה כמו תקלת רשת (#79, ‏#156, ‏#201). מספר גבוה שאינו מכיל "900"
#: — כדי ש-`ss -lunp | grep :900` יישאר בדיקה שאין בה דו-משמעות.
SENDER_PORTBASE = 30199

ESP_GUID = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
WINDOWS_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"

GB256 = 256060514304
GB500 = 500107862016

BUILD_MAC = "aa:bb:cc:00:00:10"
CLONER_MAC = "aa:bb:cc:00:00:20"
CLASS_MACS = ["00:00:5e:07:1a:c4", "00:00:5e:07:1a:c5",
              "00:00:5e:07:1a:c6", "00:00:5e:07:1a:c7"]
UNKNOWN_MAC = "de:ad:be:ef:00:01"

ADMIN = {"username": "noc", "password": "sim-pass-1234"}
DEPLOY = {"username": "madrich", "password": "sim-deploy-99"}

#: תוכן המחיצות של שני האימג'ים — קטן, אבל עובר את אותו מסלול בדיוק.
FILES_A = {"p1.esp.pcl.zst": b"esp-a-payload" * 300,
           "p3.windows.pcl.zst": b"windows-a-payload" * 900}
FILES_B = {"p1.esp.pcl.zst": b"esp-b-payload" * 250,
           "p3.windows.pcl.zst": b"windows-b-payload" * 1400}

passed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}  {detail}")
        raise SystemExit(f"FAILED: {label} {detail}")


def wait_until(fn, label: str, timeout: float = 15.0, detail=None):
    """ממתין עד ש-fn מחזירה ערך אמת — ומחזיר אותו. המכונות רצות
    בתהליכונים, ולכן רוב הבדיקות הן 'בסוף זה יקרה', לא 'זה קרה עכשיו'."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            check(label, True)
            return value
        # בלי ההשהיה הלולאה מפציצה את השרת ומרוקנת פורטים בווינדוס
        # (TIME_WAIT) — וה-hello של המכונות מתעכב עד שהטיימרים משתבשים.
        time.sleep(0.1)
    extra = f" | {detail()}" if detail else ""
    check(label, False, f"לא קרה תוך {timeout} שניות{extra}")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def disk(dev: str, size_bytes: int, serial: str, port: int | None = None) -> dict:
    """דיסק כפי שהסוכן מדווח אותו (ממשק 2).

    ‏`port` הוא החריץ הפיזי (ataN). מכונה בלי חריצים מדמה סוכן ישן או
    VM עם SCSI — השדה פשוט לא נשלח, וזה חייב להמשיך לעבוד.
    """
    entry = {"dev": dev, "size_bytes": size_bytes, "model": "SIM SSD",
             "serial": serial, "removable": False, "scheme": "gpt",
             "has_data": True}
    if port is not None:
        entry["port"] = port
    return entry


class Client:
    """לקוח HTTP קטן ששומר cookie — כמו דפדפן, בלי דפדפן."""

    def __init__(self, base: str = BASE):
        self.base = base
        self.cookie = None

    def request(self, method, path, body=None, ctype="application/json", raw=False):
        data = body if raw else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", ctype)
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                set_cookie = response.headers.get("Set-Cookie")
                if set_cookie:
                    self.cookie = set_cookie.split(";")[0]
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def json(self, method, path, body=None):
        status, payload = self.request(method, path, body)
        try:
            return status, json.loads(payload)
        except ValueError:
            return status, payload


def hello(client: Client, mac: str, disks: list[dict]):
    """ממשק 2 — בדיוק מה שהסוכן האמיתי שולח כשה-initramfs עולה."""
    return client.json("POST", "/api/v1/agent/hello", {
        "schema": 1, "mac": mac, "all_macs": [mac], "ip": "10.99.12.50",
        "hostname_current": None, "uuid": "SIM", "firmware": "uefi",
        "secure_boot": True, "agent_version": "0.1.0", "memory_bytes": 8 << 30,
        "disks": disks,
    })


def make_manifest(source_bytes: int, files: dict[str, bytes]) -> dict:
    """מניפסט ממשק 1 לשני קבצים — ESP קטן ומחיצת windows מתרחבת."""
    esp_name, win_name = sorted(files)          # p1 לפני p3
    return {
        "schema": 1, "family": 256 if source_bytes == GB256 else 500,
        "source_disk_bytes": source_bytes, "min_target_bytes": source_bytes,
        "scheme": "gpt", "sector_size": 512,
        "partitions": [
            {"index": 1, "type_guid": ESP_GUID, "role": "esp", "fs": "vfat",
             "start_sector": 2048, "size_bytes": 104857600,
             "used_bytes": 31457280, "file": esp_name,
             "sha256": sha(files[esp_name]), "expandable": False},
            {"index": 3, "type_guid": WINDOWS_GUID, "role": "windows", "fs": "ntfs",
             "start_sector": 1085440, "size_bytes": source_bytes - 1085440 * 512,
             "used_bytes": 84509376512, "file": win_name,
             "sha256": sha(files[win_name]), "expandable": True},
        ],
        "total_compressed_bytes": sum(len(v) for v in files.values()),
        "compression": "zstd-9",
    }


def start_server(workdir: Path) -> tuple[subprocess.Popen, Path, Path]:
    data_dir, images = workdir / "data", workdir / "images"
    images.mkdir(parents=True)
    # הפלט לקובץ ולא ל-PIPE: המכונות המדומות מייצרות אלפי שורות לוג,
    # ו-PIPE שאיש לא קורא מתמלא — והשרת נחסם על הכתיבה ומפסיק לענות.
    log = (workdir / "server.log").open("wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "server.main", "--server-url", BASE,
         "--data-dir", str(data_dir), "--images", str(images),
         "--host", "127.0.0.1", "--port", str(PORT),
         # ‏#201: השרת הזה אמיתי ומגיע ל-udp-sender אמיתי. בלי הדגל הזה
         # הוא משדר על פורטי ההפצה של הייצור.
         "--sender-portbase", str(SENDER_PORTBASE)],
        cwd=str(REPO), stdout=log, stderr=subprocess.STDOUT,
    )
    return server, data_dir, images


#: תחילת רשומת לוג של uvicorn. כל מה שביניהן שייך לרשומה הקודמת —
#: וזה בדיוק מה שמאפשר לגזור traceback שלם, כולל חריגות משורשרות.
_LOG_RECORD = re.compile(r"(INFO|ERROR|WARNING|CRITICAL|DEBUG)[:\s]")

TRACEBACK_HEAD = "Traceback (most recent call last):"


def server_tracebacks(text: str, limit: int = 3) -> list[str]:
    """ה-traceback-ים ביומן השרת; מוחזרים האחרונים, בסדר הופעתם."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(TRACEBACK_HEAD):
            current = [line]
            blocks.append(current)
        elif current is not None:
            if _LOG_RECORD.match(line):
                current = None          # הרשומה הבאה — סוף ה-traceback
            else:
                current.append(line)
    return ["\n".join(block) for block in blocks[-limit:]]


def server_log_tail(workdir: Path, lines: int = 40) -> str:
    """סוף יומן השרת — ולפניו החריגות שנמצאו בכל אורכו.

    ארבע מכונות דוגמות hello שלוש פעמים בשנייה והמבט-על נדגם עשר פעמים
    בשנייה: היומן מוצף שורות גישה, ולכן 40 השורות האחרונות הן כמעט תמיד
    *אחרי* ה-traceback שגרם לכישלון. כך נראה כשל ב-CI שכל מה שנשאר ממנו
    הוא "החזיר 500" בלי הסבר, ושדרש הרצה חוזרת כדי להיחקר. החריגה היא
    הדבר היחיד שבאמת מסביר, ולכן היא נשלפת בנפרד ומודפסת ראשונה.
    """
    try:
        text = (workdir / "server.log").read_text(errors="replace")
    except OSError:
        return "(אין server.log)"
    parts = []
    tracebacks = server_tracebacks(text)
    if tracebacks:
        parts.append(f"--- חריגות בשרת: {text.count(TRACEBACK_HEAD)} סה\"כ,"
                     f" {len(tracebacks)} האחרונות ---")
        parts.extend(tracebacks)
    parts.append(f"--- {lines} השורות האחרונות של יומן השרת ---")
    parts.append("\n".join(text.splitlines()[-lines:]))
    return "\n".join(parts)


def wait_for_server(timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(BASE + "/console/", timeout=2)
            return True
        except Exception:
            time.sleep(0.4)
    return False
