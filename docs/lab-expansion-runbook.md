# בדיקה 2.5 — הרחבה 256→500: מה בדיוק לבדוק, פקודה-פקודה

מסמך עמידה-מול-המכונה לבדיקות 2.5 ו-2.5א–2.5ה
ב-[`docs/lab-test-plan.md`](lab-test-plan.md). לכל בדיקה: מה מריצים, מה
הפלט התקין, ומה סימן הכשל. אין כאן מה לנחש — אם הפלט לא נראה כמו
"תקין", זה כשל, גם אם המערכת עלתה.

**למה זו הבדיקה החשובה בקבוצה:** היא הראשונה שמאמתת מול דיסק אמיתי את
הנחת היסוד של כל מסלול ההרחבה — ‏`sgdisk` עם **התחלה שלילית**
(`-n idx:-<sectors>:0` = "כך וכך סקטורים לפני סוף השטח הפנוי"). כל
הטסטים בריפו מזייפים את `sgdisk`; אף אחד לא הריץ אותו על ברזל.

---

## 0. לפני שמתחילים

| | |
|---|---|
| כונן היעד | 500GB, ריק או עם תוכן שמותר לדרוס |
| האימג' | נקלט מכונן 256GB |
| גישה לתחנה | ‏`imagectl.debug=1` בשורת הקרנל פותח מעטפת טכנאי על `ttyS0` **וגם** SSH ‏(#44) |

התחברות ל-SSH של התחנה (טביעת ה-host נוצרת מחדש בכל אתחול, ולכן לא
נבדקת):

```sh
ssh -i ~/.ssh/imagectl-lab -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null root@<כתובת התחנה>
```

יומן הסוכן: `/run/imagectl/agent.log`. שם יושבות כל שורות ההרחבה.

---

## 1. מיד אחרי השחזור, **לפני** האתחול הראשון

עדיין בתוך ה-initramfs. שלוש פקודות, לפי הסדר.

### 1.1 היומן אומר שההרחבה בכלל רצה

```sh
grep -E 'expanding partition|expansion failed|no resize tool' /run/imagectl/agent.log
```

| פלט | פירוש |
|---|---|
| `expanding partition 3 to fill the disk` | ✅ ההרחבה זוהתה והתחילה |
| **אין שורה כזו** | ❌ **הכשל השקט של #46/#58**: אף מחיצה לא סומנה כניתנת להרחבה. בדוק במניפסט מי `"expandable": true`. **הסימון נעשה בקליטה בלבד** — אימג' שנקלט לפני #58 יישאר בלי סימון גם אחרי העדכון, וצריך לקלוט אותו מחדש |
| `WARNING: expansion failed -- restoring at the image's own size` | ⚠️ ההרחבה נכשלה לפני הזרם, והשחזור נסוג לגודל המקורי. האימג' תקין, ההרחבה לא. זה הכשל שמעניין אותנו |
| `WARNING: expansion failed, image still usable` | ⚠️ המחיצה הורחבה אבל מערכת הקבצים לא גדלה. תראה דיסק מלא ו-`df` ישן |
| `no resize tool for <fs>` | ⚠️ המחיצה גדלה, מערכת הקבצים לא — סוג לא נתמך |

### 1.2 טבלת המחיצות שלמה, וגיבוי ה-GPT זז לסוף

```sh
sgdisk -v /dev/sda
```

- ✅ `No problems found. 0 free sectors` — או מספר קטן של סקטורים חופשיים.
- ❌ כל אזכור של `backup GPT`, ‏`corrupt`, או `Problem:` — ‏`sgdisk -e`
  לא סיים. **עצור כאן**; אל תאתחל, זה בדיוק מה ש-2.5 בודקת.

### 1.3 המחיצות הן התקני בלוקים, לא קבצים

**הבדיקה הזו קודמת לכל השאר, כי בלעדיה כל השאר יכול לשקר** (#51).
אם הקרנל לא קרא מחדש את טבלת המחיצות, `/dev/sda3` אינו קיים כהתקן —
ואז `partclone -O /dev/sda3` **יוצר שם קובץ רגיל** ב-devtmpfs, כותב
לתוך RAM, ויוצא 0. ה-sha256 עובר, כי הוא נלקח על הבייטים שהתקבלו ולא
על מה שיושב על הדיסק. הסבב מסתיים `done` והדיסק קיבל טבלה וכלום.

```sh
ls -l /dev/sda*
lsblk -o NAME,TYPE,SIZE /dev/sda
```

- ✅ ב-`ls -l` כל שורה מתחילה ב-**`b`** (block device), והגודל מוצג
  כזוג `major, minor` — למשל `brw-rw---- 1 root disk 8, 3`.
  ב-`lsblk` העמודה `TYPE` היא `disk` לדיסק ו-`part` לכל מחיצה.
- ❌ שורה שמתחילה ב-**`-`** (קובץ רגיל) עם גודל בבייטים = המחיצה לא
  נוצרה, ומה שנכתב אליה הלך ל-RAM. **זה כישלון גם אם השחזור דיווח
  `done` וגם אם ה-sha עבר.**
- ❌ מחיצה שמופיעה במניפסט וחסרה כאן לגמרי = `blockdev --rereadpt`
  נכשל. חפש ביומן את הפלט של `sgdisk`.

### 1.4 המחיצה באמת תופסת את הדיסק

```sh
lsblk -b -o NAME,SIZE,FSTYPE,PARTUUID /dev/sda
parted -s /dev/sda print free
```

- ✅ המחיצה האחרונה שאינה swap גדולה בערך ב-244GB מגודלה באימג';
  ב-`print free` **אין** בלוק `Free Space` של מאות ג'יגה.
- ❌ בלוק `Free Space` בגודל ~244GB = המחיצה לא הורחבה. זה הכשל
  שהבדיקה קיימת בשבילו.
- ❌ ה-`PARTUUID` השתנה לעומת המניפסט — ‏Windows לא יעלה (#26).

---

## 2. אחרי האתחול למערכת המשוחזרת

**זו הבדיקה העיקרית.** מחיצה גדולה שהמערכת לא רואה היא כישלון, גם אם
הכול נראה תקין ב-initramfs.

### 2.1 לינוקס — ext4 (2.5ב)

```sh
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT
df -h /
```

- ✅ `df -h /` מראה **את הגודל המלא** של הכונן (‏~460G שמישים על 500GB),
  לא ~230G.
- ❌ `lsblk` מראה מחיצה של 460G אבל `df` מראה 230G = המחיצה גדלה
  ומערכת הקבצים לא. חפש ביומן `expansion failed, image still usable`.

בדיקת שפיות למערכת הקבצים:

```sh
sudo tune2fs -l /dev/sda3 | grep -E 'Block count|Block size'
```
מכפלת השניים צריכה להתקרב לגודל המחיצה ב-`lsblk -b`.

### 2.2 לינוקס — btrfs (2.5ג)

```sh
sudo btrfs filesystem usage /
sudo btrfs subvolume list /
```

- ✅ `Device size` שווה לגודל המחיצה, ו-`Free (estimated)` מציג את
  המקום שנוסף. ה-subvolumes מופיעים ונגישים.
- ❌ `Device size` נשאר בגודל האימג' = ‏`btrfs filesystem resize max`
  לא רץ.

### 2.3 swap (2.5ד) — שתי בדיקות, לא אחת

```sh
swapon --show
sudo blkid -s UUID -o value /dev/sda2
grep -i swap /etc/fstab
```

- ✅ `swapon --show` מציג את ה-swap בגודל שבמניפסט, **וה-UUID
  ש-`blkid` מחזיר זהה לזה שב-`fstab`**.
- ❌ `swapon --show` ריק = ה-swap לא עלתה. אם ה-UUID שונה מ-`fstab`,
  זה #48 (‏`mkswap` בלי `-U`) — היה אמור להיסגר, וחזרה שלו כאן היא
  רגרסיה.
- ❌ ה-swap יושבת **באמצע** הדיסק ולא בזנב = ההזזה של #46 לא רצה;
  אמת ב-`lsblk` שסדר המחיצות הוא [מערכת גדולה] ואז [swap].

### 2.4 Windows — NTFS (2.5א)

מ-PowerShell כמנהל במערכת המשוחזרת:

```powershell
Get-Disk | Format-Table Number, FriendlyName, Size, PartitionStyle
Get-Partition -DiskNumber 0 | Format-Table PartitionNumber, DriveLetter, Size, Type
Get-Volume | Format-Table DriveLetter, FileSystem, Size, SizeRemaining
```

- ✅ ה-`Size` של מחיצת המערכת ושל ה-Volume הם הגודל המלא; הסכום מכסה
  את הדיסק.
- ❌ פער של ~244GB בין `Get-Disk` לסכום המחיצות = שטח לא מוקצה.
  ב-`diskmgmt.msc` זה ייראה כרצועה שחורה **Unallocated** בקצה הימני.
- ❌ המערכת לא עולה עם `winload.efi 0xc000000e` = ה-GUID לא שרד את
  ההרחבה (#26).

---

## 3. ‏500→500 — שהכלי לא יעשה כלום (2.5ה)

שחזור אימג' 500GB לכונן 500GB. **הבדיקה כאן היא שדבר לא קרה.**

```sh
grep -c 'expanding partition' /run/imagectl/agent.log
sgdisk -v /dev/sda
```

- ✅ `0` — ההרחבה לא ניסתה בכלל, והשחזור הסתיים תקין.
- ❌ מספר גדול מ-0 = ניסינו להרחיב כשאין לאן.

**הסף מתועד:** ההרחבה רצה רק אם נשאר בזנב יותר מ-1GiB
(`2097152` סקטורים) **אחרי** שמנכים את גודל ה-swap שתחזור לשם. כונן
500GB מול אימג' 500GB לא חוצה את הסף, וזה תקין.

---

## 4. טבלת סימני כשל — מסך אחד

| מה רואים | מה זה | לאן |
|---|---|---|
| אין `expanding partition` ביומן | אף מחיצה לא סומנה `expandable` | #46, #58 |
| `Free Space` ~244GB ב-`parted` | המחיצה לא הורחבה | #46, #58 |
| ‏`recovery` באמצע הדיסק אחרי שחזור | ההזזה לזנב לא רצה | #58 |
| `lsblk` גדול, `df` קטן | מערכת הקבצים לא גדלה | `grow_expanded` |
| `swapon --show` ריק | ה-swap לא עלתה | #48 |
| ‏UUID ב-`blkid` ≠ ‏`fstab` | ‏`mkswap` בלי `-U` | #48 |
| swap באמצע הדיסק | ההזזה לזנב לא רצה | #46 |
| ‏`sgdisk -v` מתלונן על backup GPT | ‏`sgdisk -e` לא סיים | 2.5 |
| ‏`/dev/sdaN` הוא קובץ ולא `b` | נכתב ל-RAM; ה-sha עובר לשווא | #51 |
| ‏Windows: ‏`winload.efi 0xc000000e` | ‏GUID לא שרד | #26 |
| השחזור **נתקע** בלי הודעה | המתנה לזרם בלי תקרה | דווח מיד |

**כל כשל — לשמור את `/run/imagectl/agent.log` לפני אתחול מחדש.**
הוא ב-tmpfs ונמחק באתחול.
