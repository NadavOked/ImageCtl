"""מפתח ה-host של dropbear בתחנה, כפי שהסוכן מדווח ב-hello (‏#1080).

אין סוד מכונה יציב ב-initramfs, ולכן המפתח נוצר מחדש בכל אתחול. הסמכות
אינה הקובץ על התחנה אלא מה שעבר ב-hello: השרת שומר אותו מגורסת (כמו
``machine_inventory``) וכותב ``<data_dir>/ssh/known_hosts`` — שורה אחת
ל-IP. כל SSH שהשרת פותח לתחנה משתמש בקובץ הזה עם
``StrictHostKeyChecking=yes``. שדה חסר (סוכן ישן, או SSH כבוי) נזנח
ואינו דורס גרסה קודמת.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import sqlite3
import struct
import threading
from pathlib import Path

from .db import _settle, _write_lock, now_iso, writing

#: ``ssh-ed25519`` + 32 בייטי מפתח, כפי ש-RFC 4253 מקודד.
_ALGO = b"ssh-ed25519"
_BLOB_LEN = 4 + len(_ALGO) + 4 + 32
_FP_RE = re.compile(r"^SHA256:[A-Za-z0-9+/]+$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/]+=*$")
_BOOT_LIMIT = 64
_PUBKEY_LIMIT = 200

_known_hosts_lock = threading.Lock()


def fingerprint_of(pubkey: str) -> str | None:
    """‏SHA256 OpenSSH של ה-blob, או ``None`` כשהשורה אינה מפתח."""
    parts = pubkey.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or not _B64_RE.match(parts[1]):
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError:
        return None
    if len(blob) != _BLOB_LEN:
        return None
    algo_len = struct.unpack(">I", blob[:4])[0]
    if blob[4:4 + algo_len] != _ALGO:
        return None
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def well_formed(value: object) -> dict | None:
    """הצורה הקנונית, או ``None`` כשהשדה אינו מה שהממשק מגדיר.

    ‏``boot_id`` רשות — בלי, אי אפשר להבחין בין אתחול לבין שינוי בתוך
    אותו אתחול, והחיווי הכתום אינו נדלק (עיקרון 5: בלי ראיה לא מאשימים).
    טביעה שאינה תואמת את ה-pubkey נזנחת: אחרת הכרטיס היה מציג מפתח
    וה-``known_hosts`` היה מצמיד אחר.
    """
    if not isinstance(value, dict):
        return None
    if value.get("type") != "ed25519":
        return None
    pubkey = value.get("pubkey")
    fingerprint = value.get("fingerprint")
    if not isinstance(pubkey, str) or not isinstance(fingerprint, str):
        return None
    pubkey = pubkey.strip()[:_PUBKEY_LIMIT]
    fingerprint = fingerprint.strip()
    computed = fingerprint_of(pubkey)
    if computed is None or not _FP_RE.match(fingerprint) or fingerprint != computed:
        return None
    parts = pubkey.split()
    pubkey = f"{parts[0]} {parts[1]}"
    boot_id = value.get("boot_id")
    if boot_id is not None:
        if not isinstance(boot_id, str):
            return None
        boot_id = boot_id.strip()[:_BOOT_LIMIT] or None
    return {"type": "ed25519", "fingerprint": fingerprint,
            "pubkey": pubkey, "boot_id": boot_id}


def _text(hostkey: dict) -> str:
    return json.dumps(hostkey, sort_keys=True, ensure_ascii=False)


def _public(hostkey: dict) -> dict:
    return {k: hostkey[k] for k in ("type", "fingerprint", "pubkey")}


def _flags(current: dict, previous: dict | None) -> dict:
    """כתום רק כשיש ראיה חיובית שזה אותו אתחול. בלי boot_id — אפור."""
    if previous is None:
        return {"changed_in_boot": False, "new_from_reboot": False}
    same_boot = (
        current.get("boot_id")
        and previous.get("boot_id")
        and current["boot_id"] == previous["boot_id"]
    )
    key_changed = current["fingerprint"] != previous["fingerprint"]
    return {
        "changed_in_boot": bool(same_boot and key_changed),
        "new_from_reboot": bool(key_changed and not same_boot),
    }


def record(conn: sqlite3.Connection, mac: str, hostkey: dict) -> bool:
    """שומר גרסה חדשה כשה-JSON הקנוני השתנה. מחזיר האם נכתבה שורה."""
    text = _text(hostkey)
    row = conn.execute(
        "SELECT hostkey_json FROM machine_ssh_hostkey WHERE mac = ?"
        " ORDER BY id DESC LIMIT 1", (mac,)
    ).fetchone()
    if row is not None and row["hostkey_json"] == text:
        return False
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO machine_ssh_hostkey (mac, seen_at, hostkey_json)"
            " VALUES (?, ?, ?)", (mac, now_iso(), text))
    return True


def _row(row: sqlite3.Row, previous: dict | None) -> dict:
    hostkey = json.loads(row["hostkey_json"])
    return {"ssh_hostkey": _public(hostkey), "seen_at": row["seen_at"],
            **_flags(hostkey, previous)}


def latest(conn: sqlite3.Connection, mac: str) -> dict | None:
    rows = conn.execute(
        "SELECT hostkey_json, seen_at FROM machine_ssh_hostkey WHERE mac = ?"
        " ORDER BY id DESC LIMIT 2", (mac,)
    ).fetchall()
    if not rows:
        return None
    prev = json.loads(rows[1]["hostkey_json"]) if len(rows) > 1 else None
    return _row(rows[0], prev)


def latest_all(conn: sqlite3.Connection) -> dict[str, dict]:
    """המפתח הנוכחי של כל מכונה שדיווחה, לפי MAC — לכרטיסי המכונות."""
    rows = conn.execute(
        "SELECT mac, hostkey_json, seen_at FROM machine_ssh_hostkey"
        " ORDER BY id DESC"
    ).fetchall()
    latest_row: dict[str, sqlite3.Row] = {}
    previous: dict[str, dict] = {}
    for row in rows:
        mac = row["mac"]
        if mac not in latest_row:
            latest_row[mac] = row
        elif mac not in previous:
            previous[mac] = json.loads(row["hostkey_json"])
    return {mac: _row(row, previous.get(mac)) for mac, row in latest_row.items()}


def history(conn: sqlite3.Connection, mac: str) -> list[dict]:
    rows = conn.execute(
        "SELECT hostkey_json, seen_at FROM machine_ssh_hostkey WHERE mac = ?"
        " ORDER BY id DESC", (mac,)
    ).fetchall()
    out = []
    for i, row in enumerate(rows):
        prev = json.loads(rows[i + 1]["hostkey_json"]) if i + 1 < len(rows) else None
        out.append(_row(row, prev))
    return out


def known_hosts_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "ssh" / "known_hosts"


def client_opts(data_dir: str | Path) -> list[str]:
    """הדגלים שכל ``ssh`` שהשרת פותח לתחנה חייב לשאת.

    המפתח מה-hello הוא הסמכות: בלי ``yes`` ובלי הקובץ הזה החיבור היה
    מקבל כל מארח, וזה בדיוק מה ש-#1080 סוגר.
    """
    path = known_hosts_path(data_dir)
    return [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={path}",
        "-o", "GlobalKnownHostsFile=/dev/null",
    ]


def _valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return False
    return True


def upsert_known_host(data_dir: str | Path, ip: str, hostkey: dict) -> None:
    """שורה אחת ל-IP: ``<ip> ssh-ed25519 <b64>``. מתעדכן בכל hello."""
    if not isinstance(ip, str) or not _valid_ip(ip):
        return
    parts = hostkey["pubkey"].split()
    line = f"{ip} {parts[0]} {parts[1]}"
    path = known_hosts_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _known_hosts_lock:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        kept = [ln for ln in existing.splitlines()
                if ln.split()[:1] != [ip] and ln.strip()]
        kept.append(line)
        # כתיבה אטומית: קריסה באמצע write_text הייתה משאירה known_hosts קטוע —
        # וכל ssh מהשרת לתחנה היה נופל על "host key verification failed"
        # (ממצא סקירת Nemotron-3 Ultra, 18/09).
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(path)
