# בונה דיסק ו-seed למכונת cloud-init של המעבדה — על המארח, בלי השרת.
#
#   .\tools\lab\build-cloud-vm.ps1 firewall
#   .\tools\lab\build-cloud-vm.ps1 college-dhcp
#
# הארגומנט הוא שם תיקיית ה-seed תחת tools/lab/ (זו שמכילה user-data,
# meta-data ו-network-config). הפלט:
#   <MachineRoot>\ImageCtl-<Vm>-disk1.vhdx   — אימג' הענן של דביאן, מוגדל
#   <StoreRoot>\<name>-seed.iso              — ה-seed, כ-ISO בשם CIDATA
#
# למה כאן ולא על ה-VM של השרת: ‏build-on-server.sh דורש SSH ל-10.98.10.8,
# והמפתח הפרטי לא נמצא על המארח (#47). הבנייה כאן לא מערבת את השרת
# ולא מפיצה מפתחות. תוכן ה-seed זהה — אותם קבצים בדיוק.
#
# דרישה: qemu-img (‏`winget install SoftwareFreedomConservancy.QEMU`).
# ‏ISO נבנה עם IMAPI2 של Windows — אין צורך ב-genisoimage.

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Vm,
    [string]$MachineRoot = "D:\ImageCtl-Lab",
    [string]$StoreRoot   = "C:\ImageCtl-Lab",
    [string]$SizeGb      = "8",
    [string]$QemuImg     = "C:\Program Files\qemu\qemu-img.exe",
    [string]$ImageUrl    = "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$seedDir = Join-Path $here $Vm
if (-not (Test-Path $seedDir)) { throw "אין תיקיית seed: $seedDir" }
if (-not (Test-Path $QemuImg)) {
    throw "qemu-img לא נמצא ב-$QemuImg. התקן: winget install SoftwareFreedomConservancy.QEMU"
}

# שם ה-VM נגזר מהתיקייה: college-dhcp -> CollegeDHCP, firewall -> Firewall.
$vmName = ($Vm -split '-' | ForEach-Object {
    if ($_ -eq "dhcp") { "DHCP" } else { $_.Substring(0,1).ToUpper() + $_.Substring(1) }
}) -join ""

New-Item -ItemType Directory -Force -Path $MachineRoot, $StoreRoot | Out-Null
$cache = Join-Path $StoreRoot "debian-13-genericcloud-amd64.qcow2"
$work  = Join-Path $env:TEMP "$Vm.qcow2"
$vhdx  = Join-Path $MachineRoot "ImageCtl-$vmName-disk1.vhdx"
$iso   = Join-Path $StoreRoot "$Vm-seed.iso"

# --- אימג' הענן -------------------------------------------------------------
# נשמר במטמון: ‏250MB, ומשמש את כל מכונות ה-cloud-init של המעבדה.
if (-not (Test-Path $cache) -or (Get-Item $cache).Length -lt 100MB) {
    Write-Host "מוריד את אימג' הענן של דביאן (פעם אחת, ~250MB)..."
    $old = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $ImageUrl -OutFile $cache -UseBasicParsing
    $ProgressPreference = $old
}
Write-Host "אימג' ענן: $cache ($([math]::Round((Get-Item $cache).Length/1MB)) MB)"

# ההגדלה עובדת על qcow2 ולא על vhdx — קודם מגדילים, אחר כך ממירים.
# דיסק קיים לא נדרס: הרצה חוזרת בונה רק את ה-seed. למי שבונה מחדש —
# למחוק אותו קודם ביד, כדי שהרצה בטעות לא תמחק מכונה שכבר עובדת.
if (Test-Path $vhdx) {
    Write-Host "הדיסק כבר קיים, מדלג: $vhdx"
} else {
    Copy-Item $cache $work -Force
    & $QemuImg resize $work "${SizeGb}G"
    if ($LASTEXITCODE -ne 0) { throw "qemu-img resize נכשל" }
    & $QemuImg convert -O vhdx -o subformat=dynamic $work $vhdx
    if ($LASTEXITCODE -ne 0) { throw "qemu-img convert נכשל" }
    Remove-Item $work -Force
    Write-Host "נבנה הדיסק: $vhdx"
}

# --- ה-seed -----------------------------------------------------------------
# ‏cloud-init במצב NoCloud מחפש מערכת קבצים בשם CIDATA. ‏IMAPI2 הוא
# צורב ה-ISO המובנה של Windows — כך אין תלות ב-genisoimage.
if (Test-Path $iso) { Remove-Item $iso -Force }
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fsi.FileSystemsToCreate = 3          # ISO9660 + Joliet
$fsi.VolumeName = "CIDATA"
foreach ($f in "user-data", "meta-data", "network-config") {
    $path = Join-Path $seedDir $f
    if (-not (Test-Path $path)) { throw "חסר קובץ seed: $path" }
    $fsi.Root.AddTree($path, $false)
}
$result = $fsi.CreateResultImage()
# ‏ImageStream הוא IStream של COM, ו-PowerShell לא יודע לקרוא ממנו ישירות —
# העתקה בלוק-בלוק דרך העוזר הזה היא הדרך הנתמכת.
if (-not ("ImageCtlIso" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class ImageCtlIso {
    public static void Save(string path, object comStream, int blockSize, int totalBlocks) {
        IStream source = (IStream)comStream;
        IntPtr got = Marshal.AllocHGlobal(4);
        try {
            using (FileStream target = File.Create(path)) {
                byte[] buffer = new byte[blockSize];
                while (totalBlocks-- > 0) {
                    source.Read(buffer, blockSize, got);
                    target.Write(buffer, 0, Marshal.ReadInt32(got));
                }
                target.Flush();
            }
        } finally { Marshal.FreeHGlobal(got); }
    }
}
"@
}
[ImageCtlIso]::Save($iso, $result.ImageStream, $result.BlockSize, $result.TotalBlocks)
Write-Host "נבנה ה-seed: $iso ($((Get-Item $iso).Length) bytes)"

Write-Host ""
Write-Host "הבא: .\tools\lab\setup-lab.ps1 — הוא יזהה את הדיסק ויצור את ImageCtl-$vmName."
Write-Host "לחבר את $iso כ-DVD להפעלה הראשונה."
