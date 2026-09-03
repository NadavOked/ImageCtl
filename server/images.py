"""ספריית האימג'ים — הדיסק הוא מקור האמת.

אימג' = תיקייה עם manifest.json (ממשק 1) וקבצי המחיצות שלצדו. אין טבלת
אימג'ים ב-DB: מה שמונח בתיקייה קיים, מה שנמחק ממנה איננו. זה מה שהופך
גיבוי והעברה ל"להעתיק תיקייה".

בטיחות הגשה: קובץ מוגש רק אם הוא מופיע במניפסט בשמו המדויק — רשימה
לבנה, לא סניטציה של נתיבים.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger("imagectl.images")

REQUIRED_FIELDS = ("id", "name", "family", "min_target_bytes", "partitions")
REQUIRED_PARTITION_FIELDS = ("index", "file", "sha256")

#: מזהה אימג' — בדיוק מה שהשרת מייצר בקליטה: `"img_" + secrets.token_hex(3)`
#: (‏`create_capture` ב-capture.py), כלומר שש ספרות הקסה קטנות.
#:
#: המזהה אינו מחרוזת תצוגה: הוא **שם התיקייה** שהאימג' נכנס אליה, ולכן
#: הוא הגבול בין "מזהה" לבין "נתיב". מניפסט מגיע גם מבחוץ (ייבוא ארכיון),
#: ומזהה כמו `../evil` בו הוא בקשה לכתוב אל מחוץ לספרייה (‏#110).
IMAGE_ID = re.compile(r"img_[0-9a-f]{6}")

MIB = 1 << 20
#: מגה-בייט שמור בזנב הכונן: עותק הגיבוי של ה-GPT (33 סקטורים) והיישור
#: ל-2048 סקטורים ש-sgdisk כופה כשהוא בורא מחיצה מחדש.
GPT_TAIL_BYTES = MIB
#: הסף בין המשפחות — אותו מספר בדיוק שהסוכן גוזר בו בקליטה (capture.sh).
FAMILY_SPLIT_BYTES = 322122547200


def valid_image_id(value: object) -> bool:
    """האם זה מזהה אימג' — ולא משהו שאפשר "לתקן" לכדי מזהה.

    התשובה היא כן/לא בלבד: אין כאן `strip`, אין `basename` ואין החלפת
    תווים. ניקוי שקט הופך קלט זדוני לקלט תקין, והקורא שנשען עליו כבר
    לא יודע מה בדיוק אישר (עיקרון 5 בכיוון ההפוך). מזהה שאינו תואם —
    נדחה בשמו, עם הודעה שאומרת מה נדחה.
    """
    return isinstance(value, str) and IMAGE_ID.fullmatch(value) is not None


def inside(path: Path, root: Path) -> Path | None:
    """הנתיב המלא אם הוא באמת בתוך `root`, ואחרת `None`.

    החגורה השנייה מתחת ל-`valid_image_id`, ובכוונה לא במקומה: היא בודקת
    את מה שיוצא — הנתיב אחרי הצירוף — ולא את מה שנכנס. ‏regex שמישהו
    ירחיב בעתיד, או שדה נוסף שמישהו יצרף לנתיב, לא ישברו את הגבול.

    ‏`resolve` פותח גם קישורים סימבוליים, ולכן "בתוך" כאן הוא היכן שהנתיב
    באמת נוחת. אם לא הצלחנו לקבוע — אין ראיה חיובית שהוא בפנים, והתשובה
    היא `None` ולא "כנראה בסדר".
    """
    try:
        resolved = path.resolve()
        base = root.resolve()
    except (OSError, ValueError) as exc:      # גם שם עם בייט אפס בתוכו
        log.warning("cannot resolve %s under %s: %s", path, root, exc)
        return None
    return resolved if resolved.is_relative_to(base) else None


def disk_family(size_bytes: int) -> int:
    """המשפחה של כונן בגודל נתון (אפיון סעיף 13): מתחת ל-300GiB — 256."""
    return 256 if size_bytes < FAMILY_SPLIT_BYTES else 500


def _whole(value: object) -> int | None:
    """מספר שלם אמיתי, או None. ‏bool הוא int בפייתון ואינו גודל."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def layout_end_bytes(manifest: dict) -> int | None:
    """הבייט שאחרי המחיצה האחרונה בפריסה, או None אם הגיאומטריה חסרה.

    מספיק שלמחיצה אחת אין `start_sector` או `size_bytes` כדי שהפריסה
    כולה לא תהיה ידועה: "לא הצלחנו לחשב" אינו "יצא קטן" (עיקרון 5).
    """
    sector = _whole(manifest.get("sector_size")) or 512
    ends = []
    for part in manifest.get("partitions") or []:
        start = _whole(part.get("start_sector")) if isinstance(part, dict) else None
        size = _whole(part.get("size_bytes")) if isinstance(part, dict) else None
        if start is None or size is None:
            return None
        ends.append(start * sector + size)
    return max(ends) if ends else None


def required_bytes(manifest: dict) -> int | None:
    """כמה בייטים כונן יעד חייב להחזיק כדי שהאימג' ייכתב עליו.

    ‏#82: הדרישה אינה גודל דיסק המקור אלא **סוף הפריסה** — המחיצה
    האחרונה שהמניפסט מצהיר עליה, ועוד `GPT_TAIL_BYTES`. דיסק מקור עם
    שטח לא מוקצה בזנב אינו חלק ממה שהאימג' צריך, וזה כל ההבדל בין
    אימג' זהב שנבנה במכונה וירטואלית לבין אימג' שאינו נכנס לשום ברזל:
    דיסק VM ‏"256GB" הוא 256 GiB, שבעה אחוזים יותר מכל כונן פיזי
    מאותה מחלקה.

    **הרצפה של המחיצה המורחבת היא הגודל שבו נקלטה, לא כמה תפוס בה.**
    ‏partclone מסרב לשחזר מערכת קבצים לתוך מחיצה קטנה ממנה ("Target
    partition size is smaller than source"), ועם `-C` הוא כותב עד
    "No space left on device" ומשאיר superblock פגום. לכן החישוב אינו
    נוגע ב-`used_bytes` כלל — וממילא אינו נשען על שדה שבכל מניפסט
    שנקלט לפני #84 הוא 0.

    ‏`None` = לא ניתן לקבוע. הקורא חוסם, לא מוותר (עיקרון 5).
    """
    end = layout_end_bytes(manifest)
    if end is None:
        # מניפסט בלי גיאומטריה מלאה — הערך המוצהר, שהוא השמרני מבין השניים.
        return _whole(manifest.get("min_target_bytes"))
    need = -(-(end + GPT_TAIL_BYTES) // MIB) * MIB
    source = _whole(manifest.get("source_disk_bytes"))
    if source is not None and end <= source < need:
        # דיסק המקור החזיק את הפריסה הזו בפועל — ראיה חיובית שדי בו.
        need = source
    return need


def streamed_partitions(manifest: dict) -> list[dict]:
    """המחיצות שיש להן קובץ בזרם. swap מתועד במניפסט אבל לא נשמר ולא
    משודר (אפיון סעיף 14) — הסוכן יוצר אותו מחדש; `file` שלו הוא null."""
    return [p for p in manifest["partitions"] if p.get("file")]


def image_os(manifest: dict) -> str:
    """`os` מהמניפסט, ולאימג'ים שנקלטו לפני שהשדה נוסף — מתפקידי המחיצות."""
    declared = manifest.get("os")
    if declared in ("windows", "linux"):
        return declared
    roles = {p.get("role") for p in manifest["partitions"]}
    if "windows" in roles:
        return "windows"
    if "linux" in roles:
        return "linux"
    return "unknown"


#: מה ששם תיקייה או שם אימג' יכולים להיות. **‏ASCII בלבד, ובכוונה.**
#:
#: מסך התחנה הוא קונסולת טקסט של לינוקס, ושם עברית אינה ניתנת להצגה
#: משתי סיבות בלתי תלויות: ב-initrd אין `setfont` ואין קובץ פונט,
#: **ולקונסולת לינוקס אין תמיכת RTL בכלל**. היפוך המחרוזת מראש עובד על
#: עברית טהורה ונשבר על מעורב — וכל שמות האימג'ים במעבדה מעורבים.
#:
#: והאכיפה כאן ולא במסך, כי הקונסולה היא דפדפן ומציגה עברית מצוין:
#: בלי בדיקה השם ייכתב **בהצלחה**, והכשל יתגלה חודשים אחר כך מול מכונה
#: בכיתה כמסך של ריבועים. זה בדיוק #111 — שם משתמש בעברית עבר את
#: היצירה ואת בדיקת הסיסמה, והפיל את `set_cookie` ב-500 על הסיסמה
#: **הנכונה**: הכישלון היה ניתן לאבחון וההצלחה לא.
#:
#: ‏48 תווים: שורת התפריט בתחנה היא ``N) <שם>  [<משפחה> GB family]``
#: על קונסולה של 80 עמודות.
#: מתחיל **וגם מסתיים** באות או בספרה. רווח בסוף בלתי נראה על המסך,
#: ו-`nicdesc:eth0 ` מ-#130 הוא בדיוק מה שהוא עושה: מפתח אחר לגמרי
#: מזה שהמפעיל התכוון לו, בלי שום סימן.
DISPLAY_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9 ._-]{0,46}[A-Za-z0-9])?")


def validate_display_name(value: str, what: str = "השם") -> str:
    """שם תקין לתצוגה, או ValueError בעברית.

    **נדחה בשמו ואינו מנוקה לצורה תקינה** — אותה הכרעה של #110 ושל #102:
    ניקוי שקט הופך "מה שהקלדתי" ל"מה שנשמר" בלי שאיש יידע.

    האכיפה היא **ביצירה בלבד**, כמו ב-`users._check_username`: שם קיים
    לא ייחסם רטרואקטיבית על ידי כלל שנוסף אחריו.
    """
    # **בלי `.strip()`.** רווח בתחילת השם או בסופו הוא בדיוק המקרה
    # ש-"נדחה ואינו מנוקה" נכתב בשבילו: ניקוי שקט הופך "מה שהקלדתי"
    # ל"מה שנשמר" בלי שאיש יידע, ו-`nicdesc:eth0 ` מ-#130 הוא מה שקורה
    # כשהרווח כן נשמר במקום אחד ולא באחר.
    text = value or ""
    if not DISPLAY_NAME_RE.fullmatch(text):
        raise ValueError(
            f"{what} {text!r} אינו תקין — מותרים אותיות אנגליות, ספרות, "
            "רווח, נקודה, מקף וקו תחתון; מתחיל באות או בספרה, עד 48 תווים")
    return text


class ImageLibrary:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    #: תיקיות העבודה של השרת בתוך שורש הספרייה — קליטה (`.capture-<task>`)
    #: וייבוא (`.import-<token>`). מה שמונח בהן עדיין לא אומת, ולכן הן
    #: אינן חלק מהספרייה.
    WORK_AREA_PREFIX = "."

    def _in_work_area(self, path: Path) -> bool:
        return any(
            part.startswith(self.WORK_AREA_PREFIX)
            for part in path.relative_to(self.root).parts[:-1]
        )

    def scan(self) -> dict[str, dict]:
        """כל המניפסטים התקינים, לפי id. פגום — מדולג עם אזהרה, לא מפיל.

        שדה לא ידוע במניפסט — מתעלמים, לפי המוסכמות הרוחביות.

        שני מצבים מדולגים בשלמותם, ומאותו טעם — אימג' שאיננו יודעים מה
        הוא לא יוצע לכיתה (עיקרון 5): מניפסט שיושב באזור עבודה, ומזהה
        שיותר מתיקייה אחת מצהירה עליו.
        """
        found: dict[str, dict] = {}
        ambiguous: set[str] = set()
        if not self.root.is_dir():
            return found
        for path in sorted(self.root.rglob("manifest.json")):
            if self._in_work_area(path):
                continue
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                log.warning("skipping unreadable manifest %s: %s", path, exc)
                continue
            problem = self._validate(manifest)
            if problem:
                log.warning("skipping manifest %s: %s", path, problem)
                continue
            image_id = manifest["id"]
            if image_id in found or image_id in ambiguous:
                # "שומרים את הראשון" הוא הכרעה שקטה לפי מיון נתיבים בין שני
                # אימג'ים שונים — וההכרעה הזאת נפרסת על כיתה שלמה. תיקייה
                # שנוספה מאוחר יותר גם דחקה כך אימג' ותיק ומאומת.
                log.warning("duplicate image id %s at %s -- serving neither", image_id, path)
                ambiguous.add(image_id)
                found.pop(image_id, None)
                continue
            manifest["_dir"] = str(path.parent)
            found[image_id] = manifest
        return found

    @staticmethod
    def _validate(manifest: object) -> str | None:
        if not isinstance(manifest, dict):
            return "not an object"
        if manifest.get("schema") != 1:
            return f"unknown schema: {manifest.get('schema')!r}"
        for field in REQUIRED_FIELDS:
            if field not in manifest:
                return f"missing field: {field}"
        # שני השדות שההחלטה "האם האימג' נכנס לכונן" נשענת עליהם. מניפסט
        # שהם פגומים בו נדחה כאן, ולא מגיע להשוואה שתשווה מחרוזת למספר.
        if manifest["family"] not in (256, 500):
            return f"family must be 256 or 500, not {manifest['family']!r}"
        if _whole(manifest["min_target_bytes"]) is None:
            return "min_target_bytes must be a whole number of bytes"
        if not isinstance(manifest["partitions"], list) or not manifest["partitions"]:
            return "partitions must be a non-empty list"
        for part in manifest["partitions"]:
            for field in REQUIRED_PARTITION_FIELDS:
                if not isinstance(part, dict) or field not in part:
                    return f"partition missing field: {field}"
            if part["file"] is None and part.get("role") != "swap":
                return f"partition {part['index']} has no file"
        if not streamed_partitions(manifest):
            return "no partition carries data"
        return None

    def get(self, image_id: str) -> dict | None:
        return self.scan().get(image_id)

    def file_path(self, image_id: str, filename: str) -> Path | None:
        """הנתיב לקובץ מחיצה — רק אם המניפסט מכריז עליו בדיוק בשם הזה."""
        manifest = self.get(image_id)
        if manifest is None:
            return None
        declared = {part["file"] for part in streamed_partitions(manifest)}
        if filename not in declared:
            return None
        path = Path(manifest["_dir"]) / filename
        return path if path.is_file() else None

    def allowed_for_disks(self, disks: list[dict] | None) -> list[str]:
        """מזהי האימג'ים שמתאימים לדיסקים שדווחו ב-hello.

        שני מסננים מול הדיסק הפנימי הגדול ביותר, ולכל אחד תפקיד משלו:

        1. **המשפחה** (אפיון סעיף 13) — אימג' 500 לא יוצע לכונן 256.
           תווית גסה בת שתי מחלקות, וזה כל מה שהיא.
        2. **הדרישה האמיתית** (`required_bytes`, ‏#82) — הגיאומטריה של
           הפריסה. זו ההחלטה הפיזית, וזו שאסור לוותר עליה: המשפחה לבדה
           הייתה מציעה אימג' של 299GB לכונן של 240GB — שניהם `256`.

        הסוכן רק מציג, הוא לא מסנן בעצמו (כלל ממשק 3).
        """
        images = self.scan()
        if not disks:
            return sorted(images)
        internal = [
            d.get("size_bytes", 0)
            for d in disks
            if isinstance(d, dict) and not d.get("removable", False)
        ]
        if not internal:
            return []
        biggest = max(internal)
        family = disk_family(biggest)
        allowed = []
        for image_id, manifest in images.items():
            need = required_bytes(manifest)
            if need is None:
                # לא ידענו לחשב כמה הוא צריך — לא מציעים אותו (עיקרון 5).
                log.warning("skipping %s: its size requirement is unknown", image_id)
                continue
            if need <= biggest and manifest["family"] <= family:
                allowed.append(image_id)
        return sorted(allowed)

    def public_list(self) -> list[dict]:
        """מה שהקונסולה מציגה — בלי נתיבי דיסק פנימיים.

        הסדר בתוך תיקייה: קודם `sort` (שהקונסולה עורכת), ואז שם.
        """
        result = []
        for manifest in self.scan().values():
            result.append(
                {
                    "id": manifest["id"],
                    "name": manifest["name"],
                    "description": manifest.get("description", ""),
                    "folder": manifest.get("folder", ""),
                    "sort": manifest.get("sort", 10**9),
                    "family": manifest["family"],
                    "os": image_os(manifest),
                    "created": manifest.get("created", ""),
                    "total_compressed_bytes": manifest.get("total_compressed_bytes", 0),
                    "partitions": len(manifest["partitions"]),
                }
            )
        return sorted(result, key=lambda m: (m["folder"], m["sort"], m["name"]))

    EDITABLE_FIELDS = ("name", "description", "folder", "sort")

    def write_meta(self, image_id: str, changes: dict) -> bool:
        """עריכת שדות תצוגה במניפסט — שם, תיאור, תיקייה, סדר.

        הדיסק הוא מקור האמת, ולכן העריכה נכתבת ל-manifest.json עצמו,
        אטומית (קובץ זמני + replace). שדות שאינם ברשימה — נדחים.
        """
        manifest = self.get(image_id)
        if manifest is None:
            return False
        path = Path(manifest["_dir"]) / "manifest.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        for key, value in changes.items():
            if key not in self.EDITABLE_FIELDS:
                raise ValueError(f"field not editable: {key}")
            # ‏`folder` ריק לגיטימי — אימג' שאינו בתיקייה. ‏`name` ריק
            # אינו: אימג' בלי שם אינו ניתן לבחירה במסך התחנה, והוא
            # היה עובר כאן בשקט כי התנאי הקודם דילג על ערך ריק.
            if key == "name" or (key == "folder" and value not in (None, "")):
                # ‏#138: כאן עוברים גם `PUT /images/{id}` וגם שינוי שם
                # תיקייה, ששניהם כותבים דרך write_meta.
                value = validate_display_name(
                    str(value), "שם האימג'" if key == "name" else "שם התיקייה")
            raw[key] = value
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True

    def delete(self, image_id: str) -> bool:
        """מוחק את תיקיית האימג' כולה. האישור בהקלדת שם — אצל הקורא."""
        manifest = self.get(image_id)
        if manifest is None:
            return False
        shutil.rmtree(manifest["_dir"])
        return True
