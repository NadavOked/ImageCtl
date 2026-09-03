# עזרי הבדיקה של ההדגמה למנהל (tools/lab/demo.ps1, ‏Issue #70).
# נטען ב-dot-source. אין כאן חלונות, ואין כאן פעולה שמוחקת נתונים —
# רק קריאה מהשרת, קריאה מ-Hyper-V, והדלקת מכונות.
#
# עיקרון 5 חי בקובץ הזה. לכל בדיקה **שלוש** תשובות ולא שתיים:
#   pass    — יש ראיה חיובית: קוד תשובה, ערך שנקרא בחזרה, מונה.
#   fail    — בדקנו, והתוצאה אינה מה שציפינו.
#   unknown — הבדיקה **עצמה** לא רצה (אין הרשאה, השרת לא נענה).
# ‏unknown עוצר את ההדגמה בדיוק כמו fail, בניסוח משלו. "לא הצלחנו
# לבדוק" אינו "בדקנו והכל תקין".
#
# ‏-ErrorAction SilentlyContinue אסור כאן. הוא היה הופך "אין הרשאה
# ל-Hyper-V" ל"אף מכונה לא רצה" — בדיוק הדפוס שהפיל את השומר ב-#53.
# ‏(‏Get-Command עם -ErrorAction Ignore הוא היוצא מן הכלל היחיד, ושם
# ההיעדר עצמו הוא מה שנבדק ומדווח כ-unknown.)

function New-DemoResult {
    param(
        [ValidateSet("pass", "fail", "unknown")][string]$Status,
        [string]$Message,
        [string[]]$Evidence = @(),
        [string]$WhatToDo = ""
    )
    [pscustomobject]@{
        Status = $Status; Message = $Message
        Evidence = @($Evidence); WhatToDo = $WhatToDo
        # שמות המחשבים שההפצה תיתן — השאלה שנשאלת את הצופה בשלב 6
        # נבנית מהם, כדי שהיא תשווה מול מה שהשרת באמת רשם.
        Names = ""
    }
}

# --- השרת -------------------------------------------------------------------

function Invoke-DemoHttp {
    # לעולם לא זורק, אבל **מבדיל**: "השרת ענה קוד" (יש ראיה) מול
    # "לא הגענו אליו בכלל" (אין ראיה, ולכן unknown אצל הקורא).
    #
    # קוד התשובה נשלף מהחריגה בלי להישען על סוג החריגה: ‏PowerShell 5.1
    # זורק WebException ו-7 זורק HttpResponseException, ותפיסה של אחד
    # מהם בלבד הייתה מקפלת "השרת ענה 404" ל"לא הצלחנו לפנות אליו".
    param([string]$Url, [int]$TimeoutSec = 15)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return [pscustomobject]@{ Reached = $true; Code = [int]$r.StatusCode
                                  Body = [string]$r.Content; Error = "" }
    } catch {
        $err = $_
        $resp = $null
        try { $resp = $err.Exception.Response } catch { $resp = $null }
        if ($null -eq $resp) {
            return [pscustomobject]@{ Reached = $false; Code = 0; Body = ""
                                      Error = $err.Exception.Message }
        }
        $code = 0
        try { $code = [int]$resp.StatusCode } catch { $code = 0 }
        $body = ""
        if ($err.ErrorDetails -and $err.ErrorDetails.Message) {
            $body = [string]$err.ErrorDetails.Message
        } else {
            try {
                $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $body = $reader.ReadToEnd(); $reader.Close()
            } catch { $body = "" }
        }
        if ($code -eq 0) {
            # יש תשובה, אבל לא הצלחנו לקרוא ממנה קוד — זו אינה ראיה.
            return [pscustomobject]@{ Reached = $false; Code = 0; Body = $body
                                      Error = $err.Exception.Message }
        }
        return [pscustomobject]@{ Reached = $true; Code = $code; Body = $body
                                  Error = $err.Exception.Message }
    }
}

function Get-DemoJson {
    param([string]$Url)
    $r = Invoke-DemoHttp -Url $Url
    if (-not $r.Reached) {
        return [pscustomobject]@{ Ok = $false; Code = 0; Data = $null
            Reason = "לא הצלחנו לפנות לשרת ההדגמה: $($r.Error)" }
    }
    if ($r.Code -ne 200) {
        return [pscustomobject]@{ Ok = $false; Code = $r.Code; Data = $null
            Reason = "השרת ענה בקוד $($r.Code) במקום 200" }
    }
    try { $data = $r.Body | ConvertFrom-Json } catch {
        return [pscustomobject]@{ Ok = $false; Code = $r.Code; Data = $null
            Reason = "תשובת השרת הגיעה אבל אינה קריאה" }
    }
    [pscustomobject]@{ Ok = $true; Code = $r.Code; Data = $data; Reason = "" }
}

#: ‏MAC תקין בתבנית שהשרת מצפה לה, שאינו של אף מכונה במעבדה. משמש רק
#: לבדיקת "השרת עונה", ורק מול נתיב שאינו כותב דבר.
$script:DemoProbeMac = "02:00:00:00:00:00"

function Test-DemoServerAlive {
    # "השרת עונה" — ראיה חיובית, ובלי תופעת לוואי.
    #
    # מכוון **לא** ‎/boot/menu: כל בקשה שם נרשמת בשרת כמגע של המכונה
    # (‏net_seen), ולכן שאלה עם MAC מומצא הייתה שותלת מכונת רפאים
    # בטבלת "מה חי ברשת" של מעבדה חיה, ושאלה עם MAC אמיתי הייתה דורסת
    # את הכתובת הרשומה שלה בכתובת של מחשב ההדגמה. ‎/api/v1/agent/state
    # הוא קריאה בלבד — שאילתות SELECT ותו לא.
    param([string]$BaseUrl)
    $j = Get-DemoJson -Url "$BaseUrl/api/v1/agent/state?mac=$($script:DemoProbeMac)"
    if (-not $j.Ok) {
        return [pscustomobject]@{ Ok = $false; Reason = $j.Reason }
    }
    if ($null -eq $j.Data.mac) {
        return [pscustomobject]@{ Ok = $false
            Reason = "התשובה הגיעה בקוד 200 אבל אינה נראית כמו תשובה של השרת הזה" }
    }
    [pscustomobject]@{ Ok = $true; Reason = "" }
}

function ConvertFrom-DemoBootMenu {
    # קריאת שלוש השורות שמעניינות אותנו מתוך התפריט שהשרת מגיש:
    # ההחלטה שנרשמה, מאיפה המחשב עולה, והאם הוא רואה מסך בחירה.
    #
    # שלוש התחיליות נוצרות ב-boot/grub_menu.py::_assemble, והאורכים
    # שנחתכים כאן נגזרים מהן. ‏tests/test_demo_script.py מוודא שהם
    # תואמים — שינוי בצד השרת שישבור את הקריאה כאן ייתפס שם, בטסטים,
    # ולא מול קהל. פונקציה טהורה בכוונה, כדי שאפשר יהיה לבדוק אותה
    # מול פלט אמיתי של המחולל בלי שרת חי.
    param([string]$Body)
    $decision = ""; $default = ""; $style = ""
    foreach ($line in ($Body -split "`n")) {
        $t = $line.Trim()
        if ($t -like "# decision:*")        { $decision = $t.Substring(11).Trim() }
        elseif ($t -like "set default=*")   { $default  = $t.Substring(12).Trim() }
        elseif ($t -like "set timeout_style=*") { $style = $t.Substring(18).Trim() }
    }
    if (-not $default) {
        return [pscustomobject]@{ Ok = $false; Decision = $decision; Default = ""
            Style = $style; Reason = "התשובה הגיעה אבל אין בה שורת אתחול" }
    }
    [pscustomobject]@{ Ok = $true; Decision = $decision; Default = $default
                       Style = $style; Reason = "" }
}

function Get-DemoBootMenu {
    # מה שמחשב שעולה מקבל מהשרת. הראיה של שלב 7 (בדיקה 3.1 בתוכנית).
    #
    # ‏**לבקשה הזו יש תופעת לוואי בשרת**: היא נרשמת כמגע של המכונה,
    # והכתובת שתירשם לה בטבלת הרשת היא של מחשב ההדגמה עד האתחול הבא
    # שלה. לכן היא נשאלת רק על המכונה שהשלב עוסק בה, ורק פעם אחת —
    # ולא כדופק "השרת חי" (ראו Test-DemoServerAlive).
    param([string]$BaseUrl, [string]$Mac)
    $r = Invoke-DemoHttp -Url "$BaseUrl/boot/menu?mac=$Mac"
    if (-not $r.Reached) {
        return [pscustomobject]@{ Ok = $false; Decision = ""; Default = ""; Style = ""
            Reason = "השרת לא ענה: $($r.Error)" }
    }
    if ($r.Code -ne 200) {
        return [pscustomobject]@{ Ok = $false; Decision = ""; Default = ""; Style = ""
            Reason = "השרת ענה בקוד $($r.Code)" }
    }
    ConvertFrom-DemoBootMenu -Body $r.Body
}

function Wait-DemoServerBack {
    # ראיה חיובית שהשרת חזר: תשובת 200 אמיתית, לא היעדר שגיאה.
    param([string]$BaseUrl, [int]$Seconds = 240)
    $deadline = (Get-Date).AddSeconds($Seconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        $last = Test-DemoServerAlive -BaseUrl $BaseUrl
        if ($last.Ok) { return $last }
        if ("System.Windows.Forms.Application" -as [type]) {
            [System.Windows.Forms.Application]::DoEvents()
        }
        Start-Sleep -Seconds 3
    }
    return $last
}

# --- המכונות על המארח --------------------------------------------------------

function Get-DemoVm {
    # שלוש תשובות: יש מכונה כזו · אין מכונה בשם הזה · לא הצלחנו לבדוק.
    param([string]$Name)
    if (-not (Get-Command Get-VM -ErrorAction Ignore)) {
        return [pscustomobject]@{ Ok = $false; Vm = $null
            Reason = "‏Hyper-V אינו זמין כאן. יש להריץ את ההדגמה על מארח המעבדה, כמנהל." }
    }
    try { $all = @(Get-VM -ErrorAction Stop) } catch {
        return [pscustomobject]@{ Ok = $false; Vm = $null
            Reason = "לא ניתן לקרוא את רשימת המחשבים: $($_.Exception.Message). הרץ כמנהל." }
    }
    $vm = $all | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if (-not $vm) {
        return [pscustomobject]@{ Ok = $false; Vm = $null
            Reason = "אין מחשב בשם '$Name' על המארח. אפשר להחליף שם בפרמטרים של הסקריפט." }
    }
    [pscustomobject]@{ Ok = $true; Vm = $vm; Reason = "" }
}

function Get-DemoVmMacs {
    # **כל** כתובות הכרטיסים של המכונה, בתבנית שהשרת מצפה לה.
    # מחשב הבנייה והשרת יושבים על שתי רשתות, והשרת מכיר רק אחת מהן:
    # לקחת "את הכרטיס הראשון" היה שואל את השרת על מכונה שהוא לא מכיר
    # ומדווח "אין עבודה" בזמן שיש (ראו Get-DemoMachineState).
    param([string]$Name)
    $v = Get-DemoVm -Name $Name
    if (-not $v.Ok) { return [pscustomobject]@{ Ok = $false; Macs = @(); Reason = $v.Reason } }
    try { $adapters = @(Get-VMNetworkAdapter -VMName $Name -ErrorAction Stop) } catch {
        return [pscustomobject]@{ Ok = $false; Macs = @()
            Reason = "לא ניתן לקרוא את כרטיסי הרשת של '$Name': $($_.Exception.Message)" }
    }
    if ($adapters.Count -eq 0) {
        return [pscustomobject]@{ Ok = $false; Macs = @(); Reason = "ל-'$Name' אין כרטיס רשת" }
    }
    $macs = @()
    foreach ($a in $adapters) {
        $raw = "$($a.MacAddress)"
        # ‏Hyper-V מדווח אפסים על כרטיס דינמי שהמכונה שלו עוד לא עלתה.
        # שאלה על MAC כזה הייתה מחזירה תשובה על מכונה אחרת לגמרי.
        if ($raw.Length -eq 12 -and $raw -ne "000000000000") {
            $macs += ($raw -replace '(..)(?!$)', '$1:').ToLower()
        }
    }
    if ($macs.Count -eq 0) {
        return [pscustomobject]@{ Ok = $false; Macs = @()
            Reason = "לכרטיסי הרשת של '$Name' אין עדיין כתובת. הדליקו אותו והמתינו רגע." }
    }
    [pscustomobject]@{ Ok = $true; Macs = $macs; Reason = "" }
}

function Get-DemoVmMac {
    # כתובת אחת — לשלב שבו המכונה ממילא על רשת אחת בלבד.
    param([string]$Name)
    $m = Get-DemoVmMacs -Name $Name
    if (-not $m.Ok) { return [pscustomobject]@{ Ok = $false; Mac = ""; Reason = $m.Reason } }
    [pscustomobject]@{ Ok = $true; Mac = $m.Macs[0]; Reason = "" }
}

function Start-DemoVms {
    # מדליק, ואז **קורא בחזרה** את המצב. הדלקה בלי שגיאה אינה ראיה.
    param([string[]]$Names)
    $up = @(); $bad = @()
    foreach ($n in $Names) {
        $v = Get-DemoVm -Name $n
        if (-not $v.Ok) { $bad += "$n — $($v.Reason)"; continue }
        if ($v.Vm.State -ne "Running") {
            try { Start-VM -Name $n -ErrorAction Stop } catch {
                $bad += "$n — ההדלקה נכשלה: $($_.Exception.Message)"; continue
            }
        }
        $after = Get-DemoVm -Name $n
        if ($after.Ok -and $after.Vm.State -eq "Running") { $up += "$n — דולק" }
        else { $bad += "$n — לא הצלחנו לוודא שהוא דולק" }
    }
    if ($bad.Count -gt 0) {
        return New-DemoResult "unknown" "לא כל המחשבים נדלקו" ($up + $bad) `
            "פתחו את Hyper-V Manager, בדקו את המחשבים ברשימה, והדליקו ידנית."
    }
    New-DemoResult "pass" "$($up.Count) מחשבים דולקים" $up
}

function Get-DemoDiskUsedGB {
    # כמה תופס באמת הדיסק של המכונה — ראיה שנכתב עליו משהו.
    param([string]$Name)
    $v = Get-DemoVm -Name $Name
    if (-not $v.Ok) { return [pscustomobject]@{ Ok = $false; GB = 0; Reason = $v.Reason } }
    try {
        $disks = @(Get-VMHardDiskDrive -VMName $Name -ErrorAction Stop)
        if ($disks.Count -eq 0) {
            return [pscustomobject]@{ Ok = $false; GB = 0
                Reason = "ל-'$Name' אין דיסק מחובר" }
        }
        $size = (Get-VHD -Path $disks[0].Path -ErrorAction Stop).FileSize
    } catch {
        return [pscustomobject]@{ Ok = $false; GB = 0
            Reason = "לא ניתן לקרוא את גודל הדיסק של '$Name': $($_.Exception.Message)" }
    }
    [pscustomobject]@{ Ok = $true; Reason = ""
                       GB = [math]::Round($size / 1GB, 1) }
}
