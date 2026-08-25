param(
    [string]$TaskName = "SRV-Repair-Periodic-Sync",
    [int]$IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = (Get-Command python -ErrorAction Stop).Source
$SyncScript = Join-Path $ProjectRoot "scripts\run_sync_node.py"

if ($IntervalMinutes -lt 1) {
    throw "IntervalMinutes must be at least 1."
}
if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    throw "Configure $ProjectRoot\.env before installing the scheduled task."
}

$Arguments = "`"$SyncScript`""
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument $Arguments `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Periodically push local service records to the host and pull canonical changes." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName"
Write-Host "Interval: $IntervalMinutes minute(s)"
Write-Host "Run once now with:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
