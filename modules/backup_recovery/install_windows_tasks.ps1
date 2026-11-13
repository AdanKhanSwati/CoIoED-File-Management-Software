# Review configuration and permissions before running as Administrator.
# SYSTEM must read database, attachments and key, and write both backup destinations.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = (Get-Command python -ErrorAction Stop).Source
$Script = Join-Path $Root 'backup_manager.py'
$Config = Join-Path $Root 'backup_config.json'
if (!(Test-Path $Config)) { throw 'Create backup_config.json from backup_config.example.json first' }
$Action = { param($args) New-ScheduledTaskAction -Execute $Py -Argument ('"' + $Script + '" --config "' + $Config + '" ' + $args) -WorkingDirectory $Root }
$Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$T = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName 'CoIoED Backup - 15 Minute' -Action (& $Action 'backup --tier snapshot') -Trigger $T -Principal $Principal -Settings $Settings -Force
Register-ScheduledTask -TaskName 'CoIoED Backup - Nightly' -Action (& $Action 'backup --tier nightly') -Trigger (New-ScheduledTaskTrigger -Daily -At '11:30PM') -Principal $Principal -Settings $Settings -Force
Register-ScheduledTask -TaskName 'CoIoED Backup - Weekly' -Action (& $Action 'backup --tier weekly') -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '1:00AM') -Principal $Principal -Settings $Settings -Force
Register-ScheduledTask -TaskName 'CoIoED Backup - Daily Health' -Action (& $Action 'health') -Trigger (New-ScheduledTaskTrigger -Daily -At '8:00AM') -Principal $Principal -Settings $Settings -Force
Write-Host 'Four CoIoED tasks installed. Check task history, event log and storage permissions. Add offsite air-gapped rotation separately.'
