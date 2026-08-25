param(
    [string]$OutputRoot = "deploy\artifacts\windows-no-python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Spec = Join-Path $ProjectRoot "packaging\windows_no_python.spec"
$Dist = Join-Path $ProjectRoot "build\windows-no-python\dist"
$Work = Join-Path $ProjectRoot "build\windows-no-python\work"
$Output = Join-Path $ProjectRoot $OutputRoot

& python -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $Dist `
    --workpath $Work `
    $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

$Built = Join-Path $Dist "ServiceManager"
if (-not (Test-Path -LiteralPath (Join-Path $Built "ServiceManager.exe"))) {
    throw "Built executable is missing."
}

New-Item -ItemType Directory -Path $Output -Force | Out-Null
Copy-Item -Path (Join-Path $Built "*") -Destination $Output -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "deploy\local\host.env.example") -Destination $Output -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "deploy\local\terminal.env.example") -Destination $Output -Force
Copy-Item -Path (Join-Path $ProjectRoot "deploy\local\README-*Python*.txt") -Destination $Output -Force
Copy-Item -Path (Join-Path $ProjectRoot "deploy\local\*Python*.cmd") -Destination $Output -Force

Write-Host "Windows no-Python package built at: $Output"
