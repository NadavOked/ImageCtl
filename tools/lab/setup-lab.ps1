# מתקן ה-VM (Issues #12, #13) — הטופולוגיה המלאה על המחשב החזק. PowerShell **כמנהל**;
# בטוח להריץ שוב — מדלג על מה שקיים.
#
#   powershell -ExecutionPolicy Bypass -File C:\ImageCtl\tools\lab\setup-lab.ps1
#
# שני datastores (מזוהים לבד, אפשר לעקוף):
#   -MachineRoot — VHDX של המכונות, על ה-NVMe הגדול (מהיר)
#   -StoreRoot   — ISOים, אימג'ים, והמגירה ה"איטית" של Cloner01, על ה-SATA
#
# שתי רשתות, כמו במכללה:
#   ImageCtl-Deploy — "וילן ההפצה": פרטי לגמרי (גם המארח לא עליו).
#   ImageCtl-LAN    — הרשת הרגילה 10.98.10.0/24: המארח 10.98.10.1, NAT לאינטרנט.
#
# דולקים תמיד (וגם Start at boot): השרת ומחשב הבנייה בלבד. כל השאר מוגדרים
# וכבויים — כל בדיקה מדליקה רק את שלה. שתי קבוצות להדלקה בפקודה אחת:
#   Start-VM -VM (Get-VMGroup "כיתה").VMMembers
#   Stop-VM  -VM (Get-VMGroup "חדר שיכפולים").VMMembers -Force

param([string]$MachineRoot, [string]$StoreRoot, [switch]$Force)

$ErrorActionPreference = "Stop"

$Deploy = "ImageCtl-Deploy"
$Lan    = "ImageCtl-LAN"
$Class  = "ImageCtl-Class"
$Server = "ImageCtl-Server"
$Build  = "ImageCtl-Build"

# --- סירוב לרוץ על מעבדה חיה --------------------------------------------------
# הסקריפט מכבה את השרת באמצע (חיווט הרשת) ומדליק אותו בסוף. אם הוא נופל
# בדרך — למשל Set-VM על מכונה שרצה, שהוא מסרב לה — השרת נשאר כבוי
# והקונסולה מתה בלי שאיש ביקש זאת. קרה ב-2026-08-28.
#
# לכן: מכונה שרצה = מעבדה חיה = עצירה, אלא אם ביקשו במפורש -Force.
# האזהרה חייבת להיות כאן ולא רק ב-README: קובץ נקרא פעם אחת, סקריפט
# נקרא בכל הרצה.
#
# ‏SilentlyContinue היה הופך "לא הצלחנו לבדוק" ל-"אף מכונה לא רצה":
# ‏Get-VM שנופל (אין הרשאת מנהל, שירות ה-VMMS מכובה) היה מחזיר רשימה
# ריקה, השער היה נפתח, והשרת היה נכבה — בדיוק מה שהשער נועד למנוע.
# שער שלא הצליח לבדוק עוצר, לא מוותר.
# ‏(תבנית עם * שלא מצאה דבר מחזירה רשימה ריקה בלי שגיאה — "בדקנו,
# אין" עובר כאן כרגיל. רק כשל אמיתי של הבדיקה מגיע ל-catch.)
try {
    $running = @(Get-VM "ImageCtl-*" -ErrorAction Stop |
                 Where-Object { $_.State -ne "Off" })
} catch {
    Write-Host ""
    Write-Host "לא ניתן לבדוק אם המעבדה חיה: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "הרץ כמנהל, או ודא ששירות Hyper-V (vmms) פועל." -ForegroundColor Yellow
    Write-Host ""
    throw "בדיקת המעבדה נכשלה — הרצה נעצרה. לא מכבים שרת על סמך בדיקה שלא רצה."
}
if ($running -and -not $Force) {
    Write-Host ""
    Write-Host "המעבדה חיה — $($running.Count) מכונות רצות:" -ForegroundColor Yellow
    $running | ForEach-Object { Write-Host "    $($_.Name)  [$($_.State)]" }
    Write-Host ""
    Write-Host "הסקריפט מכבה את השרת באמצע ומדליק אותו בסוף. אם הוא ייפול" -ForegroundColor Yellow
    Write-Host "בדרך, השרת יישאר כבוי והקונסולה תמות." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "לכבות הכל ולהריץ:   Stop-VM ImageCtl-* -Force; .\tools\lab\setup-lab.ps1"
    Write-Host "להריץ בכל זאת:      .\tools\lab\setup-lab.ps1 -Force"
    Write-Host "אם השרת נשאר כבוי:  Start-VM ImageCtl-Server"
    Write-Host ""
    throw "מעבדה חיה — הרצה נעצרה. ‏-Force כדי לעקוף."
}
if ($running -and $Force) {
    Write-Host "‏-Force: ממשיך למרות ש-$($running.Count) מכונות רצות." -ForegroundColor Yellow
    Write-Host "אם ההרצה תיפול באמצע — Start-VM ImageCtl-Server." -ForegroundColor Yellow
}

# --- זיהוי שני ה-datastores לפי סוג הכונן וגודלו ------------------------------
function Find-DataDrive($Bus, $MinGB, $What) {
    $sys = $env:SystemDrive.TrimEnd(':')
    foreach ($d in Get-PhysicalDisk |
             Where-Object { $_.BusType -eq $Bus -and $_.Size -gt $MinGB * 1GB }) {
        $letter = Get-Partition -DiskNumber ([int]$d.DeviceId) -ErrorAction SilentlyContinue |
                  Where-Object { $_.DriveLetter -and "$($_.DriveLetter)" -ne $sys } |
                  Select-Object -First 1 -ExpandProperty DriveLetter
        if ($letter) { return "${letter}:" }
    }
    throw "לא נמצא כונן $What ($Bus מעל ${MinGB}GB עם אות כונן). אפשר לעקוף: -MachineRoot / -StoreRoot"
}
if (-not $MachineRoot) { $MachineRoot = (Find-DataDrive "NVMe" 500  "המכונות") + "\ImageCtl-Lab" }
if (-not $StoreRoot)   { $StoreRoot   = (Find-DataDrive "SATA" 1000 "המאגר")   + "\ImageCtl-Store" }
New-Item -ItemType Directory -Force -Path $MachineRoot, $StoreRoot, "$StoreRoot\ISO" | Out-Null
Write-Host "מכונות: $MachineRoot | מאגר (ISO/אימג'ים/מגירה איטית): $StoreRoot"

# --- שני הסוויצ'ים ------------------------------------------------------------
if (-not (Get-VMSwitch -Name $Deploy -ErrorAction SilentlyContinue)) {
    New-VMSwitch -Name $Deploy -SwitchType Private | Out-Null
    Write-Host "נוצר וילן ההפצה (Private): $Deploy"
}
if (-not (Get-VMSwitch -Name $Lan -ErrorAction SilentlyContinue)) {
    New-VMSwitch -Name $Lan -SwitchType Internal | Out-Null
    Write-Host "נוצרה הרשת הרגילה (Internal): $Lan"
}
$nic = Get-NetAdapter -Name "vEthernet ($Lan)"
if (-not (Get-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress 10.98.10.1 `
          -ErrorAction SilentlyContinue)) {
    New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress 10.98.10.1 `
        -PrefixLength 24 | Out-Null
    Write-Host "המארח קיבל 10.98.10.1/24"
}
# וילן כיתה מדומה — Private, כמו וילן ההפצה: גם המארח לא עליו, וכל
# מה שיוצא ממנו עובר דרך ImageCtl-Firewall (בדיקות 3.8/3.9).
if (-not (Get-VMSwitch -Name $Class -ErrorAction SilentlyContinue)) {
    New-VMSwitch -Name $Class -SwitchType Private | Out-Null
    Write-Host "נוצר וילן הכיתה (Private): $Class"
}
if (-not (Get-NetNat -Name "$Lan-NAT" -ErrorAction SilentlyContinue)) {
    New-NetNat -Name "$Lan-NAT" -InternalIPInterfaceAddressPrefix "10.98.10.0/24" | Out-Null
    Write-Host "NAT על הרשת הרגילה — יש אינטרנט להתקנות"
}

# --- שרת: נוצר אם חסר; ה-VHDX המועתק מהמחשב הישן נתפס אוטומטית ---------------
$serverVhd = "$MachineRoot\$Server.vhdx"
if (-not (Get-VM -Name $Server -ErrorAction SilentlyContinue)) {
    New-VM -Name $Server -Generation 2 -MemoryStartupBytes 4GB `
        -SwitchName $Deploy -Path $MachineRoot -NoVHD | Out-Null
    Set-VM -Name $Server -AutomaticCheckpointsEnabled $false
    Set-VMProcessor -VMName $Server -Count 2
    if (Test-Path $serverVhd) {
        Add-VMHardDiskDrive -VMName $Server -Path $serverVhd
        Set-VMFirmware -VMName $Server `
            -FirstBootDevice (Get-VMHardDiskDrive -VMName $Server)[0] `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
        Write-Host "נוצר $Server סביב ה-VHDX המועתק"
    } else {
        Write-Host "נוצר $Server בלי דיסק — העתיקו את $Server.vhdx אל $MachineRoot והריצו שוב"
    }
} elseif ((Test-Path $serverVhd) -and -not (Get-VMHardDiskDrive -VMName $Server)) {
    Add-VMHardDiskDrive -VMName $Server -Path $serverVhd
    Set-VMFirmware -VMName $Server `
        -FirstBootDevice (Get-VMHardDiskDrive -VMName $Server)[0] `
        -SecureBootTemplate MicrosoftUEFICertificateAuthority
    Write-Host "ה-VHDX של השרת חובר ל-VM הקיים"
}
if ((Get-VM -Name $Server).State -ne "Off") {
    Stop-VM -Name $Server -Force
    Write-Host "השרת כובה לרגע לחיווט הרשת"
}
$adapters = @(Get-VMNetworkAdapter -VMName $Server)
Connect-VMNetworkAdapter -VMNetworkAdapter $adapters[0] -SwitchName $Deploy
Set-VMNetworkAdapter -VMNetworkAdapter $adapters[0] -StaticMacAddress "00155D100001"
if ($adapters.Count -lt 2) {
    Add-VMNetworkAdapter -VMName $Server -SwitchName $Lan -StaticMacAddress "00155D100002"
} else {
    Connect-VMNetworkAdapter -VMNetworkAdapter $adapters[1] -SwitchName $Lan
    Set-VMNetworkAdapter -VMNetworkAdapter $adapters[1] -StaticMacAddress "00155D100002"
}
Write-Host "לשרת שני כרטיסים: הפצה (MAC ...:01) ורגילה (MAC ...:02)"

# --- המכונות הריקות — עולות ב-PXE, בלי מערכת הפעלה ---------------------------
# SlowThird: המגירה השלישית נוצרת על ה-SATA — בידוד fanout עם הבדל מהירות
# אמיתי (סעיף 1.3 בתוכנית הסימולציה), בלי Storage QoS.
function New-LabVM($Name, $Nics, $DiskCount, $DiskGB, $MemMB, $SlowThird = $false,
                   $Gen1 = $false) {
    # ‏$Gen1: מחשבי השיכפול האמיתיים הם Legacy BIOS ללא UEFI ‏(#38) —
    # דור 1 עם כרטיס רשת legacy (בלעדיו אין PXE בדור 1) ובלי Secure Boot.
    if (Get-VM -Name $Name -ErrorAction SilentlyContinue) { return }
    $gen = if ($Gen1) { 1 } else { 2 }
    $first = $Nics[0]
    New-VM -Name $Name -Generation $gen -MemoryStartupBytes ($MemMB * 1MB) `
        -SwitchName $first.Switch -Path $MachineRoot -NoVHD | Out-Null
    if ($Gen1) {
        Get-VMNetworkAdapter -VMName $Name | Remove-VMNetworkAdapter
        Add-VMNetworkAdapter -VMName $Name -SwitchName $first.Switch `
            -IsLegacy $true -StaticMacAddress $first.Mac
    } else {
        Set-VMNetworkAdapter -VMName $Name -StaticMacAddress $first.Mac
    }
    foreach ($extra in $Nics | Select-Object -Skip 1) {
        Add-VMNetworkAdapter -VMName $Name -SwitchName $extra.Switch `
            -StaticMacAddress $extra.Mac
    }
    for ($i = 1; $i -le $DiskCount; $i++) {
        $dir = if ($SlowThird -and $i -eq 3) { $StoreRoot } else { $MachineRoot }
        $vhd = "$dir\$Name-disk$i.vhdx"
        if (-not (Test-Path $vhd)) {
            New-VHD -Path $vhd -SizeBytes ($DiskGB * 1GB) -Dynamic | Out-Null
        }
        Add-VMHardDiskDrive -VMName $Name -Path $vhd
    }
    Set-VMProcessor -VMName $Name -Count 2
    if ($Gen1) {
        Set-VMBios -VMName $Name -StartupOrder @("LegacyNetworkAdapter","IDE","CD","Floppy")
    } else {
        Set-VMFirmware -VMName $Name `
            -FirstBootDevice (Get-VMNetworkAdapter -VMName $Name)[0] `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
    }
    Set-VM -Name $Name -AutomaticCheckpointsEnabled $false
    # קונסולה טורית — הסוכן עונה על ttyS0 עם imagectl.debug=1
    Set-VMComPort -VMName $Name -Number 1 -Path "\\.\pipe\icl-$Name"
    Write-Host "נוצר: $Name (דור $gen, $DiskCount דיסקים, אתחול מהרשת)"
}

# חדר השיכפולים — 12 מחשבים, 3 מגירות כל אחד. ב-Cloner01 המגירה השלישית על ה-SATA.
# ‏256GB דינמי — אימג' שנקלט מדיסק 256 לא ישוחזר למגירה קטנה יותר (min_target).
$cloners = foreach ($i in 1..12) {
    $name = "ImageCtl-Cloner{0:D2}" -f $i
    $mac  = "00155D1000{0:X2}" -f (0x10 + $i)
    New-LabVM $name @(@{Switch=$Deploy; Mac=$mac}) 3 256 512 ($i -eq 1) -Gen1 $true
    $name
}
# הכיתה — 20 תחנות על וילן ההפצה; האחרונה 500GB לבדיקות ההרחבה והסינון (2.5-2.6)
$classes = foreach ($i in 1..20) {
    $name = "ImageCtl-Class{0:D2}" -f $i
    $mac  = "00155D1000{0:X2}" -f (0x20 + $i)
    New-LabVM $name @(@{Switch=$Deploy; Mac=$mac}) 1 $(if ($i -eq 20) { 500 } else { 256 }) 512
    $name
}
# שתי תחנות מחוץ לוילן ההפצה — ה-PXE של המעבדה לא אמור להגיע אליהן
New-LabVM "ImageCtl-Student-LAN1" @(@{Switch=$Lan; Mac="00155D100051"}) 1 256 512
New-LabVM "ImageCtl-Student-LAN2" @(@{Switch=$Lan; Mac="00155D100052"}) 1 256 512
# ‏"DHCP המכללה" — שרת הכתובות ה"קיים" של הרשת הרגילה (בדיקות 3.2/3.4).
# הדיסק נבנה מ-cloud image של דביאן + seed של cloud-init שמתקין dnsmasq
# ‏(DHCP בלבד, ‏10.98.10.100–140) — ראו tools/lab/college-dhcp/.
# הסקריפט רק יוצר את המכונה; את הדיסק וה-seed בונים לפי ה-README שם.
if (-not (Get-VM -Name "ImageCtl-CollegeDHCP" -ErrorAction SilentlyContinue)) {
    $cdhcpDisk = "$MachineRoot\ImageCtl-CollegeDHCP-disk1.vhdx"
    if (Test-Path $cdhcpDisk) {
        New-VM -Name "ImageCtl-CollegeDHCP" -Generation 2 -MemoryStartupBytes 512MB `
            -VHDPath $cdhcpDisk -SwitchName $Lan -Path $MachineRoot | Out-Null
        Set-VM -Name "ImageCtl-CollegeDHCP" -StaticMemory -AutomaticStartAction Nothing `
            -AutomaticStopAction ShutDown -CheckpointType Disabled
        Set-VMFirmware -VMName "ImageCtl-CollegeDHCP" `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
        Set-VMNetworkAdapter -VMName "ImageCtl-CollegeDHCP" -StaticMacAddress "00155D100061"
        Set-VMComPort -VMName "ImageCtl-CollegeDHCP" -Number 1 `
            -Path "\\.\pipe\icl-ImageCtl-CollegeDHCP"
        Write-Host "נוצר: ImageCtl-CollegeDHCP (DHCP המכללה, ‏10.98.10.2)"
    } else {
        Write-Host "דילוג על ImageCtl-CollegeDHCP — אין דיסק ב-$cdhcpDisk (ראו tools/lab/college-dhcp/)"
    }
}
# חומת האש — מימוש התייחסות ל-install/firewall-rules.md, ומתקן הבדיקה
# של 3.8/3.9. שתי רגליים: וילן הכיתה (10.97.0.1) והרשת הרגילה (10.98.10.9).
# בלי vTPM — הוא שבור על המארח הזה. הדיסק וה-seed נבנים לפי
# tools/lab/firewall/build-on-server.sh, כמו ב-college-dhcp.
if (-not (Get-VM -Name "ImageCtl-Firewall" -ErrorAction SilentlyContinue)) {
    $fwDisk = "$MachineRoot\ImageCtl-Firewall-disk1.vhdx"
    if (Test-Path $fwDisk) {
        New-VM -Name "ImageCtl-Firewall" -Generation 2 -MemoryStartupBytes 1GB `
            -VHDPath $fwDisk -SwitchName $Class -Path $MachineRoot | Out-Null
        Set-VM -Name "ImageCtl-Firewall" -StaticMemory -AutomaticStartAction Nothing `
            -AutomaticStopAction ShutDown -CheckpointType Disabled
        Set-VMFirmware -VMName "ImageCtl-Firewall" `
            -SecureBootTemplate MicrosoftUEFICertificateAuthority
        Set-VMNetworkAdapter -VMName "ImageCtl-Firewall" -StaticMacAddress "00155D100071"
        Add-VMNetworkAdapter -VMName "ImageCtl-Firewall" -SwitchName $Lan `
            -StaticMacAddress "00155D100072"
        Set-VMComPort -VMName "ImageCtl-Firewall" -Number 1 `
            -Path "\\.\pipe\icl-ImageCtl-Firewall"
        Write-Host "נוצר: ImageCtl-Firewall (כיתה 10.97.0.1 / רגילה 10.98.10.9)"
    } else {
        Write-Host "דילוג על ImageCtl-Firewall — אין דיסק ב-$fwDisk (ראו tools/lab/firewall/)"
    }
}
# מחשב הבנייה — שני כרטיסים, כמו במציאות
New-LabVM $Build @(
    @{Switch=$Deploy; Mac="00155D100041"},
    @{Switch=$Lan;    Mac="00155D100042"}) 1 40 4096

# --- קבוצות להדלקה וכיבוי בפקודה אחת -----------------------------------------
function Sync-Group($GName, $Members) {
    $g = Get-VMGroup -Name $GName -ErrorAction SilentlyContinue
    if (-not $g) { $g = New-VMGroup -Name $GName -GroupType VMCollectionType }
    $have = @($g.VMMembers.Name)
    foreach ($m in $Members | Where-Object { $_ -notin $have }) {
        Add-VMGroupMember -VMGroup $g -VM (Get-VM -Name $m)
    }
}
Sync-Group "חדר שיכפולים" $cloners
Sync-Group "כיתה" $classes

# --- מדיניות עלייה וכיבוי: שרת ובנייה תמיד; כל השאר כבויים עד שבדיקה מדליקה ---
Set-VM -Name $Server -AutomaticStartAction Start -MemoryStartupBytes 4GB
Set-VM -Name $Build  -AutomaticStartAction Start
Get-VM "ImageCtl-*" | Where-Object { $_.Name -notin @($Server, $Build) } |
    ForEach-Object {
        Set-VM -Name $_.Name -AutomaticStartAction Nothing -AutomaticStopAction TurnOff
    }
# קונסולה טורית לכל מכונה כבויה — גם כאלה שנוצרו לפני הסקריפט הזה
Get-VM "ImageCtl-*" | Where-Object State -eq "Off" | ForEach-Object {
    Set-VMComPort -VMName $_.Name -Number 1 -Path "\\.\pipe\icl-$($_.Name)"
}

if (Test-Path $serverVhd) { Start-VM -Name $Server; Start-VM -Name $Build }
Write-Host "`nהטופולוגיה מוכנה: 12 שיכפול + 20 כיתה + 2 מחוץ לוילן, כבויים."
Write-Host "השלמת רשת בתוך דביאן (אם זו התקנה טרייה) — ראו tools/lab/README.md"
