[CmdletBinding()]
param(
    [ValidateRange(1, 600)]
    [int]$StartupTimeoutSeconds = 60,

    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $PSScriptRoot "start.ps1"
$LogDirectory = Join-Path $ProjectRoot "logs"
$LauncherLog = Join-Path $LogDirectory "desktop-launcher.log"
$HubUrl = "http://127.0.0.1:8750"
$HealthUrl = "$HubUrl/health"
$ControllerPort = 8751
$ControllerUrl = "http://127.0.0.1:$ControllerPort"
$TokenFile = Join-Path $ProjectRoot "runtime\process-compose.token"
$PowerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Write-LauncherLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    $Line = "[{0}] [PID {1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $PID, $Message
    try {
        Add-Content -LiteralPath $LauncherLog -Value $Line -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        # Logging must never prevent the launcher from opening the Hub.
    }
}

function Test-HubHealthy {
    try {
        $Response = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2
        return $Response.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Test-PortListening {
    param([Parameter(Mandatory = $true)][int]$Port)

    try {
        return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1)
    }
    catch {
        $Client = New-Object System.Net.Sockets.TcpClient
        try {
            $Pending = $Client.BeginConnect("127.0.0.1", $Port, $null, $null)
            if (-not $Pending.AsyncWaitHandle.WaitOne(500)) {
                return $false
            }
            $Client.EndConnect($Pending)
            return $true
        }
        catch {
            return $false
        }
        finally {
            $Client.Dispose()
        }
    }
}

function Open-Hub {
    if ($NoBrowser) {
        Write-LauncherLog "Health check passed; browser opening skipped by -NoBrowser."
        return
    }

    Start-Process -FilePath $HubUrl
    Write-LauncherLog "Opened $HubUrl in the default browser."
}

function Start-HubViaController {
    if (-not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
        throw "Process Compose token file does not exist: $TokenFile"
    }

    $Token = (Get-Content -LiteralPath $TokenFile -Raw -Encoding UTF8).Trim()
    if ($Token.Length -lt 20) {
        throw "Process Compose token is invalid."
    }

    $Headers = @{ "X-PC-Token-Key" = $Token }
    Invoke-RestMethod `
        -Uri "$ControllerUrl/process/start/service_hub" `
        -Method Post `
        -Headers $Headers `
        -TimeoutSec 5 | Out-Null
    Write-LauncherLog "Requested service_hub start from the existing controller."
}

try {
    Write-LauncherLog "Desktop launcher started."

    if (Test-HubHealthy) {
        Write-LauncherLog "Service Hub is already healthy."
        Open-Hub
        exit 0
    }

    $StartProcess = $null
    if (Test-PortListening -Port $ControllerPort) {
        Write-LauncherLog "Controller port $ControllerPort is already listening; requesting Service Hub start."
        try {
            Start-HubViaController
        }
        catch {
            # The Hub may already be in its startup window. Continue to the health
            # wait so a harmless 'already running' response does not block opening.
            Write-LauncherLog "Controller start request did not succeed immediately: $($_.Exception.Message)"
        }
    }
    else {
        if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
            throw "Start script does not exist: $StartScript"
        }
        if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
            throw "Windows PowerShell does not exist: $PowerShell"
        }

        $RunId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmssfff"), $PID
        $StartOutputLog = Join-Path $LogDirectory "desktop-start-$RunId.out.log"
        $StartErrorLog = Join-Path $LogDirectory "desktop-start-$RunId.err.log"
        $QuotedStartScript = '"' + $StartScript + '"'

        Write-LauncherLog "Service Hub is offline; starting scripts\start.ps1 in the background."
        $StartProcess = Start-Process `
            -FilePath $PowerShell `
            -ArgumentList @(
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", $QuotedStartScript
            ) `
            -WorkingDirectory $ProjectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $StartOutputLog `
            -RedirectStandardError $StartErrorLog `
            -PassThru

        Write-LauncherLog "Background start process created (PID $($StartProcess.Id)); stdout: $StartOutputLog; stderr: $StartErrorLog."
    }

    $Deadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        if (Test-HubHealthy) {
            Write-LauncherLog "Service Hub became healthy."
            Open-Hub
            exit 0
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $Deadline)

    $Detail = "Service Hub did not become healthy within $StartupTimeoutSeconds seconds."
    if ($null -ne $StartProcess -and $StartProcess.HasExited) {
        $Detail += " The background start process exited with code $($StartProcess.ExitCode)."
    }
    elseif (Test-PortListening -Port $ControllerPort) {
        $Detail += " Controller port $ControllerPort is listening, but the Hub health check is still failing."
    }

    throw $Detail
}
catch {
    Write-LauncherLog "ERROR: $($_.Exception.Message)"
    Write-Error "Unable to open Local Service Hub. $($_.Exception.Message) See $LauncherLog for details."
    exit 1
}
