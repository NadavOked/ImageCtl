"""קצב המולטיקאסט לכל גל — נקבע בשרת לבד (#447 + הכרעת נדב 11/10).

‏`udp-sender --max-bitrate` הוא מספר אחד לכל הזרם. **הכרעת נדב 11/10:**
"אני לא נוגע בזה, והשרת לבד מחליט". לפני כל גל:

    פתיחה = min(START_BITRATE, גג)    גג = IMAGECTL_MAX_BITRATE, ובלעדיו TOP_BITRATE
    (15/10: כשמהירות כרטיס ההפצה נקראה — גג 90% ממנה ופתיחה 60%; ראו `read_link`;
     ולעולם לא מעל 90%/60% ממחשב השיכפול האיטי בגל — `nic_speed`, הנחת 1G כשלא ידוע)

    קצב = המינימום על **מחשבי השיכפול** בגל (#1471, הכרעת נדב 16/10) — לכל
          מכונה (MAC), מהגל האחרון שהיא הייתה בו:
          ‏+STEP_UP      — שידורים חוזרים = 0 **וגם** כל כונן שלו עמד בקצב
                          **וגם** הזרם עצמו רץ ב-KEEP_UP מהקצב לפחות
          ‏× DOWN_FACTOR — שידורים חוזרים מעל LOSS_DOWN_RATIO מהבלוקים
          כמות שהוא    — כל השאר, כולל "לא נמדד" (עיקרון 5)
          מכונה בלי גל כזה (חדשה / הוחלפה) — **כל הגל** בפתיחה (`_from_previous`)
    קצב = min(קצב, הכונן האיטי המוכר × MARGIN)        (#447, למטה)
    קצב = בין FLOOR_RATIO × פתיחה לבין הגג

עלייה רק על **ראיה חיובית** לגל נקי: המונה של udp-sender עצמו נקרא בסוף
הגל (‏`record_stream`) ושווה אפס, ולכל כונן של הגל יש דגימה שעמד בה בקצב.
מונה שלא נקרא, גל שנעצר לפני הסוף, כונן שנכשל או בלי דגימה — אין ראיה,
והקצב נשאר. הזרם עצמו אינו משתנה; רק המספר שעובר ל-`--max-bitrate`.

**הזיכרון של הכוננים (#447).** כאן נשמר מה שכל כונן (דגם + סידורי) באמת
כתב בגלים שהסתיימו, וכונן שידוע שהוא איטי מוריד את הגל אליו:

    תקרת כונן = הקצב של הכונן האיטי המוכר × MARGIN

כונן שאינו מוכר אינו מוריד דבר (לא מנחשים).

שלוש הבחנות שבלעדיהן הלמידה מכיילת את עצמה על שקר:

1. **כונן שעמד בקצב הגל אינו מגביל.** במולטיקאסט כל כונן כותב בקצב הזרם,
   והזרם חסום בתקרה — כונן שכתב 24MB/s תחת 200m "עשה 24" רק כי זה מה
   שנשלח. דגימה כזו אומרת "לפחות התקרה", ולמידה שהייתה לוקחת אותה כקצב
   הייתה מורידה כל גל ב-10% עד אינסוף (ראצ'ט). רק דגימה מתחת ל-`KEEP_UP`
   מתקרת הגל היא ראיה שהכונן הגביל.
2. **הערכת היכולת היא המקסימום, לא האחרון.** ‏`WINDOW` הגלים האחרונים: דגימה
   מוגבלת שווה את הקצב שלה, דגימה שעמדה בקצב שווה את תקרת הגל שלה. אחרי
   שהגל הורד לכונן איטי הוא עומד בקצב — וזו אינה סיבה להוריד שוב, וגם לא
   לחזור לתקרה. בלי אף דגימה מוגבלת בחלון — הכונן אינו מגביל.
3. **ייחוס אינו מושלם, ונאמר כך.** כל הכוננים בגל כותבים בקצב האיטי שבהם,
   ולכן כונן מהיר שישב עם כונן איטי נרשם "מוגבל" גם הוא. גל אחד שבו עמד
   בקצב הגל מנקה אותו, ואחרת הדגימה יוצאת מהחלון אחרי `WINDOW` גלים.

שלושה סייגים נגד הרעלה (סקירת אבטחה, 11/10): כונן מוריד רק אחרי
`MIN_HELD_SAMPLES` גלים שונים שבהם הגביל; נלמד רק מכונן שהיה ברשימת הגל
כשיצא (מכונה + סידורי, כפי שהשרת ראה לפני הגל — לא מה שמדווח בסוף); והקצב
אינו יורד מתחת ל-`FLOOR_RATIO` מקצב הפתיחה — גם לא על שידורים חוזרים.

עיקרון 5: תקרה לא-מספרית — עוברת כמות שהיא, בלי למידה. מתג שאינו "true" —
קצב הפתיחה, קבוע. גל בלי תקרה ידועה, דגימה בלי זמנים, מונה שלא נקרא —
"לא נלמד". וכשל בלמידה עצמה אינו מפיל גל: הוא נרשם ביומן, והגל יוצא
בקצב הפתיחה.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import nic_speed
from .db import _write_lock, get_setting, journal, now_iso, writing

log = logging.getLogger("imagectl.drive_rates")

#: מתג ההגדרה (טבלת `settings`). ברירת המחדל "true" (`db.DEFAULT_SETTINGS`).
SETTING = "learned_rate_enabled"
#: כמה גלים אחרונים של כל כונן נחשבים.
WINDOW = 5
#: שולי ביטחון: משדרים מעט מתחת למה שהכונן האיטי הוכיח שהוא כותב.
MARGIN = 0.9
#: מתחת לאיזה חלק מתקרת הגל דגימה נחשבת "הכונן הגביל". ‏udp-sender מגיע
#: ל-95.7% מהתקרה בתשלום (191.4Mbit/s מתוך 200m, ‏handoff 06/09); הממוצע
#: של מגירה כולל גם את ההמתנות בין המחיצות, ולכן הסף נמוך מזה.
KEEP_UP = 0.8
#: דגימה קצרה או קטנה מדי היא רעש של התחלה וסוף, לא קצב.
MIN_SAMPLE_BYTES = 10 ** 9
MIN_SAMPLE_S = 60
#: כמה גלים שונים שבהם הכונן הגביל נדרשים לפני שהוא מוריד גל. דגימה אחת —
#: כונן חם, גל חריג, או דיווח כוזב של מכונה אחת — אינה תכונה של כונן.
MIN_HELD_SAMPLES = 2
#: רצפה לקצב, כחלק מקצב הפתיחה (600m → 150m; ‏200m → 50m). מה שהלמידה
#: יכולה לעשות במקרה הגרוע — דגימות מורעלות, כונן גוסס, מקבל שמבקש שידור
#: חוזר על כל בלוק — חסום כאן, ולא משתק חדר שלם בקצב של 1m.
FLOOR_RATIO = 0.25

#: קצב הפתיחה, בביטים לשנייה: ‏600m — נמדד על ברזל ב-06/09 (מ.19,
#: ‏`docs/verification-register.md`): ‏61.06MB/s לשני מקבלים, ‏re-xmits=0.
#: ⚠️ המדידה ההיא הייתה **בלי כתיבה לדיסק** — לכן הוא נקודת פתיחה, לא
#: הבטחה; הירידה על שידורים חוזרים והכוננים האיטיים (#447) הם מה שמגן.
START_BITRATE = 600_000_000
#: הגג כשאין `IMAGECTL_MAX_BITRATE`: קו של 1Gb/s, פחות מקום ל-ACK-ים של
#: udpcast ולשאר התעבורה (‏udp-sender מגיע ל-95.7% מהתקרה, ‏handoff 06/09).
#: ‏`IMAGECTL_MAX_BITRATE`, כשהוגדר, מחליף אותו — כלפי מטה או מעלה.
TOP_BITRATE = 900_000_000
#: צעד העלייה אחרי גל נקי: ‏600→900 בשלושה גלים נקיים.
STEP_UP = 100_000_000
#: הירידה אחרי גל עם שידורים חוזרים: רבע מהקצב.
DOWN_FACTOR = 0.75
#: מעל איזה חלק מהבלוקים שידורים חוזרים מורידים. ‏udp-sender מדפיס את
#: היחס הזה בעצמו ברזולוציה של 0.1% — מתחת לסף הוא מדפיס "0.0%". בין
#: אפס לסף הקצב נשאר: לא נקי מספיק כדי לעלות, לא רע מספיק כדי לרדת.
#: ⚠️ ניחוש מנומק, לא מדידה — לכייל על ברזל.
LOSS_DOWN_RATIO = 0.001
#: גודל הבלוק של udpcast (‏`--blocksize`; ברירת המחדל לפי ה-man page, והשרת
#: אינו מעביר אותו) — המכנה של `LOSS_DOWN_RATIO`. לא נמדד על ברזל.
UDPCAST_BLOCK = 1456
#: כמה גלים אחרונים סורקים כדי למצוא את הגל האחרון של כל מכונה (#1471).
MEMORY_SCAN = 50

#: ‏הכרעת נדב 15/10: "הכי מהר שאפשר, והשרת מגביל לפי כרטיס הרשת שלו".
#: כשמהירות הקישור של כרטיס ההפצה נקראה (‏`read_link`), במקום 600m/900m:
#:     גג    = ‏LINK_CEILING_PER_MBPS × מהירות   (90% — מקום ל-ACK ולשידור חוזר)
#:     פתיחה = ‏LINK_START_PER_MBPS × מהירות    (60% — שני שלישים מהגג)
#: ב-ביטים לשנייה לכל Mb/s. על 1GbE זה **בדיוק** 900m/600m — ההתנהגות של
#: היום; על 10GbE ‏9000m/6000m. ‏`IMAGECTL_MAX_BITRATE`, כשהוגדר, נשאר גג
#: — הנמוך מבין השניים. הרצפה **אינה** גדלה עם הקישור (`_floor`).
LINK_CEILING_PER_MBPS = TOP_BITRATE // 1000
LINK_START_PER_MBPS = START_BITRATE // 1000
SYS_NET = Path("/sys/class/net")
#: דרייברים של כרטיס וירטואלי: המהירות שלהם היא מה שהמארח **מצהיר**
#: (‏vmxnet3 תמיד 10000), לא הקו הפיזי — עיקרון 5א. בלי ראיה — ההתנהגות
#: של היום.
VIRTUAL_DRIVERS = frozenset({"vmxnet3", "virtio_net", "hv_netvsc", "xen_netfront"})


def read_link(interface: str | None, sys_net: Path = SYS_NET) -> dict:
    """מהירות הקישור של כרטיס ההפצה מ-`/sys/class/net/<if>/speed`.

    ‏`{"interface", "speed_mbps", "why"}`: ‏`speed_mbps` מספר חיובי ו-`why`
    ‏None — נקרא; אחרת `speed_mbps` ‏None ו-`why` אומר למה (עיקרון 5: "לא
    נקרא" אינו 1GbE): ‏`no_interface` · ‏`absent` (אין כרטיס/קובץ) ·
    ‏`unreadable` (‏OSError — לינוקס מחזיר EINVAL כשהקישור למטה) · ‏`unknown`
    ‏(‏-1 או לא מספר) · ‏`virtual` (‏`VIRTUAL_DRIVERS`). ‏bond — הסלייב
    האיטי: זרם מולטיקאסט אחד יוצא בסלייב אחד. לעולם אינו זורק."""
    view = {"interface": interface or None, "speed_mbps": None, "why": None}
    if not interface:
        return {**view, "why": "no_interface"}
    try:
        mbps, why = _link_speed(Path(sys_net), interface)
    except OSError:
        mbps, why = None, "unreadable"
    return {**view, "speed_mbps": mbps, "why": why}


def _link_speed(sys_net: Path, name: str) -> tuple[int | None, str | None]:
    entry = sys_net / name
    if not entry.is_dir():
        return None, "absent"
    slaves = entry / "bonding" / "slaves"
    if slaves.exists():
        got = [_link_speed(sys_net, s) for s in slaves.read_text().split()]
        if not got:
            return None, "unknown"
        why = next((w for m, w in got if m is None), None)
        return (None, why) if why else (min(m for m, _ in got), None)
    uevent = entry / "device" / "uevent"
    if uevent.exists() and any(
            line.strip() in {f"DRIVER={d}" for d in VIRTUAL_DRIVERS}
            for line in uevent.read_text().splitlines()):
        return None, "virtual"
    speed = entry / "speed"
    if not speed.exists():
        return None, "absent"
    try:
        mbps = int(speed.read_text().strip())
    except ValueError:
        return None, "unknown"
    return (mbps, None) if mbps > 0 else (None, "unknown")


def _link_bounds(link: dict | None) -> tuple[int, int] | None:
    """(גג, פתיחה) בביטים מהקישור; ‏None — המהירות לא נקראה."""
    mbps = (link or {}).get("speed_mbps")
    if not isinstance(mbps, int) or isinstance(mbps, bool) or mbps <= 0:
        return None
    return mbps * LINK_CEILING_PER_MBPS, mbps * LINK_START_PER_MBPS

_BITRATE = re.compile(r"^(\d+(?:\.\d+)?)([kmg]?)$")
_UNIT = {"": 1, "k": 10 ** 3, "m": 10 ** 6, "g": 10 ** 9}


def parse_bitrate(raw: object) -> int | None:
    """‏`--max-bitrate` של udpcast בביטים לשנייה; ‏None = אינו מספר חיובי."""
    if not isinstance(raw, str):
        return None
    match = _BITRATE.match(raw.strip().lower())
    if match is None:
        return None
    bits = int(float(match.group(1)) * _UNIT[match.group(2)])
    return bits if bits > 0 else None


def enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING) == "true"


def _limit_of(conn: sqlite3.Connection, serial: str,
              model: str | None) -> tuple[int | None, int | None, bool]:
    """(התקרה בביטים שהכונן מצדיק, הקצב המוגבל שנמדד לו, האם מוכר בכלל).

    דגימה שהכונן הגביל בה מצדיקה את הקצב שלה × `MARGIN`; דגימה שעמד בה
    בקצב מצדיקה את תקרת הגל שלה **כמות שהיא** — הוא הוכיח שהוא עומד בה,
    ומקדם ביטחון עליה היה הראצ'ט מהדלת האחורית. ההערכה היא המקסימום
    בחלון. בלי דגימה מוגבלת בחלון — (None, None, מוכר?)."""
    rows = conn.execute(
        "SELECT rate_bps, cap_bps FROM drive_rates WHERE serial = ? AND model IS ?"
        " ORDER BY id DESC LIMIT ?", (serial, model, WINDOW),
    ).fetchall()
    def held_back(r) -> bool:
        return r["rate_bps"] < KEEP_UP * r["cap_bps"]
    held = [r["rate_bps"] for r in rows if held_back(r)]
    if len(held) < MIN_HELD_SAMPLES:
        return None, None, bool(rows)
    bits = max(int(r["rate_bps"] * 8 * MARGIN) if held_back(r) else r["cap_bps"] * 8
               for r in rows)
    return bits, max(held), True


def _fmt(bits: int) -> str:
    return f"{max(1, bits // 10 ** 6)}m"


def _all_kept_up(conn: sqlite3.Connection, session_id: str, roster: list) -> bool:
    """ראיה חיובית שכל כונן של הגל עמד בקצב: לכל אחד דגימה מהגל הזה, והיא
    לפחות `KEEP_UP` מתקרת הגל. כונן בלי דגימה (נכשל, גל קצר) — אין ראיה."""
    serials = {d.get("serial") for d in roster if isinstance(d, dict)}
    if not serials or None in serials:
        return False
    kept = {r["serial"] for r in conn.execute(
        "SELECT serial, rate_bps, cap_bps FROM drive_rates WHERE session_id = ?",
        (session_id,)) if r["rate_bps"] >= KEEP_UP * r["cap_bps"]}
    return serials <= kept


def _next_from(conn: sqlite3.Connection, session_id: str, past: dict, stream: dict,
               rate: int, roster: list) -> tuple[int, str, dict]:
    """(הקצב שהגל הבא יורש מהגל הזה, ‏basis, ‏previous) — לפי מה שהגל הזה
    עצמו הוכיח: ‏`up`/‏`down`/‏`held`/‏`unmeasured` (ראו `choose`)."""
    loss, sent = stream.get("loss_blocks"), stream.get("sent_bytes")
    bps, secs = stream.get("stream_bps"), stream.get("stream_s")
    previous = {"session_id": session_id, "max_bitrate": past["max_bitrate"],
                "loss_blocks": loss, "sent_bytes": sent, "stream_bps": bps}
    if not isinstance(loss, int) or not isinstance(sent, int) or sent <= 0:
        return rate, "unmeasured", previous         # עיקרון 5: לא נמדד ≠ נקי
    if loss > LOSS_DOWN_RATIO * -(-sent // UDPCAST_BLOCK):
        return int(rate * DOWN_FACTOR), "down", previous
    if loss != 0 or not _all_kept_up(conn, session_id, roster):
        return rate, "held", previous
    # התנאי השני לעלייה: הזרם עצמו רץ בקצב הגל. ‏`bytes_written` של כונן
    # נספר אחרי פריסת הדחיסה — כונן שהאט את הזרם עלול להיראות "עמד".
    if (not isinstance(bps, int) or not isinstance(secs, (int, float))
            or secs < MIN_SAMPLE_S):
        return rate, "unmeasured", previous
    if bps < KEEP_UP * rate / 8:
        return rate, "held", previous
    return rate + STEP_UP, "up", previous


def _from_previous(conn: sqlite3.Connection, drives: list[dict], start: int,
                   exclude: str | None) -> tuple[int, str, dict | None]:
    """(הקצב לפני הכוננים והגבולות, ‏basis, הגל הקודם) — **לפי מחשבי השיכפול**.

    ‏#1471, הכרעת נדב 16/10: בחדר מחליפים מגירות בכל גל, ולכן "הגל האחרון
    שכל הכוננים האלה היו בו" כמעט לא קיים, וכמעט כל גל יצא בפתיחה (60%).
    הזיכרון הוא לכל **מכונה** (‏MAC — מכונה שהוחלפה היא MAC חדש):

        לכל MAC בגל: הגל האחרון (לא ישיר, עם קצב) שהמכונה הייתה ב-`roster`
                     שלו, והקצב שהגל ההוא מוריש (`_next_from`: ‏+STEP_UP על
                     גל נקי, ×DOWN_FACTOR על שידורים חוזרים, אחרת כמות שהוא)
        קצב = המינימום על המכונות; ‏`previous` = הגל של המכונה שקבעה, ו-`mac` שלה
        מכונה אחת בלי גל כזה (או כונן בלי MAC) → **כל הגל** בפתיחה

    הגל יוצא, נמדד, ובפעם הבאה המכונה החדשה כבר מוכרת. ההגנות לכל כונן
    אינן משתנות: הכונן האיטי המוכר (`_limit_of`) מוריד אחרי זה, בדיקת
    הכתיבה בסוכן פוסלת מגירה איטית, ו"עלייה" עדיין דורשת שכל כונן של הגל
    הקודם עמד בקצב והזרם רץ בו (`_next_from`)."""
    macs = {d.get("mac") for d in drives}
    if not macs or None in macs:
        return start, "start", None
    found: dict[str, tuple[int, str, dict]] = {}
    for row in conn.execute(
            "SELECT session_id, decision, stream FROM wave_rates"
            " WHERE session_id IS NOT ? ORDER BY rowid DESC LIMIT ?",
            (exclude, MEMORY_SCAN)):
        try:
            past = json.loads(row["decision"])
            stream = json.loads(row["stream"]) if row["stream"] else {}
        except (TypeError, ValueError):
            continue
        roster = past.get("roster") or []
        rate = parse_bitrate(past.get("max_bitrate"))
        if past.get("direct") or rate is None:
            continue
        hits = (macs - found.keys()) & {d.get("mac") for d in roster if isinstance(d, dict)}
        if not hits:
            continue
        outcome = _next_from(conn, row["session_id"], past, stream, rate, roster)
        for mac in hits:
            found[mac] = outcome
        if found.keys() >= macs:
            break
    if found.keys() < macs:
        return start, "start", None                 # מכונה שלא נראתה — פתיחה
    mac, (bits, basis, previous) = min(found.items(), key=lambda kv: (kv[1][0], kv[0]))
    return bits, basis, {**previous, "mac": mac}


def choose(conn: sqlite3.Connection, drives: list[dict],
           ceiling_raw: str | None, session_id: str | None = None,
           direct: bool = False, link: dict | None = None) -> dict:
    """הקצב לגל של `drives` (‏`serial`, ‏`model`, ‏`mac`, ‏`port`) — קריאה בלבד.

    ‏`max_bitrate` הוא מה שעובר ל-udp-sender, ו-`basis` אומר למה: ‏`start`
    (מכונה בגל שאין לה גל קודם — #1471) · ‏`up` (הגל הקודם של המכונה שקבעה
    נקי — ב-`previous`, עם ה-`mac` שלה) ·
    ‏`down` (שידורים חוזרים בגל הקודם) · ‏`held` (הגל הקודם לא הוכיח שאפשר
    לעלות) · ‏`unmeasured` (המונה של הגל הקודם לא נקרא — נשאר) · ‏`learned`
    (כונן מוכר הוריד, והוא ב-`by`) · ‏`ceiling` (הגג עצר) · ‏`disabled` (המתג
    כבוי — הפתיחה, קבועה) · ‏`bad_ceiling` (התקרה אינה מספר; עוברת כמות
    שהיא). ‏`session_id` — הגל שבוחרים לו; הוא אינו "הגל הקודם" של עצמו.

    ‏`direct` — מחשב בנייה משדר (#715): אותה פתיחה, אותו גג ואותה ירידה
    לכונן איטי מוכר — אבל **בלי זיכרון ובלי עלייה**. המונה של udp-sender
    נמצא במחשב הבנייה ולא בשרת, ובלעדיו אין ראיה לגל נקי (עיקרון 5); וגל
    כזה אינו "הגל הקודם" של אף גל ואינו מלמד את הכוננים (`record_wave`) —
    המקור עצמו (דיסק חי + דחיסה) יכול להיות האיטי, והייחוס לכוננים היה שקר.

    ‏`link` — מה ש-`read_link` קרא על כרטיס ההפצה (הכרעת נדב 15/10): גג
    ופתיחה לפי מהירות הקישור (`LINK_CEILING_PER_MBPS`). מהירות שלא נקראה —
    600m/900m של היום, והסיבה ב-`link.why`. **לא** לגל ישיר: הזרם יוצא
    מהכרטיס של מחשב הבנייה, לא מזה של השרת.
    """
    ceiling = parse_bitrate(ceiling_raw) if ceiling_raw else TOP_BITRATE
    configured = ceiling if ceiling_raw else None
    server = None if direct or ceiling is None else _link_bounds(link)
    # ‏15/10 (ב): המקבלים — מחשב השיכפול האיטי בגל (ידני / מדווח / הנחת 1G).
    # גם בגל ישיר: המקבלים אותם מקבלים.
    cloner = (nic_speed.slowest(conn, [d.get("mac") for d in drives])
              if ceiling is not None else None)
    receivers = _link_bounds(cloner)
    # שרת שלא נקרא — הגג של היום (‏`IMAGECTL_MAX_BITRATE` או 900m) והפתיחה 600m.
    base = server or (ceiling, START_BITRATE)
    bounds = server
    if receivers is not None:
        bounds = (min(base[0], receivers[0]), min(base[1], receivers[1]))
    start_at = START_BITRATE
    if bounds is not None:
        # ‏`IMAGECTL_MAX_BITRATE` שהוגדר נשאר גג — הנמוך מבין השניים.
        ceiling = min(ceiling, bounds[0]) if ceiling_raw else bounds[0]
        start_at = bounds[1]
    # מי קבע את התקרה — למסך. ‏None = 900m של היום (שום דבר לא נקרא).
    if configured is not None and (bounds is None or configured <= bounds[0]):
        cap_by = "configured"
    elif receivers is not None and receivers[0] <= base[0] \
            and (server is None or receivers[0] < server[0]):
        cap_by = "cloner"
    else:
        cap_by = "server" if server is not None else None
    result = {"max_bitrate": ceiling_raw or None,
              "ceiling": _fmt(ceiling) if bounds else ceiling_raw or _fmt(TOP_BITRATE),
              "start": None, "basis": "start", "by": None, "previous": None,
              "drives": len(drives), "unknown_drives": 0, "cap_by": cap_by,
              **({"direct": True} if direct else {}),
              **({"link": {**link, "applied": server is not None}} if link else {}),
              **({"cloner": cloner} if cloner else {})}
    if ceiling is None:
        return {**result, "basis": "bad_ceiling"}
    start = min(start_at, ceiling)
    result = {**result, "max_bitrate": _fmt(start), "start": _fmt(start)}
    if not enabled(conn):
        return {**result, "basis": "disabled"}
    bits, basis, previous = ((start, "start", None) if direct
                             else _from_previous(conn, drives, start, session_id))
    slowest, limit, rate = None, None, None
    for drive in drives:
        got, measured, known = _limit_of(conn, drive["serial"], drive.get("model"))
        if not known:
            result["unknown_drives"] += 1
        if got is not None and (limit is None or got < limit):
            slowest, limit, rate = drive, got, measured
    by = None
    if slowest is not None and limit < bits:
        bits, basis = limit, "learned"
        by = {"serial": slowest["serial"], "model": slowest.get("model"),
              "mac": slowest.get("mac"), "port": slowest.get("port"),
              "rate_bps": rate}
    if bits > ceiling:
        bits, basis, by = ceiling, "ceiling", None
    # הרצפה מקצב הפתיחה **של 1GbE לכל היותר** (150m): כרטיס 10G של השרת אינו
    # אומר שהמקבלים ב-10G, ורצפה של 1500m הייתה נועלת חדר של מקבלים ב-1G
    # מעל מה שהם מסוגלים לקבל.
    bits = max(bits, int(min(start, START_BITRATE) * FLOOR_RATIO))
    return {**result, "max_bitrate": _fmt(bits), "basis": basis, "by": by,
            "previous": previous}


def decision(conn: sqlite3.Connection, session_id: str | None) -> dict | None:
    """מה שנבחר לגל כשיצא; ‏None = לא נבחר (גל שהשרת אינו משדר, או ישן)."""
    if not session_id:
        return None
    row = conn.execute("SELECT decision FROM wave_rates WHERE session_id = ?",
                       (session_id,)).fetchone()
    try:
        return json.loads(row["decision"]) if row else None
    except (TypeError, ValueError):
        return None


def decide(conn: sqlite3.Connection, session_id: str,
           drives: Callable[[], list[dict]], ceiling_raw: str | None,
           direct: bool = False,
           link: Callable[[], dict] | None = None) -> str | None:
    """נקרא כשהגל יוצא: בוחר, שומר, רושם ביומן, ומחזיר את `--max-bitrate`.
    ‏`direct` — המשדר הוא מחשב בנייה (#715, ‏`app.on_running`), ראו `choose`.
    ‏`link` — קורא את כרטיס ההפצה (`SenderEngine.link`); נקרא פעם אחת לגל.

    לעולם אינו זורק: כשל כאן הוא "לא נלמד" — נרשם ביומן, והגל יוצא בקצב
    הפתיחה. הלמידה אינה סיבה שהשידור לא ייצא.
    """
    try:
        wave = drives()
        chosen = choose(conn, wave, ceiling_raw, session_id, direct=direct,
                        link=link() if link is not None else None)
        # הכוננים שהגל יצא אליהם, כפי שהשרת ראה אותם **לפני** הגל — רק מהם
        # נלמד בסופו (`record_wave`), ולא ממה שמדווח בסוף.
        stored = {**chosen, "roster": [
            {"mac": d.get("mac"), "serial": d["serial"], "model": d.get("model")}
            for d in wave]}
        with _write_lock, writing(conn):
            conn.execute(
                "INSERT OR REPLACE INTO wave_rates (session_id, decision, at)"
                " VALUES (?, ?, ?)",
                (session_id, json.dumps(stored, ensure_ascii=False), now_iso()))
        by, previous, nic = chosen["by"], chosen["previous"], chosen.get("link")
        journal(conn, "wave_rate",
                f"{session_id} {chosen['max_bitrate']}"
                f" basis={chosen['basis']}"
                + (f" by={by['model'] or '?'} {by['serial']}" if by else "")
                + (f" from={previous['session_id']} {previous['max_bitrate']}"
                   f" rexmits={previous['loss_blocks']} mac={previous.get('mac')}"
                   if previous else "")
                + (f" link={nic['interface']}"
                   f" {str(nic['speed_mbps']) + 'Mb/s' if nic['applied'] else nic['why'] or 'unused'}"
                   f" ceiling={chosen['ceiling']}" if nic else "")
                + _cloner_note(chosen.get("cloner"), chosen.get("cap_by")))
        return chosen["max_bitrate"]
    except Exception as exc:      # noqa: BLE001 — הלמידה אינה מפילה גל
        log.exception("wave rate for %s failed; using the start rate", session_id)
        _failed(conn, f"{session_id} decide — {exc}")
        return _fallback(ceiling_raw)


def _cloner_note(cloner: dict | None, cap_by: str | None) -> str:
    """ליומן: מחשב השיכפול האיטי ומאיפה המהירות — ‏`assumed_1g(<why>)` כשהונחה."""
    if not cloner:
        return ""
    source = (f"assumed_1g({cloner['why']})" if cloner["source"] == "assumed"
              else cloner["source"])
    return (f" cloner={cloner['mac']} {cloner['speed_mbps']}Mb/s {source}"
            f" assumed={len(cloner['assumed'])} cap_by={cap_by}")


def direct_bitrate(conn: sqlite3.Connection, session_id: str | None,
                   ceiling_raw: str | None) -> str:
    """‏`--max-bitrate` למחשב בנייה שמשדר (#715, ‏`task.direct.multicast`):
    מה שנבחר לגל כשיצא (`app.on_running`); לפני כן, או בלי בחירה — קצב
    הפתיחה. לעולם לא "בלי תקרה": בלי `IMAGECTL_MAX_BITRATE` — ‏600m."""
    try:
        chosen = (decision(conn, session_id) or {}).get("max_bitrate")
    except Exception:             # noqa: BLE001 — קצב הפתיחה, לא כישלון ה-hello
        log.exception("direct bitrate for %s failed", session_id)
        chosen = None
    return chosen if isinstance(chosen, str) and chosen else _fallback(ceiling_raw)


def _fallback(ceiling_raw: str | None) -> str:
    """קצב הפתיחה בלי לגעת ב-DB — מה שהגל יוצא בו כשהבחירה עצמה נכשלה."""
    ceiling = parse_bitrate(ceiling_raw) if ceiling_raw else TOP_BITRATE
    return ceiling_raw if ceiling is None else _fmt(min(START_BITRATE, ceiling))


def record_stream(conn: sqlite3.Connection, session_id: str, outcome: dict) -> None:
    """נקרא כשהשולח סיים את הגל (הצליח, נכשל או נעצר): מה ש-udp-sender
    עצמו ספר — ‏`sent_bytes` ו-`loss_blocks` (‏`SenderEngine.wave_outcome`).

    ‏`None` נשמר כמות שהוא — "לא נמדד", שאינו "אפס שידורים חוזרים". גל שלא
    נבחר לו קצב (אין שורה ב-`wave_rates`) — אין מה לעדכן. לעולם אינו זורק.
    """
    try:
        stream = {key: outcome.get(key) for key in
                  ("sent_bytes", "loss_blocks", "stream_bps", "stream_s")}
        with _write_lock, writing(conn):
            conn.execute("UPDATE wave_rates SET stream = ? WHERE session_id = ?",
                         (json.dumps(stream), session_id))
    except Exception as exc:      # noqa: BLE001 — הזיכרון אינו מפיל את השולח
        log.exception("recording the stream of %s failed", session_id)
        _failed(conn, f"{session_id} stream — {exc}")


def _seconds(since: object, until: object) -> float | None:
    try:
        return (datetime.fromisoformat(until) - datetime.fromisoformat(since)).total_seconds()
    except (TypeError, ValueError):
        return None


def record_wave(conn: sqlite3.Connection, session_id: str, members) -> int:
    """כל מגירה שסיימה (`done`) בגל משאירה את הקצב שכתבה. מחזיר כמה נרשמו.

    הקצב = ‏`bytes_written` חלקי הזמן בין התנועה הראשונה לאחרונה של המונה
    (‏`first_moved_at` → ‏`moved_at`, ‏`reports.stamp_movement`) — בלי ההמתנה
    למקבלים לפני הבייט הראשון. חוזר על עצמו בבטחה (שורה לכל גל+סידורי),
    ולעולם אינו זורק: כשל כאן נרשם ביומן ואינו עוצר את סיום הגל.
    """
    try:
        decided = decision(conn, session_id) or {}
        cap = parse_bitrate(decided.get("max_bitrate"))
        if cap is None or decided.get("direct"):
            return 0                  # אין תקרה ידועה, או מקור שאולי הוא האיטי
        roster = {(d.get("mac"), d.get("serial")): d.get("model")
                  for d in decided.get("roster") or [] if isinstance(d, dict)}
        rows = []
        for member in members:
            for t in json.loads(member["targets_json"] or "[]"):
                serial = t.get("serial")
                if (member["mac"], serial) not in roster:
                    continue          # לא היה בגל כשיצא — לא לומדים ממנו
                seconds = _seconds(t.get("first_moved_at"), t.get("moved_at"))
                written = t.get("bytes_written")
                if (t.get("state") != "done" or not serial or seconds is None
                        or seconds < MIN_SAMPLE_S or not isinstance(written, int)
                        or written < MIN_SAMPLE_BYTES):
                    continue
                rows.append((serial, roster[(member["mac"], serial)], session_id,
                             int(written / seconds), cap // 8, written,
                             int(seconds), now_iso()))
        with _write_lock, writing(conn):
            return sum(conn.execute(
                "INSERT OR IGNORE INTO drive_rates (serial, model, session_id,"
                " rate_bps, cap_bps, bytes, seconds, at) VALUES (?,?,?,?,?,?,?,?)",
                row).rowcount for row in rows)
    except Exception as exc:      # noqa: BLE001 — הזיכרון אינו עוצר סיום גל
        log.exception("recording drive rates of %s failed", session_id)
        _failed(conn, f"{session_id} record — {exc}")
        return 0


def _failed(conn: sqlite3.Connection, detail: str) -> None:
    try:
        journal(conn, "drive_rates_failed", " ".join(detail.split())[:200])
    except Exception:             # noqa: BLE001 — גם היומן אינו מפיל
        log.exception("drive_rates failure was not journaled")
