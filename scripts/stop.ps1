[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProcessCompose = Join-Path $ProjectRoot "tools\process-compose\process-compose.exe"
$Token = Join-Path $ProjectRoot "runtime\process-compose.token"

if (-not (Test-Path -LiteralPath $ProcessCompose)) {
    throw "Process Compose is not installed."
}
if (-not (Test-Path -LiteralPath $Token)) {
    throw "Process Compose token file does not exist."
}

& $ProcessCompose -p 8751 --token-file $Token down
exit $LASTEXITCODE

