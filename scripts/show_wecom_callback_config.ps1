Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $projectRoot ".env"
$sessionPath = Join-Path $projectRoot "tmp\wecom-callback\session.json"

function Get-DotEnvValue([string]$Key) {
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return ""
}

if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env was not found."
}
if (-not (Test-Path -LiteralPath $sessionPath)) {
    throw "No active callback test session was found."
}

$session = Get-Content -LiteralPath $sessionPath -Raw -Encoding UTF8 | ConvertFrom-Json
$token = Get-DotEnvValue "WECOM_CALLBACK_TOKEN"
$aesKey = Get-DotEnvValue "WECOM_CALLBACK_AES_KEY"
if ([string]::IsNullOrWhiteSpace($token) -or [string]::IsNullOrWhiteSpace($aesKey)) {
    throw "Callback Token or EncodingAESKey is missing."
}

Write-Host "Enter these values in WeCom > Receive messages > Set API receive:" -ForegroundColor Cyan
Write-Host ""
Write-Host "URL: $($session.callback_url)"
Write-Host "Token: $token"
Write-Host "EncodingAESKey: $aesKey"
Write-Host ""
Write-Host "Do not send these values in chat or screenshots." -ForegroundColor Yellow
Read-Host "Press Enter after WeCom reports that the callback was saved"
