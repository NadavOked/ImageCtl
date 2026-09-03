# שמונת שלבי ההדגמה למנהל — tools/lab/demo.ps1, ‏Issue #70.
# נטען ב-dot-source אחרי demo-checks.ps1 ו-demo-actions.ps1.
#
# הטקסט כאן נקרא בקול מול מנהל שלא מכיר את המערכת: "המחשב מקבל כתובת
# ברשת", לא "‏DHCP lease"; "מעתיקים את הדיסק", לא "‏partclone stream".
# שם המערכת, שמות המכונות ושורות הראיה הטכניות מופיעים רק בתיבת
# הראיות, ולא במה שמקריאים.
#
# חלוקת העבודה: **המנהל עושה** את התנועות הפיזיות (מחבר דיסק, מנתק
# תקליטור, בוחר בכיתה מהמסך), **הסקריפט מדליק מחשבים ובודק**. כך
# ההדגמה בטוחה להרצה על מעבדה חיה: היא לא משנה תצורה של אף מכונה.
# ארבעה שלבים חורגים מזה, וכל אחד מהם מאחורי הקלדת מילה בחלון
# (עיקרון 7) — ראו DemoRiskySteps ב-demo.ps1.

function Get-DemoSteps {
    param([hashtable]$Cfg)

    @(
      @{
        Title = "מכינים מחשב לדוגמה"
        Text = @(
          "נדליק מחשב אחד ונתקין עליו Windows מהתקליטור.",
          "",
          "אחרי ההתקנה אפשר להתקין תוכנות, לשנות רקע, לסדר קיצורי דרך — כל מה",
          "שרוצים שיהיה על כל מחשבי הכיתה. בסוף מכבים אותו.",
          "",
          "זה המחשב היחיד שמישהו מתקין עליו ידנית. פעם אחת."
        ) -join "`r`n"
        Confirm = @{ Word = "התקנה"
          Why = "הדלקת המחשב עם התקליטור מוחקת את מה שיש היום על הדיסק שלו." }
        ArmLabel = "הדלק את המחשב"
        Arm = { param($c) Start-DemoVms -Names @($c.GoldenVm) }
        Check = { param($c)
          $v = Get-DemoVm -Name $c.GoldenVm
          if (-not $v.Ok) { return New-DemoResult "unknown" "לא ניתן לבדוק את המחשב" @($v.Reason) }
          $d = Get-DemoDiskUsedGB -Name $c.GoldenVm
          if (-not $d.Ok) { return New-DemoResult "unknown" "לא ניתן לקרוא את גודל הדיסק" @($d.Reason) }
          $ev = @("מצב המחשב כפי שנקרא מהמארח: $($v.Vm.State)",
                  "הדיסק שלו תופס עכשיו $($d.GB) ג'יגה")
          if ($v.Vm.State -ne "Off") {
            return New-DemoResult "fail" "המחשב עדיין דולק" $ev "סיימו את ההתקנה וכבו אותו מתוך Windows."
          }
          if ($d.GB -lt $c.MinInstalledGB) {
            return New-DemoResult "fail" "אין על הדיסק מערכת" $ev `
              "הדיסק תופס פחות מ-$($c.MinInstalledGB) ג'יגה — ההתקנה לא רצה או לא הסתיימה. הדליקו והתקינו שוב."
          }
          New-DemoResult "pass" "יש מחשב מוכן, והוא כבוי" $ev
        }
      },

      @{
        Title = "מוציאים את התקליטור"
        Text = @(
          "מוציאים מהמחשב את תקליטור ההתקנה.",
          "",
          "בלעדיו הוא יעלה מהדיסק שלו, כמו כל מחשב רגיל, ולא יתחיל התקנה מחדש.",
          "",
          "בחלון הניהול: המחשב ← הגדרות ← כונן התקליטורים ← 'ללא'."
        ) -join "`r`n"
        Check = { param($c)
          $v = Get-DemoVm -Name $c.GoldenVm
          if (-not $v.Ok) { return New-DemoResult "unknown" "לא ניתן לבדוק את המחשב" @($v.Reason) }
          try { $drives = @(Get-VMDvdDrive -VMName $c.GoldenVm -ErrorAction Stop) } catch {
            return New-DemoResult "unknown" "לא ניתן לקרוא את כונן התקליטורים" @($_.Exception.Message)
          }
          $loaded = @($drives | Where-Object { $_.Path })
          if ($drives.Count -eq 0) {
            $ev = @("במחשב אין בכלל כונן תקליטורים — אין מה להוציא")
          } else {
            $ev = @("נבדקו $($drives.Count) כונני תקליטור במחשב")
            foreach ($d in $drives) {
              if ($d.Path) { $ev += "עדיין מוכנס: $($d.Path)" } else { $ev += "כונן ריק" }
            }
          }
          if ($loaded.Count -gt 0) {
            return New-DemoResult "fail" "התקליטור עדיין בפנים" $ev `
              "הוציאו אותו בהגדרות המחשב ולחצו 'בדוק שוב'."
          }
          New-DemoResult "pass" "אין תקליטור במחשב" $ev
        }
      },

      @{
        Title = "שומרים את המחשב הזה בשרת"
        Text = @(
          "מחברים את הדיסק שהכנו למחשב הבנייה ומדליקים אותו.",
          "",
          "על המסך שלו בוחרים את הדיסק, נותנים שם ('כיתת מחשבים 2026') ומתחילים.",
          "המחשב שולח לשרת עותק מדויק של הדיסק, והשרת בודק שהעותק שהגיע זהה",
          "למקור — בית אחר בית — לפני שהוא נכנס לספרייה.",
          "",
          "מכאן והלאה אף אחד לא נוגע יותר בדיסק הזה."
        ) -join "`r`n"
        ArmLabel = "הדלק את מחשב הבנייה"
        # ההפעלה רושמת קודם מה כבר שמור בשרת. במעבדה שכבר עבדו בה יש
        # למחשב הבנייה קליטה ישנה שהסתיימה, והבדיקה למטה הייתה מכריזה
        # "עבר" עליה — לפני שהמנהל נגע בכלום.
        Arm = { param($c)
          $base = Save-DemoBaseline -Cfg $c -Key "buildtask"
          if ($base.Status -ne "pass") { return $base }
          $boot = Start-DemoVms -Names @($c.BuildVm)
          if ($boot.Status -ne "pass") { return $boot }
          New-DemoResult "pass" $boot.Message (@($base.Evidence) + @($boot.Evidence))
        }
        Check = { param($c)
          $st = Get-DemoMachineState -Cfg $c -Name $c.BuildVm
          if (-not $st.Ok) {
            return New-DemoResult "unknown" "השרת לא מכיר את מחשב הבנייה" @($st.Reason) `
              "ודאו שהשרת דולק בכתובת $($c.ServerUrl), ושמחשב הבנייה רשום בטבלת המחשבים בקונסולה."
          }
          $t = $st.Data.task
          if ($null -eq $t) {
            return New-DemoResult "fail" "השרת לא מכיר עבודה כזו" `
              @("השרת ענה 200", "המכונה מזוהה אצלו: $($st.Mac)",
                "ואין לה שום עבודה רשומה") `
              "התחילו את השמירה מהמסך של מחשב הבנייה."
          }
          $gb = [math]::Round(([double]$t.bytes_written) / 1GB, 1)
          $ev = @("השרת ענה 200", "שם: $($t.name)", "מצב העבודה בשרת: $($t.state)",
                  "נשמרו $gb ג'יגה")
          $fresh = Test-DemoIsNew -Cfg $c -Key "buildtask" -Id "$($t.id)"
          if (-not $fresh.Ok) {
            return New-DemoResult "fail" "מה שרואים כאן אינו השמירה של ההדגמה" `
              ($ev + @($fresh.Reason)) `
              "התחילו שמירה חדשה מהמסך של מחשב הבנייה, ואז לחצו 'בדוק שוב'."
          }
          if ("$($t.state)" -ne "done") {
            $msg = if ("$($t.state)" -eq "failed") { "השמירה נכשלה" } else { "השמירה עוד רצה" }
            return New-DemoResult "fail" $msg ($ev + @("הודעת שגיאה: $($t.error)")) `
              "המתינו לסיום, או התחילו מחדש מהמסך של מחשב הבנייה."
          }
          if ([double]$t.bytes_written -le 0) {
            return New-DemoResult "fail" "לא נשמר דבר" $ev "התחילו את השמירה מחדש."
          }
          New-DemoResult "pass" "העותק בשרת, ונבדק מול המקור" $ev
        }
      },

      @{
        Title = "ממלאים דיסקים בחדר השיכפולים"
        Text = @(
          "מדליקים את מחשבי חדר השיכפולים. במסך של מחשב הבנייה בוחרים את",
          "התמונה שהכנו ואת מספר הדיסקים שרוצים, ומתחילים.",
          "",
          "כל המגירות מתמלאות יחד, מאותה שידור אחד — לא אחת אחרי השנייה.",
          "מגירה שאיבדה משהו בדרך מסומנת ככושלת בשמה; היא לא נספרת כמוצלחת."
        ) -join "`r`n"
        Confirm = @{ Word = "שיכפול"
          Why = "הדיסקים שבמגירות של מחשבי השיכפול ייכתבו מחדש. כל מה שעליהם היום יימחק." }
        ArmLabel = "הדלק את מחשבי השיכפול"
        Arm = { param($c)
          $base = Save-DemoBaseline -Cfg $c -Key "session"
          if ($base.Status -ne "pass") { return $base }
          $boot = Start-DemoVms -Names $c.ClonerVms
          if ($boot.Status -ne "pass") { return $boot }
          New-DemoResult "pass" $boot.Message (@($base.Evidence) + @($boot.Evidence))
        }
        Check = { param($c) Test-DemoSessionFinished -Cfg $c -Role "cloner" }
      },

      @{
        Title = "בודקים דיסק אחד"
        Text = @(
          "לוקחים דיסק אחד שהתמלא עכשיו, מחברים אותו למחשב ריק, ומדליקים.",
          "",
          "אמור לעלות בדיוק אותו Windows שהכנו בשלב הראשון — עם התוכנות,",
          "עם הרקע, עם הכל.",
          "",
          "זה מה שמחליף את השיכפול הידני של היום."
        ) -join "`r`n"
        ArmLabel = "הדלק את המחשב הריק"
        Arm = { param($c) Start-DemoVms -Names @($c.RestoredVm) }
        Check = { param($c)
          $v = Get-DemoVm -Name $c.RestoredVm
          if (-not $v.Ok) { return New-DemoResult "unknown" "לא ניתן לבדוק את המחשב" @($v.Reason) }
          $ev = @("מצב המחשב כפי שנקרא מהמארח: $($v.Vm.State)", "דולק כבר: $($v.Vm.Uptime)")
          if ($v.Vm.State -ne "Running") {
            return New-DemoResult "fail" "המחשב אינו דולק" $ev "לחצו 'הדלק את המחשב הריק'."
          }
          New-DemoResult "pass" "המחשב דולק" $ev
        }
        Eyes = "האם עלה על המסך אותו Windows שהכנו בשלב 1?"
        EyesNo = "אם המחשב לא עלה — הדיסק לא נכתב, או שהוא חובר למקום הלא נכון. חזרו לשלב 4."
      },

      @{
        Title = "מפיצים לכיתה שלמה"
        Text = @(
          "עכשיו כיתה שלמה, בלי לגעת באף מחשב.",
          "",
          "במסך של מחשב הבנייה בוחרים את הכיתה, את התמונה, ואת השם שיינתן",
          "למחשבים — למשל LAB1. השרת מעיר את כל מחשבי הכיתה, הם מקבלים את",
          "התמונה יחד, וכל אחד מקבל את השם שלו לפי המקום שלו בכיתה.",
          "",
          "בסוף מדליקים מחשב אחד ומסתכלים על שם המחשב שלו."
        ) -join "`r`n"
        Confirm = @{ Word = "כיתה"
          Why = "הדיסקים של מחשבי הכיתה שבהדגמה ייכתבו מחדש. כל מה שעליהם היום יימחק." }
        ArmLabel = "הדלק את מחשבי הכיתה"
        Arm = { param($c)
          $base = Save-DemoBaseline -Cfg $c -Key "session"
          if ($base.Status -ne "pass") { return $base }
          $boot = Start-DemoVms -Names $c.ClassVms
          if ($boot.Status -ne "pass") { return $boot }
          New-DemoResult "pass" $boot.Message (@($base.Evidence) + @($boot.Evidence))
        }
        Check = { param($c) Test-DemoSessionFinished -Cfg $c -Role "classroom" }
        Eyes = { param($c, $r) "האם על אחד המחשבים מופיע אחד השמות האלה? " + $r.Names }
        EyesNo = "אם השם שונה — בדקו את טבלת המחשבים בקונסולה: השם נגזר ממנה."
      },

      @{
        Title = "מחשב ברשת הרגילה של המכללה"
        Text = @(
          "השאלה הראשונה שנשאלת תמיד: אז כל מחשב במכללה יכול פתאום להימחק?",
          "",
          "מדליקים מחשב שנמצא ברשת הרגילה — לא ברשת של המערכת. הוא אמור",
          "לעלות ישר למערכת שלו, בלי לראות אף מסך שלנו ובלי לחכות.",
          "",
          "המערכת עונה למחשב הזה: 'אין לי מה לעשות איתך, עלה מהדיסק שלך'."
        ) -join "`r`n"
        ArmLabel = "הדלק את המחשב ברשת הרגילה"
        Arm = { param($c) Start-DemoVms -Names @($c.OutsideVm) }
        Check = { param($c)
          $m = Get-DemoVmMac -Name $c.OutsideVm
          if (-not $m.Ok) { return New-DemoResult "unknown" "לא ניתן לזהות את המחשב" @($m.Reason) }
          $b = Get-DemoBootMenu -BaseUrl $c.ServerUrl -Mac $m.Mac
          if (-not $b.Ok) { return New-DemoResult "unknown" "לא קיבלנו תשובה לבדוק" @($b.Reason) `
              "ודאו שהשרת דולק ועונה בכתובת $($c.ServerUrl)." }
          $ev = @("השרת ענה 200", "ההחלטה שהשרת רשם: $($b.Decision)",
                  "מאיפה המחשב עולה: $($b.Default)", "מסך בחירה: $($b.Style)",
                  "(השאלה הזו נרשמת בשרת כמגע של המחשב, ובטבלת הרשת תופיע לו",
                  " עד האתחול הבא שלו הכתובת של מחשב ההדגמה.)")
          if ($b.Default -ne "local") {
            return New-DemoResult "fail" "המערכת כן מציעה משהו למחשב הזה" $ev `
              "המחשב רשום כמחשב כיתה, או שהוא חובר לרשת ההפצה. בדקו את טבלת המחשבים."
          }
          if ($b.Style -ne "hidden") {
            return New-DemoResult "fail" "המחשב היה רואה מסך בחירה" $ev `
              "בדקו את הגדרות התפריט בשרת."
          }
          New-DemoResult "pass" "המערכת שולחת אותו לדיסק שלו, בלי מסך" $ev
        }
        Eyes = "האם המחשב עלה ישר למערכת שלו, בלי שום מסך בחירה?"
        EyesNo = "אם הופיע מסך — המחשב חובר לרשת הלא נכונה. בדקו לאיזו רשת הוא מחובר."
      },

      @{
        Title = "מכבים את השרת"
        Text = @(
          "והשאלה השנייה: מה קורה אם השרת נופל?",
          "",
          "נכבה אותו לגמרי — לא נעצור שירות, נכבה מחשב. ואז נדליק מחשב כיתה,",
          "אחד מאלה שהמערכת כן מכירה.",
          "",
          "הוא אמור לעלות מהדיסק שלו כרגיל, כאילו כלום. בלי המתנה, בלי מסך",
          "שגיאה. זו התשובה: המערכת מוסיפה יכולת, היא לא תנאי לעבודה.",
          "",
          "בסוף השלב ההדגמה מדליקה את השרת בחזרה ומוודאת שהוא עונה."
        ) -join "`r`n"
        Confirm = @{ Word = "כיבוי"
          Why = ("השלב מכבה את השרת — כיבוי מסודר, לא ניתוק חשמל. אם מישהו " +
                 "אחר עובד עכשיו על המעבדה, הקונסולה שלו תיפסק לעבוד עד סוף " +
                 "השלב. ההדגמה מסרבת לכבות כשיש הפצה פעילה, וגם כשלא הצליחה " +
                 "לבדוק אם יש. בסוף השלב היא מדליקה אותו בחזרה.") }
        ArmLabel = "כבה את השרת והדלק מחשב כיתה"
        Arm = { param($c) Invoke-DemoBlackout -Cfg $c }
        Check = { param($c) Restore-DemoServer -Cfg $c }
        # השאלה נשאלת **לפני** הבדיקה: הבדיקה מדליקה את השרת בחזרה,
        # ומה שצריך לראות בעין הוא דווקא המצב שבו הוא כבוי.
        Eyes = "האם מחשב הכיתה עלה למערכת שלו כרגיל, בזמן שהשרת כבוי?"
        EyesNo = "אם לא — סמנו זאת. השרת יודלק בחזרה בכל מקרה."
        EyesFirst = $true
      }
    )
}
