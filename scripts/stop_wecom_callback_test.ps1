Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sessionPath = Join-Path $projectRoot "tmp\wecom-callback\session.json"
if (-not (Test-Path -LiteralPath $sessionPath)) {
    Write-Host "No active WeCom callback test session was found."
    exit 0
}

$session = Get-Content -LiteralPath $sessionPath -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($processId in @($session.gateway_pid, $session.tunnel_pid)) {
    if ($processId -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $processId -Force
    }
}
Remove-Item -LiteralPath $sessionPath -Force
Write-Host "WeCom callback test session stopped." -ForegroundColor Green
