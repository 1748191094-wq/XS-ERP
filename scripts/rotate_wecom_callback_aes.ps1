Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) { throw ".env was not found." }

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
$newKey = $result.ToString()

$lines = [System.Collections.Generic.List[string]]::new()
$found = $false
foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
    if ($line -match '^\s*WECOM_CALLBACK_AES_KEY=') {
        $lines.Add("WECOM_CALLBACK_AES_KEY=$newKey")
        $found = $true
    } else {
        $lines.Add($line)
    }
}
if (-not $found) { $lines.Add("WECOM_CALLBACK_AES_KEY=$newKey") }
[System.IO.File]::WriteAllLines(
    $envPath,
    $lines,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Output "rotated"
