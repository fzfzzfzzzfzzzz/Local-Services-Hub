[CmdletBinding()]
param(
    [string]$Version = "v1.120.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolDir = Join-Path $ProjectRoot "tools\process-compose"
$Asset = "process-compose_windows_amd64.zip"
$ZipPath = Join-Path $ToolDir $Asset
$ChecksumPath = Join-Path $ToolDir "process-compose_checksums.txt"
$ReleaseBase = "https://github.com/F1bonacc1/process-compose/releases/download/$Version"

New-Item -ItemType Directory -Force -Path $ToolDir | Out-Null
Invoke-WebRequest -Uri "$ReleaseBase/$Asset" -OutFile $ZipPath
Invoke-WebRequest -Uri "$ReleaseBase/process-compose_checksums.txt" -OutFile $ChecksumPath

$ChecksumLine = Get-Content $ChecksumPath | Where-Object { $_ -match [regex]::Escape($Asset) }
if (-not $ChecksumLine) {
    throw "The official checksum file does not contain $Asset"
}

$Expected = ($ChecksumLine -split "\s+")[0].ToLowerInvariant()
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
if ($Actual -ne $Expected) {
    throw "Checksum mismatch for $Asset. Expected $Expected, got $Actual"
}

Expand-Archive -LiteralPath $ZipPath -DestinationPath $ToolDir -Force
$Executable = Join-Path $ToolDir "process-compose.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "process-compose.exe was not found after extracting $Asset"
}

Write-Host "Installed Process Compose $Version"
Write-Host "SHA-256: $Actual"
& $Executable version

