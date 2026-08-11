[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$HubDir = Join-Path $ProjectRoot "service-hub"
$VenvDir = Join-Path $HubDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$TokenPath = Join-Path $ProjectRoot "runtime\process-compose.token"
$EnvPath = Join-Path $ProjectRoot ".env"

function Resolve-Python {
    param([string]$Requested)

    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        if (Test-Path -LiteralPath $Requested -PathType Leaf) {
            return [pscustomobject]@{
                Executable = (Resolve-Path -LiteralPath $Requested).Path
                Arguments = @()
            }
        }
        $RequestedCommand = Get-Command $Requested -CommandType Application -ErrorAction SilentlyContinue
        if ($RequestedCommand) {
            return [pscustomobject]@{
                Executable = $RequestedCommand.Source
                Arguments = @()
            }
        }
        throw "Python executable could not be resolved: $Requested"
    }

    $Launcher = Get-Command "py" -CommandType Application -ErrorAction SilentlyContinue
    if ($Launcher) {
        return [pscustomobject]@{
            Executable = $Launcher.Source
            Arguments = @("-3")
        }
    }
    $Interpreter = Get-Command "python" -CommandType Application -ErrorAction SilentlyContinue
    if ($Interpreter) {
        return [pscustomobject]@{
            Executable = $Interpreter.Source
            Arguments = @()
        }
    }
    throw "Python 3 was not found. Install Python 3.11+ or pass -Python with an executable path."
}

$ResolvedPython = Resolve-Python $Python
$PythonExecutable = $ResolvedPython.Executable
$PythonArguments = @($ResolvedPython.Arguments)

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonExecutable @PythonArguments -m venv $VenvDir
}

$Requirements = if ($Dev) { "requirements-dev.txt" } else { "requirements.txt" }
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $HubDir $Requirements)

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $TokenPath) | Out-Null
if (-not (Test-Path -LiteralPath $TokenPath)) {
    $Bytes = New-Object byte[] 32
    $Generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Generator.GetBytes($Bytes)
    }
    finally {
        $Generator.Dispose()
    }
    $Token = ([BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
    [IO.File]::WriteAllText(
        $TokenPath,
        $Token,
        (New-Object Text.UTF8Encoding($false))
    )
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot ".env.example") -Destination $EnvPath
}

Write-Host "Service Hub environment is ready."
Write-Host "Bootstrap Python: $PythonExecutable"
Write-Host "Python: $VenvPython"
Write-Host "Token:  $TokenPath (value intentionally not printed)"
