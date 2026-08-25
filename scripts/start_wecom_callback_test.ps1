Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$thirdPartyName = [string]::Concat([char]0x7B2C, [char]0x4E09, [char]0x65B9, [char]0x8F6F, [char]0x4EF6)
$thirdPartyDir = Join-Path (Split-Path $projectRoot -Parent) $thirdPartyName
$cloudflared = Join-Path $thirdPartyDir "cloudflared\cloudflared.exe"
$runtimeDir = Join-Path $projectRoot "tmp\wecom-callback"
$sessionPath = Join-Path $runtimeDir "session.json"
$gatewayOut = Join-Path $runtimeDir "gateway.out.log"
$gatewayErr = Join-Path $runtimeDir "gateway.err.log"
$tunnelOut = Join-Path $runtimeDir "tunnel.out.log"
$tunnelErr = Join-Path $runtimeDir "tunnel.err.log"

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    throw "WeCom is not configured. Run the WeCom configuration launcher first."
}
if (-not (Test-Path -LiteralPath $cloudflared)) {
    throw "Third-party cloudflared.exe is missing. Install the test tunnel tool first."
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = Join-Path $env:LocalAppData "Programs\Python\Python311\python.exe"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "The project Python 3.11 runtime was not found."
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
foreach ($log in @($gatewayOut, $gatewayErr, $tunnelOut, $tunnelErr)) {
    if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
}

$gateway = Start-Process -FilePath $python `
    -ArgumentList @("scripts\run_wecom_callback.py") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $gatewayOut `
    -RedirectStandardError $gatewayErr `
    -PassThru

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if ($gateway.HasExited) { break }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8011/health" -TimeoutSec 2
        if ($health.status -eq "ok" -and $health.configured) {
            $healthy = $true
            break
        }
    } catch {}
}
if (-not $healthy) {
    if (-not $gateway.HasExited) { Stop-Process -Id $gateway.Id -Force }
    throw "Callback gateway failed to start or is not configured. Check $gatewayErr"
}

$tunnel = Start-Process -FilePath $cloudflared `
    -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8011") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $tunnelOut `
    -RedirectStandardError $tunnelErr `
    -PassThru

$publicBase = ""
for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Milliseconds 500
    if ($tunnel.HasExited) { break }
    $content = ""
    if (Test-Path -LiteralPath $tunnelErr) {
        $content += Get-Content -LiteralPath $tunnelErr -Raw -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tunnelOut) {
        $content += Get-Content -LiteralPath $tunnelOut -Raw -ErrorAction SilentlyContinue
    }
    if ($content -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
        $publicBase = $Matches[0]
        break
    }
}
if ([string]::IsNullOrWhiteSpace($publicBase)) {
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id -Force }
    if (-not $gateway.HasExited) { Stop-Process -Id $gateway.Id -Force }
    throw "Temporary HTTPS tunnel failed to start. Check $tunnelErr"
}

$session = [ordered]@{
    gateway_pid = $gateway.Id
    tunnel_pid = $tunnel.Id
    public_base_url = $publicBase
    callback_url = "$publicBase/wecom/callback"
    started_at = (Get-Date).ToString("o")
}
$session | ConvertTo-Json | Set-Content -LiteralPath $sessionPath -Encoding UTF8

Write-Host "WeCom callback test session started" -ForegroundColor Green
Write-Host "URL: $($session.callback_url)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Keep this computer online. The temporary URL changes after restart and is test-only." -ForegroundColor Yellow
