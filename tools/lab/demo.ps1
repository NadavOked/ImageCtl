# ההדגמה למנהל — שמונה שלבים, בעברית (Issue #70).
#
#   powershell -ExecutionPolicy Bypass -File C:\ImageCtl\tools\lab\demo.ps1
#
# חלון אחד: הוראה בעברית, כפתור להפעלת השלב, כפתור בדיקה, חזרה שלב
# אחורה, ואיפוס. הטקסט שמוקרא למנהל נטול מונחים טכניים; הראיות
# הטכניות יושבות בתיבה נפרדת, למי שרוצה לראות אותן.
#
# ‏**בטוח להרצה על מעבדה חיה** — בתנאי אחד, שההדגמה אומרת בקול בחלון
# הפתיחה. בניגוד ל-setup-lab.ps1, שמכבה את השרת באמצע כדי לחווט רשת
# ואסור להריץ אותו על מעבדה שעובדת, ההדגמה אינה משנה תצורה של אף
# מכונה: היא מדליקה מחשבים, קוראת מהשרת, ובודקת. ארבעה שלבים כן
# נוגעים בנתונים, וכל אחד מהם מאחורי **הקלדת מילה** (עיקרון 7):
#   שלב 1 — הדלקת המחשב עם התקליטור מוחקת את הדיסק שלו.
#   שלב 4 — ההפצה לחדר השיכפולים כותבת על המגירות.
#   שלב 6 — ההפצה לכיתה כותבת על מחשבי הכיתה שבהדגמה.
#   שלב 8 — **מכבה את השרת** (כיבוי מסודר), ומדליק בחזרה בסוף השלב.
#           מסרב לכבות כשיש הפצה פעילה, וגם כשלא הצליח לבדוק אם יש.
#
# הצלחה נקבעת כאן לפי ראיה חיובית בלבד — קוד תשובה מהשרת, ערך שנקרא
# בחזרה מ-Hyper-V, מונה מחשבים שסיימו (עיקרון 5). בדיקה שלא רצה
# מדווחת בכתום כ"לא הצלחנו לבדוק", ואינה מקדמת את ההדגמה.
#
# שמות המכונות הם ברירות מחדל של מעבדת ה-VM ‏(tools/lab/README.md);
# מול חומרה אמיתית מעבירים אותם בפרמטרים.

param(
    [string]$ServerUrl  = "http://10.98.10.8:8080",
    [string]$GoldenVm   = "ImageCtl-Class01",
    [string]$BuildVm    = "ImageCtl-Build",
    [string[]]$ClonerVms = @("ImageCtl-Cloner01", "ImageCtl-Cloner02", "ImageCtl-Cloner03"),
    [string]$RestoredVm = "ImageCtl-Class02",
    [string[]]$ClassVms = @("ImageCtl-Class04", "ImageCtl-Class05",
                            "ImageCtl-Class06", "ImageCtl-Class07"),
    [string]$OutsideVm  = "ImageCtl-Student-LAN1",
    [string]$BlackoutVm = "ImageCtl-Class03",
    [string]$ServerVm   = "ImageCtl-Server",
    [int]$MinInstalledGB = 3,
    [int]$RestoreSeconds = 240
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

. "$PSScriptRoot\demo-checks.ps1"
. "$PSScriptRoot\demo-actions.ps1"
. "$PSScriptRoot\demo-steps.ps1"
. "$PSScriptRoot\demo-ui.ps1"

$script:Cfg = @{
    ServerUrl = $ServerUrl.TrimEnd('/'); GoldenVm = $GoldenVm; BuildVm = $BuildVm
    ClonerVms = $ClonerVms; RestoredVm = $RestoredVm; ClassVms = $ClassVms
    OutsideVm = $OutsideVm; BlackoutVm = $BlackoutVm; ServerVm = $ServerVm
    MinInstalledGB = $MinInstalledGB; RestoreSeconds = $RestoreSeconds
}
$script:Steps = @(Get-DemoSteps -Cfg $script:Cfg)
$script:Index = 0
$script:Armed = @{}
$script:Results = @{}
$script:DemoServerStopped = $false

function Get-DemoNoticeText {
    # מה נוגע במה — נבנה מהשלבים עצמם, כדי שלא ייפרד מהם.
    $lines = @(
        "ההדגמה מדליקה מחשבים, קוראת מהשרת, ובודקת. היא אינה משנה את",
        "התצורה של אף מכונה, ואינה נוגעת בהגדרות הרשת.",
        "",
        "השלבים הבאים כן משנים נתונים, וכל אחד מהם יבקש הקלדת מילה",
        "לפני שהוא רץ:",
        ""
    )
    for ($i = 0; $i -lt $script:Steps.Count; $i++) {
        $s = $script:Steps[$i]
        if ($s.Confirm) { $lines += "  שלב $($i + 1) — $($s.Title): $($s.Confirm.Why)"; $lines += "" }
    }
    $lines += @(
        "שלב 7 שואל את השרת מה הוא היה עונה למחשב שברשת הרגילה. השאלה",
        "נרשמת אצלו כמגע של אותו מחשב, ובטבלת הרשת תופיע לו הכתובת של",
        "מחשב ההדגמה עד האתחול הבא שלו.",
        "",
        "אם מישהו אחר עובד עכשיו על המעבדה — שלב 8 יפריע לו. ההדגמה",
        "מסרבת לכבות את השרת כשרצה בו הפצה, וגם כשלא הצליחה לבדוק."
    )
    $lines -join "`r`n"
}

# --- החלון --------------------------------------------------------------------

$script:Form = New-Object System.Windows.Forms.Form
$script:Form.Text = "ImageCtl — הדגמה"
$script:Form.Size = New-Object System.Drawing.Size(940, 780)
$script:Form.StartPosition = "CenterScreen"
$script:Form.RightToLeft = "Yes"; $script:Form.RightToLeftLayout = $true
$script:Form.Font = New-Object System.Drawing.Font("Segoe UI", 11)

$script:LblTitle    = New-DemoLabel "" 20 15 880 40 16 -Bold
$script:TxtDo       = New-DemoReadOnlyBox 20 60 880 220 13
$script:LblStatus   = New-DemoLabel "" 20 292 880 56 13 -Bold
$script:LblEvidence = New-DemoLabel "מה נבדק בפועל:" 20 352 880 24
$script:TxtEvidence = New-DemoReadOnlyBox 20 378 880 200 11 -Dim

$script:BtnArm    = New-DemoButton "הפעל את השלב" 20 595 250
$script:BtnCheck  = New-DemoButton "סיימתי — בדוק" 290 595 220
$script:BtnBack   = New-DemoButton "שלב קודם" 530 595 130
$script:BtnReset  = New-DemoButton "אפס" 20 651 120
$script:BtnNotice = New-DemoButton "מה זה עושה?" 160 651 170
$script:BtnClose  = New-DemoButton "סגור" 350 651 120

$script:Form.Controls.AddRange(@(
    $script:LblTitle, $script:TxtDo, $script:LblStatus, $script:LblEvidence,
    $script:TxtEvidence, $script:BtnArm, $script:BtnCheck, $script:BtnBack,
    $script:BtnReset, $script:BtnNotice, $script:BtnClose))

# --- הצגה ובקרה ---------------------------------------------------------------

function Set-DemoStatus {
    param([string]$Text, [string]$Color, [string[]]$Evidence = @(), [string]$WhatToDo = "")
    $script:LblStatus.Text = $Text
    $script:LblStatus.ForeColor = [System.Drawing.Color]::FromName($Color)
    $lines = @($Evidence)
    if ($WhatToDo) { $lines = $lines + @("", "מה עושים: $WhatToDo") }
    $script:TxtEvidence.Lines = $lines
    $script:Form.Refresh()
}

function Show-DemoResult {
    # שלושה צבעים לשלושה מצבים. כתום אינו ירוק חיוור: "לא הצלחנו
    # לבדוק" הוא מצב בפני עצמו, והוא אינו מקדם את ההדגמה.
    param($Result)
    $color = "DarkOrange"; $head = "לא הצלחנו לבדוק — "
    if ($Result.Status -eq "pass") { $color = "DarkGreen"; $head = "עבר — " }
    elseif ($Result.Status -eq "fail") { $color = "Firebrick"; $head = "נעצר — " }
    Set-DemoStatus ($head + $Result.Message) $color $Result.Evidence $Result.WhatToDo
}

function Show-DemoStep {
    $s = $script:Steps[$script:Index]
    $script:LblTitle.Text = "שלב $($script:Index + 1) מתוך $($script:Steps.Count)  ·  $($s.Title)"
    $script:TxtDo.Lines = @($s.Text -split "`r`n")
    $script:BtnArm.Visible = [bool]$s.Arm
    if ($s.Arm) {
        $script:BtnArm.Text = $s.ArmLabel
        $script:BtnArm.Enabled = -not $script:Armed.ContainsKey($script:Index)
    }
    $script:BtnBack.Enabled = ($script:Index -gt 0)
    $done = $script:Results.ContainsKey($script:Index) -and
            $script:Results[$script:Index].Status -eq "pass"
    if ($done -and $script:Index -lt $script:Steps.Count - 1) {
        $script:BtnCheck.Text = "לשלב הבא"
    } elseif ($done) { $script:BtnCheck.Text = "סיום ההדגמה" }
    else { $script:BtnCheck.Text = "סיימתי — בדוק" }
    if ($script:Results.ContainsKey($script:Index)) {
        Show-DemoResult $script:Results[$script:Index]
    } else { Set-DemoStatus "" "Black" @() }
}

function Invoke-DemoBlock {
    # שגיאה לא צפויה בתוך בדיקה או הפעלה לא מפילה את החלון מול קהל,
    # וגם לא נבלעת: היא הופכת ל"לא הצלחנו לבדוק" עם הטקסט המקורי.
    param([scriptblock]$Block)
    try { & $Block $script:Cfg } catch {
        New-DemoResult "unknown" "הבדיקה עצמה נכשלה" @($_.Exception.Message) `
            "זו תקלה בהדגמה, לא במערכת. אפשר להמשיך לשלב הבא ידנית."
    }
}

function Invoke-DemoStepCheck {
    $s = $script:Steps[$script:Index]
    Set-DemoStatus "בודק..." "Gray" @()
    $eyesAnswer = $null
    $eyesText = ""
    # ‏EyesFirst: שלב שהבדיקה שלו מחזירה את המצב לקדמותו (שלב 8 מדליק
    # את השרת בחזרה) חייב לשאול את העין לפני כן. אפשרי רק כששאלת
    # העין קבועה מראש ואינה נגזרת מתוצאת הבדיקה.
    if ($s.EyesFirst -and ($s.Eyes -is [string])) {
        $eyesText = [string]$s.Eyes
        $eyesAnswer = Show-DemoYesNo $eyesText
    }
    $r = Invoke-DemoBlock $s.Check
    if ($r.Status -eq "pass" -and $s.Eyes) {
        if ($null -eq $eyesAnswer) {
            $eyesText = if ($s.Eyes -is [scriptblock]) { & $s.Eyes $script:Cfg $r }
                        else { [string]$s.Eyes }
            $eyesAnswer = Show-DemoYesNo $eyesText
        }
        if ($eyesAnswer) {
            $r.Evidence = @($r.Evidence) + @("המפעיל אישר בעיניו: $eyesText")
        } else {
            $r = New-DemoResult "fail" "מה שעל המסך אינו מה שציפינו" `
                (@($r.Evidence) + @("המפעיל ענה 'לא' על: $eyesText")) $s.EyesNo
        }
    }
    $script:Results[$script:Index] = $r
    Show-DemoResult $r
    if ($r.Status -eq "pass") { Show-DemoStep }
}

function Invoke-DemoArm {
    $s = $script:Steps[$script:Index]
    if ($s.Confirm) {
        if (-not (Show-DemoConfirm $s.Title $s.Confirm.Why $s.Confirm.Word)) {
            Set-DemoStatus "השלב לא הופעל — האישור לא הוקלד" "DarkOrange" @()
            return
        }
    }
    Set-DemoStatus "מפעיל..." "Gray" @()
    $r = Invoke-DemoBlock $s.Arm
    Show-DemoResult $r
    if ($r.Status -eq "pass") {
        $script:Armed[$script:Index] = $true
        $script:BtnArm.Enabled = $false
    }
}

function Invoke-DemoAdvance {
    $done = $script:Results.ContainsKey($script:Index) -and
            $script:Results[$script:Index].Status -eq "pass"
    if (-not $done) { Invoke-DemoStepCheck; return }
    if ($script:Index -lt $script:Steps.Count - 1) { $script:Index++; Show-DemoStep }
    else { Set-DemoStatus "ההדגמה הסתיימה. כל שמונת השלבים עברו." "DarkGreen" @() }
}

function Invoke-DemoReset {
    $script:Index = 0; $script:Armed = @{}; $script:Results = @{}
    # גם מה שנרשם כ"מה כבר היה בשרת" נמחק: ריצה שנייה שתירש בסיס
    # מהריצה הקודמת הייתה מקבלת את העבודה של הריצה הראשונה כאילו היא
    # חדשה. אחרי איפוס צריך להפעיל כל שלב מחדש.
    foreach ($key in @($script:Cfg.Keys | Where-Object { $_ -like "before_*" })) {
        $script:Cfg.Remove($key)
    }
    Show-DemoStep
    if ($script:DemoServerStopped) {
        Set-DemoStatus "ההדגמה כיבתה את השרת — מדליק אותו בחזרה..." "DarkOrange" @()
        Show-DemoResult (Restore-DemoServer -Cfg $script:Cfg)
    }
}

$script:BtnArm.Add_Click({ Invoke-DemoArm })
$script:BtnCheck.Add_Click({ Invoke-DemoAdvance })
$script:BtnBack.Add_Click({ if ($script:Index -gt 0) { $script:Index--; Show-DemoStep } })
$script:BtnReset.Add_Click({ Invoke-DemoReset })
$script:BtnNotice.Add_Click({ Show-DemoNotice (Get-DemoNoticeText) })
$script:BtnClose.Add_Click({ $script:Form.Close() })

$script:Form.Add_FormClosing({
    # השרת לא נשאר כבוי בגלל שההדגמה נסגרה באמצע.
    if (-not $script:DemoServerStopped) { return }
    if (Show-DemoYesNo "ההדגמה כיבתה את השרת והוא עדיין כבוי. להדליק אותו עכשיו?") {
        $r = Restore-DemoServer -Cfg $script:Cfg
        if ($r.Status -ne "pass") {
            [System.Windows.Forms.MessageBox]::Show(
                "$($r.Message)`r`n`r`n$($r.WhatToDo)", "השרת נשאר כבוי") | Out-Null
        }
    }
})

$script:Form.Add_Shown({
    Show-DemoStep
    Show-DemoNotice (Get-DemoNoticeText)
    $probe = Test-DemoServerAlive -BaseUrl $script:Cfg.ServerUrl
    if (-not $probe.Ok) {
        Set-DemoStatus "השרת בכתובת $($script:Cfg.ServerUrl) לא עונה" "DarkOrange" `
            @($probe.Reason) "בדקו שהשרת דולק, או העבירו כתובת אחרת ב--ServerUrl."
    } else {
        Set-DemoStatus "מוכן. השרת עונה בכתובת $($script:Cfg.ServerUrl)" "DarkGreen" `
            @("השרת ענה 200 לשאלה על מחשב")
    }
})

[System.Windows.Forms.Application]::EnableVisualStyles()
[void]$script:Form.ShowDialog()
