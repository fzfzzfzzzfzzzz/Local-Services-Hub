[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VbsLauncher = Join-Path $PSScriptRoot "open-service-hub.vbs"
$Desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop)
# Build "本地服务中心" from Unicode code points so Windows PowerShell 5.1
# reads this UTF-8 script correctly even on a non-UTF-8 system code page.
$ShortcutName = "$([char]0x672C)$([char]0x5730)$([char]0x670D)$([char]0x52A1)$([char]0x4E2D)$([char]0x5FC3)"
$ShortcutPath = Join-Path $Desktop "$ShortcutName.lnk"

if ([string]::IsNullOrWhiteSpace($Desktop) -or -not (Test-Path -LiteralPath $Desktop -PathType Container)) {
    throw "Windows desktop directory could not be resolved."
}

if ($Remove) {
    if (Test-Path -LiteralPath $ShortcutPath -PathType Leaf) {
        Remove-Item -LiteralPath $ShortcutPath -Force
        Write-Host "Removed desktop shortcut: $ShortcutPath"
    }
    else {
        Write-Host "Desktop shortcut is not installed: $ShortcutPath"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $VbsLauncher -PathType Leaf)) {
    throw "VBS launcher does not exist: $VbsLauncher"
}

$WindowsScriptHost = "$env:SystemRoot\System32\wscript.exe"
if (-not (Test-Path -LiteralPath $WindowsScriptHost -PathType Leaf)) {
    throw "Windows Script Host does not exist: $WindowsScriptHost"
}

$Shell = New-Object -ComObject WScript.Shell
try {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $WindowsScriptHost
    $Shortcut.Arguments = '"' + $VbsLauncher + '"'
    $Shortcut.WorkingDirectory = $ProjectRoot
    $Shortcut.Description = "Start and open Local Service Hub"
    $Shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,15"
    $Shortcut.Save()
}
finally {
    if ($null -ne $Shortcut) {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shortcut)
    }
    [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell)
}

Write-Host "Installed desktop shortcut: $ShortcutPath"
