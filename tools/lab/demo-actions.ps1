# ההפצה וכיבוי השרת — הפעולות של ההדגמה למנהל (tools/lab/demo.ps1, ‏Issue #70).
# נטען ב-dot-source אחרי demo-checks.ps1 ומשתמש בעזרים שבו.
#
# הופרד מ-demo-checks.ps1 כשזה עבר את מגבלת ~300 השורות: שם יושבות
# הקריאות הגולמיות (HTTP, ‏Hyper-V), וכאן שלוש הפעולות שההדגמה עושה
# בפועל — קריאת מצב ההפצה, כיבוי השרת, והדלקתו בחזרה.
#
# ‏Invoke-DemoBlackout היא הפעולה היחידה בכל ההדגמה שנוגעת בשרת עצמו,
# ולפניה שלושה שומרים: הקלדת מילה בחלון, בדיקה שאין הפצה פעילה,
# וכיבוי מסודר (לא ניתוק חשמל) שמאומת בקריאה חוזרת של המצב.

#: כמה שניות ממתינים לכיבוי המסודר של השרת לפני שמדווחים שלא הצלחנו.
$script:DemoStopWaitSeconds = 120

function Get-DemoMachineState {
    # מה שהשרת יודע על המכונה. עובר על **כל** כתובות הכרטיסים שלה
    # ועוצר על הראשונה שהשרת מכיר — מחשב הבנייה יושב על שתי רשתות,
    # ורק אחת מהן רשומה אצלו.
    #
    # ‏/api/v1/agent/state הוא קריאה בלבד: הוא לא רושם מגע, לא מקדם
    # סבב, ולא סופר אתחול. אפשר לשאול אותו כמה פעמים שרוצים.
    param([hashtable]$Cfg, [string]$Name)
    $m = Get-DemoVmMacs -Name $Name
    if (-not $m.Ok) {
        return [pscustomobject]@{ Ok = $false; Data = $null; Mac = ""; Reason = $m.Reason }
    }
    $reasons = @()
    foreach ($mac in $m.Macs) {
        $j = Get-DemoJson -Url "$($Cfg.ServerUrl)/api/v1/agent/state?mac=$mac"
        if (-not $j.Ok) { $reasons += "$mac — $($j.Reason)"; continue }
        if ($j.Data.known -eq $true) {
            return [pscustomobject]@{ Ok = $true; Data = $j.Data; Mac = $mac; Reason = "" }
        }
        $reasons += "$mac — השרת ענה 200, והמכונה הזו אינה רשומה אצלו"
    }
    [pscustomobject]@{ Ok = $false; Data = $null; Mac = ""
        Reason = ($reasons -join " · ") }
}

function Save-DemoBaseline {
    # "מה כבר היה בשרת לפני שהשלב התחיל" — נקרא בהפעלת השלב ונשמר.
    #
    # בלעדיו, עבודה ישנה שהסתיימה מזמן נראית בדיוק כמו זו שההדגמה הרגע
    # הריצה. זה לא תרחיש תיאורטי: מחשב הבנייה של המעבדה נושא קליטה
    # במצב done מסבב קודם, ובלי הבסיס הזה שלב 3 היה מכריז "עבר" לפני
    # שהמנהל נגע בכלום. מצב ישן שנשאר כשהיה אינו ראיה להצלחה.
    param([hashtable]$Cfg, [ValidateSet("buildtask", "session")][string]$Key)
    $Cfg.Remove("before_$Key")
    $id = ""
    if ($Key -eq "session") {
        $j = Get-DemoJson -Url "$($Cfg.ServerUrl)/api/v1/agent/sessions/active"
        if (-not $j.Ok) {
            return New-DemoResult "unknown" "לא הצלחנו לקרוא מהשרת מה קורה עכשיו" @($j.Reason) `
                "ודאו שהשרת דולק ועונה בכתובת $($Cfg.ServerUrl), ונסו שוב."
        }
        if ($null -ne $j.Data.session) { $id = "$($j.Data.session.id)" }
    } else {
        $st = Get-DemoMachineState -Cfg $Cfg -Name $Cfg.BuildVm
        if (-not $st.Ok) {
            return New-DemoResult "unknown" "השרת לא מכיר את מחשב הבנייה" @($st.Reason) `
                "ודאו שהשרת דולק, ושמחשב הבנייה רשום בטבלת המחשבים בקונסולה."
        }
        if ($null -ne $st.Data.task) { $id = "$($st.Data.task.id)" }
    }
    $Cfg["before_$Key"] = $id
    $what = if ($id) { "מה שכבר היה רשום בשרת: $id" } else { "בשרת לא היה רשום כלום" }
    New-DemoResult "pass" "נרשם מה שכבר קיים" @($what)
}

function Test-DemoIsNew {
    # האם מה שאנחנו רואים נוצר בשלב הזה, או שהיה כאן עוד קודם.
    param([hashtable]$Cfg, [string]$Key, [string]$Id)
    if (-not $Cfg.ContainsKey("before_$Key")) {
        return [pscustomobject]@{ Ok = $false
            Reason = "לא ידוע מה היה בשרת לפני השלב — הפעילו את השלב מחדש בכפתור." }
    }
    if ("$Id" -eq "$($Cfg["before_$Key"])") {
        return [pscustomobject]@{ Ok = $false
            Reason = "מה שרואים כאן היה בשרת עוד לפני שהשלב התחיל, ולכן אינו ראיה." }
    }
    [pscustomobject]@{ Ok = $true; Reason = "" }
}

function Test-DemoSessionFinished {
    # ההפצה כפי שהשרת רואה אותה — מונים שנקראו ממנו, לא מהמסך.
    # "כולם סיימו" נקבע לפי מספר המדווחים כמסיימים, ולא לפי היעדר
    # הודעת שגיאה: מגירה שנכשלה נספרת ומוצגת בשמה.
    #
    # בחדר השיכפולים ההפצה היא סבב של גלים, וכל גל הוא session בפני
    # עצמו. מה שנבדק כאן הוא הגל שרץ עכשיו — זה שההדגמה פתחה.
    param([hashtable]$Cfg, [ValidateSet("cloner", "classroom")][string]$Role)
    $j = Get-DemoJson -Url "$($Cfg.ServerUrl)/api/v1/agent/sessions/active"
    if (-not $j.Ok) {
        return New-DemoResult "unknown" "לא הצלחנו לשאול את השרת מה קורה" @($j.Reason) `
            "ודאו שהשרת דולק ושהכתובת $($Cfg.ServerUrl) נכונה."
    }
    $s = $j.Data.session
    if ($null -ne $s) {
        $fresh = Test-DemoIsNew -Cfg $Cfg -Key "session" -Id "$($s.id)"
        if (-not $fresh.Ok) {
            return New-DemoResult "fail" "ההפצה שרצה עכשיו אינה זו של השלב הזה" `
                @("השרת ענה 200", "לאן: $($s.group_label)", $fresh.Reason) `
                "המתינו לסיום ההפצה הקודמת או סגרו אותה מהקונסולה, ואז התחילו את זו של השלב."
        }
    }
    Test-DemoSessionData -Session $s -Role $Role
}

function Test-DemoSessionData {
    # הליבה של הבדיקה למעלה, בלי HTTP: מקבלת את הסבב כפי שהשרת מתאר
    # אותו ומחזירה את הפסק. טהורה בכוונה — ‏tests/test_demo_script.py
    # מריץ עליה תצוגת סבב אמיתית שנבנתה מ-server/session_view.py, ולכן
    # שינוי בשמות השדות שם נופל בטסטים ולא באמצע הדגמה.
    param($Session, [ValidateSet("cloner", "classroom")][string]$Role)
    $s = $Session
    if ($null -eq $s) {
        return New-DemoResult "fail" "השרת לא מכיר הפצה פעילה" @("השרת ענה 200, ואין אצלו הפצה") `
            "התחילו את ההפצה מהמסך של מחשב הבנייה, ואז לחצו 'בדוק שוב'."
    }
    $members = @($s.members)
    $done    = @($members | Where-Object { $_.done })
    $failed  = @($members | Where-Object { "$($_.state)" -eq "failed" })
    $ev = @("השרת ענה 200", "מה מופץ: $($s.image_name)", "לאן: $($s.group_label)",
            "השם שהשרת רשם למחשבים: $($s.prefix)",
            "מחשבים בהפצה: $($members.Count)",
            "סיימו: $($done.Count)", "נכשלו: $($failed.Count)")
    $result = $null
    if ("$($s.group_role)" -ne $Role) {
        $wanted = if ($Role -eq "classroom") { "לכיתה" } else { "לחדר השיכפולים" }
        $result = New-DemoResult "fail" "ההפצה שרצה עכשיו אינה $wanted" $ev `
            "סגרו אותה, או המתינו לה, והתחילו את ההפצה של השלב הזה."
    } elseif ($members.Count -eq 0) {
        $result = New-DemoResult "fail" "עוד לא הצטרף אף מחשב" $ev `
            "ודאו שהמחשבים דולקים ומחוברים לרשת של המערכת."
    } elseif ($failed.Count -gt 0) {
        $names = ($failed | ForEach-Object { "$($_.name) ($($_.error))" }) -join " · "
        $result = New-DemoResult "fail" "$($failed.Count) לא הצליחו" ($ev + @("שנכשלו: $names")) `
            "מה שנכשל מסומן בשמו. אפשר להריץ עליו שוב אחרי שמטפלים בו."
    } elseif ($done.Count -lt $members.Count) {
        $result = New-DemoResult "fail" "ההפצה עוד רצה — $($done.Count) מתוך $($members.Count)" $ev `
            "המתינו לסיום ולחצו 'בדוק שוב'."
    } else {
        $result = New-DemoResult "pass" "כל $($done.Count) המחשבים סיימו" $ev
    }
    $hostnames = @($members | Where-Object { $_.hostname } | ForEach-Object { $_.hostname })
    $result.Names = ($hostnames -join ", ")
    $result
}

function Stop-DemoServerVm {
    # כיבוי **מסודר** (לא ניתוק חשמל): מבקש מהשרת לכבות ומחכה שהמצב
    # שנקרא מהמארח יהיה Off. ‏-Force היה חוסך את ההמתנה, אבל הוא מושך
    # את התקע ממכונה שאולי כותבת עכשיו — ועל מעבדה חיה זה מיותר.
    # אם הכיבוי לא הסתיים בזמן, זה unknown עם הפקודה המדויקת למפעיל.
    param([hashtable]$Cfg)
    try { Stop-VM -Name $Cfg.ServerVm -ErrorAction Stop } catch {
        return New-DemoResult "unknown" "בקשת הכיבוי נכשלה" @($_.Exception.Message) `
            "כבו את '$($Cfg.ServerVm)' ידנית מחלון הניהול, או דלגו על השלב."
    }
    $deadline = (Get-Date).AddSeconds($script:DemoStopWaitSeconds)
    $state = "?"
    while ((Get-Date) -lt $deadline) {
        $after = Get-DemoVm -Name $Cfg.ServerVm
        if (-not $after.Ok) {
            return New-DemoResult "unknown" "לא הצלחנו לקרוא את מצב השרת" @($after.Reason)
        }
        $state = "$($after.Vm.State)"
        if ($state -eq "Off") { return New-DemoResult "pass" "השרת כבוי" @("המצב שנקרא מהמארח: Off") }
        if ("System.Windows.Forms.Application" -as [type]) {
            [System.Windows.Forms.Application]::DoEvents()
        }
        Start-Sleep -Seconds 2
    }
    New-DemoResult "unknown" "השרת עוד לא כבה" @("המצב שנקרא מהמארח: $state") `
        "אם צריך, כבו אותו בכוח מהמסוף:  Stop-VM $($Cfg.ServerVm) -Force"
}

function Invoke-DemoBlackout {
    # השלב היחיד שנוגע בשרת. שני שומרים לפניו: אישור בהקלדה (בחלון),
    # ובדיקה שאין הפצה פעילה. בדיקה שלא רצה עוצרת — לא מכבים שרת על
    # סמך שאלה שלא נענתה.
    param([hashtable]$Cfg)
    $j = Get-DemoJson -Url "$($Cfg.ServerUrl)/api/v1/agent/sessions/active"
    if (-not $j.Ok) {
        return New-DemoResult "unknown" "לא הצלחנו לבדוק אם השרת עסוק — לא מכבים" @($j.Reason) `
            "אם השרת כבר כבוי, אין מה להדגים כאן. ודאו שהוא דולק ועונה, ונסו שוב."
    }
    if ($null -ne $j.Data.session) {
        return New-DemoResult "fail" "יש הפצה פעילה עכשיו — לא מכבים את השרת" `
            @("השרת ענה 200", "הפצה פעילה: $($j.Data.session.group_label)") `
            "המתינו לסיום ההפצה, או סגרו אותה מהקונסולה, ואז חזרו לשלב הזה."
    }
    $v = Get-DemoVm -Name $Cfg.ServerVm
    if (-not $v.Ok) { return New-DemoResult "unknown" "לא ניתן לגשת לשרת" @($v.Reason) }

    $script:DemoServerStopped = $true      # לפני הכיבוי: גם ניסיון שנקטע
                                           # מחייב את ההדלקה בחזרה בסוף.
    $stop = Stop-DemoServerVm -Cfg $Cfg
    if ($stop.Status -ne "pass") { return $stop }

    $boot = Start-DemoVms -Names @($Cfg.BlackoutVm)
    $ev = @($stop.Evidence) + @($boot.Evidence)
    if ($boot.Status -ne "pass") {
        return New-DemoResult "unknown" "השרת כבוי, אבל מחשב הכיתה לא נדלק" $ev $boot.WhatToDo
    }
    New-DemoResult "pass" "השרת כבוי, ומחשב הכיתה נדלק" $ev
}

function Restore-DemoServer {
    # סוגר את שלב 8, וגם מה שכפתור "אפס" וסגירת החלון מריצים. הצלחה
    # נקבעת לפי תשובת 200 אמיתית מהשרת — לא לפי כך שההדלקה לא זרקה
    # שגיאה. ‏VM שעלה אינו שרת שעונה.
    param([hashtable]$Cfg)
    $manual = "אם השרת נשאר כבוי, הריצו במסוף כמנהל:  Start-VM $($Cfg.ServerVm)"
    $v = Get-DemoVm -Name $Cfg.ServerVm
    if (-not $v.Ok) { return New-DemoResult "unknown" "לא ניתן לגשת לשרת" @($v.Reason) $manual }
    if ($v.Vm.State -ne "Running") {
        try { Start-VM -Name $Cfg.ServerVm -ErrorAction Stop } catch {
            return New-DemoResult "fail" "השרת לא נדלק" @($_.Exception.Message) $manual
        }
    }
    $probe = Wait-DemoServerBack -BaseUrl $Cfg.ServerUrl -Seconds $Cfg.RestoreSeconds
    if ($null -eq $probe -or -not $probe.Ok) {
        $why = if ($null -eq $probe) { "לא בוצעה בדיקה" } else { $probe.Reason }
        return New-DemoResult "fail" "השרת עוד לא עונה" @("סיבה: $why") $manual
    }
    $script:DemoServerStopped = $false
    New-DemoResult "pass" "השרת חזר ועונה" `
        @("המצב שנקרא מהמארח: Running", "השרת ענה 200 לשאלה על מחשב")
}
