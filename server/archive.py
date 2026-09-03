"""ייצוא וייבוא של אימג' כקובץ tar אחד — גיבוי, העברה בין שרתים, ארכיון.

אימג' הוא תיקייה, ותיקייה לא נשלחת בדפדפן. tar נבחר כי הוא זורם: אפשר
לכתוב אותו בייט-בייט בלי להחזיק 60GB בזיכרון ובלי קובץ ביניים בשרת.

הכתיבה כאן ידנית ולא דרך tarfile, כי tarfile דוחף את כל הקובץ דרך
אובייקט הפלט בקריאה אחת, ומחולל שמזרים לרשת חייב לקבל את הבייטים
תוך כדי. הפורמט הוא ustar תקני — כל כלי tar קורא את התוצאה.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
import tarfile
from pathlib import Path
from typing import Iterator

from .images import ImageLibrary, inside, streamed_partitions, valid_image_id

log = logging.getLogger("imagectl.archive")

BLOCK = 512
CHUNK = 1024 * 1024
NAME_LIMIT = 100          # שדה השם ב-ustar; שמות האימג'ים קצרים בהרבה
#: אזור הביניים של הייבוא. **בתוך** שורש הספרייה, כדי שהכניסה לספרייה
#: תהיה `rename` באותה מערכת קבצים ולא העתקה של עשרות ג'יגה — ובשם
#: שמתחיל בנקודה, שהוא אזור עבודה שהספרייה מדלגת עליו. אסימון לכל ייבוא:
#: שם קבוע היה מאפשר לייבוא שני למחוק את אזור הביניים של הראשון תוך כדי
#: האימות שלו.
STAGING_PREFIX = ".import-"
#: השם שהמניפסט שוכב תחתיו עד שכל ה-sha256 נבדקו.
UNVERIFIED = "manifest.unverified"


class ArchiveError(ValueError):
    pass


# --- ייצוא -------------------------------------------------------------------


def _header(name: str, size: int, mtime: int) -> bytes:
    encoded = name.encode("utf-8")
    if len(encoded) > NAME_LIMIT:
        raise ArchiveError(f"שם ארוך מדי לארכיון: {name}")

    def octal(value: int, width: int) -> bytes:
        return f"{value:0{width - 1}o}\0".encode()

    header = bytearray(BLOCK)
    header[0:len(encoded)] = encoded
    header[100:108] = octal(0o644, 8)          # mode
    header[108:116] = octal(0, 8)              # uid
    header[116:124] = octal(0, 8)              # gid
    header[124:136] = octal(size, 12)
    header[136:148] = octal(mtime, 12)
    header[148:156] = b" " * 8                 # מקום לסכום הביקורת
    header[156:157] = b"0"                     # קובץ רגיל
    header[257:263] = b"ustar\0"
    header[263:265] = b"00"
    header[148:156] = f"{sum(header):06o}\0 ".encode()
    return bytes(header)


def tar_stream(directory: Path, arcname: str) -> Iterator[bytes]:
    """מזרים את תוכן התיקייה כ-tar. קבצים רגילים בלבד, ללא רקורסיה."""
    for path in sorted(p for p in directory.iterdir() if p.is_file()):
        size = path.stat().st_size
        yield _header(f"{arcname}/{path.name}", size, int(path.stat().st_mtime))
        with path.open("rb") as handle:
            sent = 0
            while chunk := handle.read(CHUNK):
                sent += len(chunk)
                yield chunk
        if sent != size:
            # הקובץ השתנה תוך כדי קריאה; ארכיון קטוע גרוע משגיאה.
            raise ArchiveError(f"הקובץ {path.name} השתנה במהלך הייצוא")
        padding = -size % BLOCK
        if padding:
            yield b"\0" * padding
    yield b"\0" * (BLOCK * 2)                  # סוף ארכיון


# --- ייבוא -------------------------------------------------------------------


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """רק קבצים רגילים, בלי נתיבים מוחלטים, יציאה מהתיקייה או קישורים."""
    members = []
    for member in tar.getmembers():
        name = member.name.replace("\\", "/")
        if not member.isfile():
            raise ArchiveError(f"הארכיון מכיל פריט שאינו קובץ: {name}")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ArchiveError(f"נתיב לא בטוח בארכיון: {name}")
        members.append(member)
    if not members:
        raise ArchiveError("הארכיון ריק")
    return members


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _shown(value: object) -> str:
    """ערך מהמניפסט בתוך הודעת שגיאה — קצוץ ומצוטט.

    מה שנדחה מוצג, אחרת אי אפשר לתקן את הארכיון; אבל הוא בא מקובץ של
    מישהו אחר, ולכן הוא לא נכנס להודעה כטקסט חופשי באורך שהוא בחר.
    """
    text = str(value)
    return repr(text[:60] + "…" if len(text) > 60 else text)


def _refuse_taken(image_id: str, taken: set[str]) -> None:
    if image_id in taken:
        raise ArchiveError(f"אימג' עם המזהה {image_id} כבר קיים בספרייה")


def _clear(staging: Path) -> None:
    """מנקה את אזור הביניים, ואומר ביומן אם לא הצליח.

    ‏`ignore_errors` לבדו הופך "לא הצלחנו למחוק" ל"נמחק": מה שנשאר כבר
    אינו נראה כאימג', אבל הוא תופס מקום עד שמישהו יידע עליו (עיקרון 5).
    """
    shutil.rmtree(staging, ignore_errors=True)
    if staging.exists():
        log.warning("import staging was left behind at %s", staging)


def import_tar(archive: Path, images_root: Path, existing_ids: set[str]) -> dict:
    """פורק ארכיון לספרייה. מחזיר את המניפסט שנקלט.

    האימות כאן הוא הסיבה שהפונקציה קיימת: אימג' שנכנס מבחוץ נבדק מול
    ה-sha256 שבמניפסט שלו לפני שהוא מוצע לכיתה. אימג' פגום שמתגלה רק
    באמצע שחזור הוא בדיוק מה שאסור.

    לכן שלושה דברים קורים כאן ולא במקום אחר (‏#71): החילוץ הוא לאזור
    עבודה שהספרייה מדלגת עליו, המניפסט אינו קיים בשמו כל עוד לא נבדק,
    ובדיקת ההתנגשות חוזרת מול הספרייה **ברגע הכניסה** — לא רק מול צילום
    שצולם לפני האימות, שאורך דקות על אימג' אמיתי.
    """
    staging = images_root / f"{STAGING_PREFIX}{secrets.token_hex(4)}"
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            members = _safe_members(tar)
            tar.extractall(staging, members=members, filter="data")

        roots = {Path(m.name.replace("\\", "/")).parts[0] for m in members}
        if len(roots) != 1:
            raise ArchiveError("הארכיון חייב להכיל תיקיית אימג' אחת")
        folder = staging / roots.pop()

        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            raise ArchiveError("אין manifest.json בארכיון")
        # תיקייה עם manifest.json מצהירה "כאן מונח אימג'". כל עוד הבייטים
        # לא נבדקו ההצהרה אינה נכונה, ולכן המניפסט שוכב תחת שם אחר: ייבוא
        # שנקטע — נפילת שרת, הפסקת חשמל — אינו משאיר על הדיסק, שהוא מקור
        # האמת, תיקייה חלקית שנראית כמו אימג' שלם.
        unverified = folder / UNVERIFIED
        manifest_path.replace(unverified)
        manifest = json.loads(unverified.read_text(encoding="utf-8"))
        if manifest.get("schema") != 1 or "id" not in manifest:
            raise ArchiveError("מניפסט לא תקין")
        # המזהה שבמניפסט הוא שם התיקייה שהאימג' ייכנס אליה, והמניפסט הזה
        # הגיע מקובץ שמישהו העלה: `../evil` בשדה הזה הוא כתיבה אל מחוץ
        # לספרייה (‏#110). מזהה שאינו בצורת המזהים הוא מניפסט לא תקין,
        # והוא נדחה בשמו — לא מנוקה בשקט לצורה שכן תעבור.
        if not valid_image_id(manifest["id"]):
            raise ArchiveError(f"מזהה אימג' לא תקין במניפסט: {_shown(manifest['id'])}")
        _refuse_taken(manifest["id"], existing_ids)

        for part in streamed_partitions(manifest):
            # ‏`_safe_members` שמר על מה שנכתב לדיסק, אבל השם הזה נקרא
            # מהמניפסט ולא מחברי ה-tar. הבדיקה קודמת לכל נגיעה בקובץ:
            # ‏`is_file` על נתיב שהתוקף בחר הוא כבר תשובה על מה קיים בשרת.
            name = part["file"]
            path = inside(folder / name, folder) if isinstance(name, str) else None
            if path is None:
                raise ArchiveError(f"נתיב קובץ מחיצה לא בטוח במניפסט: {_shown(name)}")
            if not path.is_file():
                raise ArchiveError(f"חסר קובץ מחיצה בארכיון: {_shown(name)}")
            if _sha256(path) != part["sha256"]:
                raise ArchiveError(f"אימות נכשל: {_shown(name)} אינו תואם ל-sha256")

        # קריאה טרייה, אחרי האימות: אימג' מאומת בעל אותו מזהה אינו נדחק
        # ואינו נדרס — הייבוא נדחה, והקיים נשאר כפי שהוא. תיקיית האימג'
        # אינה חייבת להיקרא בשם המזהה, ולכן `target.exists()` לבדו אינו
        # מוצא התנגשות מזהים.
        _refuse_taken(manifest["id"], set(ImageLibrary(images_root).scan()))
        # חגורה שנייה, על הנתיב עצמו ולא על המזהה: מה שנכנס לספרייה חייב
        # לנחות בתוכה. ‏`target.exists()` לבדו אינו גבול — יעד מחוץ לשורש
        # שאינו קיים עובר אותו בשקט, וזה בדיוק המקרה המסוכן.
        target = inside(images_root / manifest["id"], images_root)
        if target is None:
            raise ArchiveError(
                f"יעד האימג' יוצא משורש הספרייה: {_shown(manifest['id'])}")
        if target.exists():
            raise ArchiveError(f"התיקייה {manifest['id']} כבר קיימת")
        unverified.replace(manifest_path)
        folder.rename(target)
        return manifest
    finally:
        _clear(staging)
