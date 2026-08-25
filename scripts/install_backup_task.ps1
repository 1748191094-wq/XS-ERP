param(
    [string]$Time = "02:30",
    [string]$TaskName = "SERVICE-Daily-Verified-Backup",
    [switch]$Disabled
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = (Get-Command python -ErrorAction Stop).Source
$script = Join-Path $project "scripts\scheduled_backup.py"

if ($Time -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
    throw "Time must use HH:mm, for example 02:30"
}

$action = New-ScheduledTaskAction -Execute $python -Argument ('"{0}"' -f $script) -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "SERVICE verified SQLite daily backup" -Force | Out-Null
if ($Disabled) {
    Disable-ScheduledTask -TaskName $TaskName | Out-Null
}

Write-Host "Installed scheduled task: $TaskName at $Time (enabled=$(-not $Disabled))"
Write-Host "Run now: Start-ScheduledTask -TaskName '$TaskName'"
