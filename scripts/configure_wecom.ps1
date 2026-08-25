Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $examplePath -Destination $envPath
}

function Get-DotEnvValue([string]$Key) {
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))=(.*)$") {
            return $Matches[1].Trim()
        }
    }
    return ""
}

function Set-DotEnvValue([string]$Key, [string]$Value) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Key))=") {
            $lines.Add("$Key=$Value")
            $found = $true
        } else {
            $lines.Add($line)
        }
    }
    if (-not $found) {
        $lines.Add("$Key=$Value")
    }
    [System.IO.File]::WriteAllLines(
        $envPath,
        $lines,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function New-RandomHex([int]$ByteCount) {
    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function New-EncodingAesKey {
    $alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    $result = [System.Text.StringBuilder]::new(43)
    $buffer = New-Object byte[] 1
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        while ($result.Length -lt 43) {
            $rng.GetBytes($buffer)
            if ($buffer[0] -lt 248) {
                [void]$result.Append($alphabet[$buffer[0] % $alphabet.Length])
            }
        }
    } finally {
        $rng.Dispose()
    }
    return $result.ToString()
}

Write-Host "WeCom local secure setup" -ForegroundColor Cyan
Write-Host "Values are written only to the local .env file." -ForegroundColor DarkGray

$currentCorpId = Get-DotEnvValue "WECOM_CORP_ID"
$corpId = Read-Host "CorpID (leave blank to keep current value)"
if ([string]::IsNullOrWhiteSpace($corpId)) { $corpId = $currentCorpId }

$currentAgentId = Get-DotEnvValue "WECOM_AGENT_ID"
$agentId = Read-Host "AgentId (leave blank to keep current value)"
if ([string]::IsNullOrWhiteSpace($agentId)) { $agentId = $currentAgentId }

$secureSecret = Read-Host "Application Secret (hidden; leave blank to keep current value)" -AsSecureString
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
try {
    $appSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
}
if ([string]::IsNullOrWhiteSpace($appSecret)) {
    $appSecret = Get-DotEnvValue "WECOM_APP_SECRET"
}

if ([string]::IsNullOrWhiteSpace($corpId) -or
    [string]::IsNullOrWhiteSpace($agentId) -or
    [string]::IsNullOrWhiteSpace($appSecret)) {
    throw "CorpID, AgentId, and Application Secret are required."
}

$callbackToken = Get-DotEnvValue "WECOM_CALLBACK_TOKEN"
if ([string]::IsNullOrWhiteSpace($callbackToken)) { $callbackToken = New-RandomHex 16 }
$callbackAesKey = Get-DotEnvValue "WECOM_CALLBACK_AES_KEY"
if ($callbackAesKey -notmatch '^[A-Za-z0-9]{43}$') { $callbackAesKey = New-EncodingAesKey }

Set-DotEnvValue "WECOM_MODE" "mock"
Set-DotEnvValue "WECOM_CORP_ID" $corpId.Trim()
Set-DotEnvValue "WECOM_AGENT_ID" $agentId.Trim()
Set-DotEnvValue "WECOM_APP_SECRET" $appSecret.Trim()
Set-DotEnvValue "WECOM_CALLBACK_TOKEN" $callbackToken
Set-DotEnvValue "WECOM_CALLBACK_AES_KEY" $callbackAesKey
Set-DotEnvValue "WECOM_CALLBACK_HOST" "127.0.0.1"
Set-DotEnvValue "WECOM_CALLBACK_PORT" "8011"

Write-Host ""
Write-Host "Configuration saved. Enter these values on the WeCom callback page:" -ForegroundColor Green
Write-Host "Token: $callbackToken"
Write-Host "EncodingAESKey: $callbackAesKey"
Write-Host ""
Write-Host "WECOM_MODE remains mock; no real reminder will be sent yet." -ForegroundColor Yellow
Read-Host "Press Enter after copying the callback values"
