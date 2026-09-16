# מדיניות אבטחה

ImageCtl מריץ שרת אתחול רשת: הוא מגיש שרשרת אתחול חתומה, יכול לחלק
כתובות DHCP, וכותב לכוננים של כיתות שלמות. חולשה כאן אינה "באג באתר" —
היא גישה לכתיבה על מחשבים של אנשים אחרים.

שני ריפואים, שני משטחי חשיפה:

| ריפו | נראות | מה Issue שם עושה |
|---|---|---|
| `NadavOked/ImageCtl-archive` | **פרטי** | Issue אינו מפרסם דבר. זה מקום הדיווח. |
| `NadavOked/ImageCtl` | ציבורי | ענף, PR או Issue שם מפרסמים את החולשה לפני שיש תיקון. |

**דיווח על חולשה בארכיון הפרטי הוא Issue, והוא מותר ורצוי.** מה שאסור
הוא לפרסם אותה לציבורי לפני שיש תיקון.

---

## גרסאות נתמכות

הפרויקט מסומן ב-semver בתגים, נמצא לפני 1.0, ונפרס ב-`git checkout` של
תג נקי. אין ענפי תחזוקה ואין backport.

| גרסה | נתמכת | מה זה אומר |
|---|---|---|
| **התג האחרון** (`git describe --tags`; `v0.15.1` נכון ל-2026-08-30) | ✅ | תיקון אבטחה נכנס ל-`main` ומקבל תג חדש |
| כל תג ישן יותר | ❌ | אין תיקון בגרסה הישנה. השדרוג הוא checkout של התג האחרון |

מה שמחזיק את זה סביר: **המסלול קדימה קצר** — התגים יוצאים בקצב גבוה,
והפרש של כמה תגים אינו שדרוג גדול.

לפני 1.0, קפיצת minor עשויה לשנות התנהגות שהמפעיל רואה. הכלל המחייב
לגזירת גרסאות כתוב ב-`CLAUDE.md`.

---

## לאן מדווחים

**אם יש גישה ל-`NadavOked/ImageCtl-archive` (הריפו הפרטי):** Issue שם,
עם תווית `security`. הוא אינו מפרסם דבר. זה המסלול הרגיל.

**אם נראה רק `NadavOked/ImageCtl` (הציבורי):** אין גישה לארכיון. דיווח
דרך GitHub Security Advisories בלבד — לשונית **Security** ← **Report a
vulnerability**, או
<https://github.com/NadavOked/ImageCtl-archive/security/advisories/new>.
Issue או PR בציבורי מפרסם את החולשה לפני שיש תיקון.

**אין כתובת דוא"ל לאבטחה, ואין תוכנית תגמול.** הפרויקט מתוחזק על ידי
אדם אחד; ערוץ שאין מי שיאייש אותו לא היה מוסיף דבר.

**מה שאסור לכולם:** לפרסם לציבורי — ענף, PR, Issue או פוסט — לפני שיש
תיקון. `tools/git/publish-to-public.sh` מסרב ל-Issue/PR עם תווית
`security`; זה השומר האמיתי, לא איסור על Issue בפרטי.

## מתי Advisory בנוסף ל-Issue

Advisory נדרש כשהחולשה נוגעת **למי שאינו נדב**:

- תלמיד שכוננו נכתב.
- מפעיל במכללה אחרת שפורס את המוצר הציבורי.
- כל מי שיכול להגיע לשרת בלי להיות בעל הריפו.

חולשה שחיה רק בכלי פנימי שאינו יוצא לציבורי (סוכנים, CI, סקילים,
מעבדה) — Issue פרטי עם תווית `security` מספיק. אין משטח חשיפה חיצוני,
ואין למי לחשוף בתיאום.

בשני המקרים התווית `security` נשארת על ה-Issue, כדי שהפרסום לציבורי
ייעצר.

---

## מה כדאי שיהיה בדיווח

אותו מבנה שבו נכתבים הבאגים כאן, כי הוא זה שמאפשר לתקן:

- **התג או הקומיט** שנבדק (`git describe --tags`).
- **השורש** — הקובץ והשורה, עם ציטוט הקוד. לא רק "יש path traversal".
- **מה נשבר בפועל** — מה תוקף יכול לעשות, ובאיזו הרשאה הוא מתחיל
  (אנונימי? משתמש `deploy`? גישה לוילן ההפצה?).
- **שחזור** — הצעדים המדויקים, ומה נצפה מולם. הפרויקט מבחין בין
  "לא הצלחנו לבדוק" ל"בדקנו"; אם לא שוחזר בפועל — לכתוב זאת.
- **מה שאסור בתיקון**, אם ידוע — כיוון תיקון שייצור את הבאג ההפוך.

---

## מה בתחום

- **השרת** (`server/`) — אימות, הרשאות, חתימת עוגיות, העלאה וייבוא
  של אימג'ים, ה-API של הקונסולה.
- **הסוכן ושרשרת האתחול** (`agent/`, `boot/`) — מה שמריץ מכונה שעולה
  ברשת, ומה שנכתב לכונן שלה.
- **המתקין ויחידות ה-systemd** (`install/`) — הרשאות, `ReadWritePaths`,
  קבצים שנוצרים על השרת.

## מה לא בתחום

- **הפעלה שגויה בכוונה.** DHCP על רשת המכללה הוא ההגדרה המסוכנת ביותר
  במערכת. הוא כבוי כברירת מחדל ומאחורי כמה שכבות אישור, ותועד ככזה
  ב-`docs/server-install.md`. הדלקה מודעת שלו על הרשת הלא-נכונה היא
  תקלה תפעולית, לא חולשה.
- **פעולות הרסניות שמפעיל מורשה ביקש** (מחיקת אימג', עצירת סבב). הן
  מאחורי הקלדת שם בכוונה.
- **דיווחי סורק בלי השפעה מוכחת.** פלט של כלי הוא התחלה של דיווח, לא
  דיווח.
- **המעבדה של הפרויקט** ותשתיות בדיקה — הן אינן חלק ממה שנפרס.

---

## מה קורה אחרי הדיווח

בעלים אחד, מאמץ סביר, **בלי SLA**. אין הבטחה לזמן תגובה, כי הבטחה כזו
לא תיאמת. מה שכן:

- כל דיווח נענה, גם כשהתשובה היא "זו לא חולשה" עם הסבר.
- תיקון נכנס ל-`main` ומקבל **תג חדש**; ה-Advisory מפורסם אחריו, עם
  קרדיט למדווח אלא אם ביקש אחרת.
- אם התיקון נוגע בהתנהגות שמפעיל רואה, הוא נכתב גם ב-`CHANGELOG.md`.

---

## Reporting a vulnerability (English)

ImageCtl is a network boot and disk-imaging server for a college lab.

Two repositories, two disclosure surfaces:

- `NadavOked/ImageCtl-archive` is **private**. An issue there does not
  publish anything. That is the working repo — file the issue there, with
  the `security` label.
- `NadavOked/ImageCtl` is public. A branch, PR, or issue there discloses
  the weakness before a fix exists. Do not file there.

If you can only see the public repo, report through GitHub Security
Advisories:
<https://github.com/NadavOked/ImageCtl-archive/security/advisories/new>

A Security Advisory is **also** required when the weakness affects someone
other than the maintainer (a student whose disk is written, another college
deploying the public product). A weakness only in private-only tooling
(agents, CI, lab) that never ships publicly needs the private issue and
label, not an Advisory.

`publish-to-public.sh` refuses any issue or PR labelled `security`. That is
the real guard — publishing is what discloses, not the private issue.

Only the latest tag is supported; fixes land on `main` and get a new tag.
There is no security email address and no bounty — this project has a single
maintainer, and responses are best effort with no SLA. Please include the tag
you tested, the file and line, what an attacker gains and from which
privilege level, and the exact steps you ran. If you did not actually
reproduce it, say so.
