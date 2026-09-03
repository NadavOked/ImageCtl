# החלון של ההדגמה למנהל — הרכיבים והדיאלוגים (tools/lab/demo.ps1, ‏Issue #70).
# נטען ב-dot-source אחרי ש-demo.ps1 טען את System.Windows.Forms.
#
# הופרד מ-demo.ps1 כדי לשמור על מגבלת ~300 השורות: כאן בניית רכיבים
# ושלושה דיאלוגים, ושם המצב וזרימת השלבים.
#
# הכל RTL: ‏RightToLeft + RightToLeftLayout על כל טופס, כמו בקונסולה.
# מצב בהיר, בלי צבעים מותאמים חוץ מצבע ההכרזה — שהוא המידע עצמו.

function New-DemoDialog {
    param([string]$Title, [int]$Height)
    $d = New-Object System.Windows.Forms.Form
    $d.Text = $Title
    $d.Size = New-Object System.Drawing.Size(600, $Height)
    $d.StartPosition = "CenterParent"
    $d.FormBorderStyle = "FixedDialog"
    $d.MinimizeBox = $false; $d.MaximizeBox = $false
    $d.RightToLeft = "Yes"; $d.RightToLeftLayout = $true
    $d.Font = New-Object System.Drawing.Font("Segoe UI", 11)
    $d
}

function New-DemoButton {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height = 46)
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $Text
    $b.SetBounds($X, $Y, $Width, $Height)
    $b
}

function New-DemoLabel {
    param([string]$Text, [int]$X, [int]$Y, [int]$Width, [int]$Height,
          [int]$Size = 11, [switch]$Bold)
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $Text
    $l.SetBounds($X, $Y, $Width, $Height)
    $style = if ($Bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    $l.Font = New-Object System.Drawing.Font("Segoe UI", $Size, $style)
    $l
}

function New-DemoReadOnlyBox {
    param([int]$X, [int]$Y, [int]$Width, [int]$Height, [int]$Size = 11,
          [switch]$Dim)
    $t = New-Object System.Windows.Forms.TextBox
    $t.SetBounds($X, $Y, $Width, $Height)
    $t.Multiline = $true; $t.ReadOnly = $true
    $t.ScrollBars = "Vertical"
    $t.BorderStyle = "FixedSingle"
    $t.Font = New-Object System.Drawing.Font("Segoe UI", $Size)
    $t.BackColor = if ($Dim) { [System.Drawing.Color]::WhiteSmoke }
                   else { [System.Drawing.Color]::White }
    $t
}

function Show-DemoConfirm {
    # פעולה שנוגעת בנתונים דורשת הקלדת מילה — לא לחיצה על "אישור"
    # (עיקרון 7). ההשוואה תלוית-רישיות ומתעלמת מרווחים בקצוות בלבד.
    param([string]$Title, [string]$Why, [string]$Word)
    $d = New-DemoDialog -Title $Title -Height 340
    $lbl = New-DemoLabel ($Why + "`r`n`r`nכדי להמשיך, הקלידו את המילה:  $Word") 20 20 540 170
    $box = New-Object System.Windows.Forms.TextBox
    $box.SetBounds(20, 198, 240, 32)
    $box.Font = New-Object System.Drawing.Font("Segoe UI", 12)
    $ok = New-DemoButton "המשך" 20 244 130
    $ok.Enabled = $false
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $no = New-DemoButton "ביטול" 170 244 130
    $no.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $box.Add_TextChanged({ $ok.Enabled = ($box.Text.Trim() -ceq $Word) }.GetNewClosure())
    $d.Controls.AddRange(@($lbl, $box, $ok, $no))
    $d.CancelButton = $no
    ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)
}

function Show-DemoYesNo {
    # מה שרק העין רואה (מסך שעלה, שם מחשב) נקבע בידי המפעיל, ולא
    # בהנחה של הסקריפט. אין ברירת מחדל: סגירת החלון היא "לא", כי
    # "לא נשאלנו" אינו "כן".
    param([string]$Question)
    $d = New-DemoDialog -Title "מה ראית?" -Height 270
    $lbl = New-DemoLabel $Question 20 20 540 120 12
    $yes = New-DemoButton "כן, ראיתי" 20 160 170
    $yes.DialogResult = [System.Windows.Forms.DialogResult]::Yes
    $no = New-DemoButton "לא" 210 160 170
    $no.DialogResult = [System.Windows.Forms.DialogResult]::No
    $d.Controls.AddRange(@($lbl, $yes, $no))
    $d.CancelButton = $no
    ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::Yes)
}

function Show-DemoNotice {
    # מה ההדגמה עומדת לעשות, לפני שהיא מתחילה — כדי שמי שמריץ אותה על
    # מעבדה שעובדת ידע מה נוגע במה. נפתח מהכפתור "מה זה עושה?".
    param([string]$Text)
    $d = New-DemoDialog -Title "מה ההדגמה עושה" -Height 520
    $box = New-DemoReadOnlyBox 20 20 540 400 11 -Dim
    $box.Lines = @($Text -split "`r`n")
    $ok = New-DemoButton "הבנתי" 20 430 130
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $d.Controls.AddRange(@($box, $ok))
    $d.CancelButton = $ok
    [void]$d.ShowDialog()
}
