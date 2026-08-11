[CmdletBinding()]
param(
    [string]$TaskName = "Local Service Hub"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $ProjectRoot "scripts\start.ps1"

if (-not (Test-Path -LiteralPath $StartScript)) {
    throw "Startup script does not exist: $StartScript"
}

$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$StartScript`""
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Arguments -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Starts Process Compose and Local Service Hub on Windows logon." `
    -Force | Out-Null

Write-Host "Scheduled task '$TaskName' was installed for user $env:USERNAME."

