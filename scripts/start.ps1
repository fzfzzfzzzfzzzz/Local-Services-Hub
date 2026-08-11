[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProcessCompose = Join-Path $ProjectRoot "tools\process-compose\process-compose.exe"
$Config = Join-Path $ProjectRoot "process-compose.yaml"
$GeneratedConfig = Join-Path $ProjectRoot "process-compose.generated.yaml"
$Token = Join-Path $ProjectRoot "runtime\process-compose.token"
$Log = Join-Path $ProjectRoot "logs\process-compose.log"
$HubPython = Join-Path $ProjectRoot "service-hub\.venv\Scripts\python.exe"

foreach ($RequiredPath in @($ProcessCompose, $Config, $Token, $HubPython)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required file does not exist: $RequiredPath. Run scripts\setup.ps1 first."
    }
}

& $HubPython (Join-Path $ProjectRoot "service-hub\generate_config.py")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate Process Compose business-service configuration."
}
if (-not (Test-Path -LiteralPath $GeneratedConfig)) {
    throw "Generated configuration does not exist: $GeneratedConfig"
}

$Existing = Get-NetTCPConnection -LocalPort 8751 -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    throw "Port 8751 is already in use. Refusing to start a duplicate controller."
}

Set-Location $ProjectRoot
& $ProcessCompose `
    -f $Config `
    -f $GeneratedConfig `
    --address 127.0.0.1 `
    -p 8751 `
    --token-file $Token `
    -L $Log `
    --log-no-color `
    --keep-project `
    up -t=false

exit $LASTEXITCODE
