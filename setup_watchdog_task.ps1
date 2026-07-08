# =============================================================================
# setup_watchdog_task.ps1 - run ONCE to register watchdog.ps1 to fire every
# 5 minutes via Windows Task Scheduler.
# =============================================================================
# ASCII-ONLY ON PURPOSE: this file must parse even when PowerShell 5.1 reads it
# as ANSI (git delivers UTF-8 without BOM; PS 5.1 then mis-decodes any non-ASCII
# byte and throws a parse error before the task can be registered). Keep it
# English-only so it never depends on BOM/encoding fixes.
#
# Usage (run ONCE, from an elevated interactive Administrator PowerShell):
#   cd C:\Users\Administrator\Desktop
#   .\setup_watchdog_task.ps1
#
# Verify:
#   Get-ScheduledTask -TaskName "ForexBotWatchdog"
#   (Get-ScheduledTask -TaskName "ForexBotWatchdog").Principal
#
# Remove:
#   Unregister-ScheduledTask -TaskName "ForexBotWatchdog" -Confirm:$false
# =============================================================================

$TaskName   = "ForexBotWatchdog"
$ScriptPath = "$env:USERPROFILE\Desktop\watchdog.ps1"

if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERROR: $ScriptPath not found -- put watchdog.ps1 on the Desktop first" -ForegroundColor Red
    exit 1
}

# Remove any existing task first (so re-running this script is idempotent).
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Task '$TaskName' already exists -- removing before re-creating" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -MultipleInstances IgnoreNew

# Run as the INTERACTIVE logged-on user (NOT SYSTEM). Root cause of the
# 2026-07-08 incident: SYSTEM launches the restarted bot into session 0, which
# cannot attach to the MT5 terminal running in the interactive RDP session ->
# mt5.initialize() fails -> the bot dies before writing a heartbeat -> watchdog
# restarts every 5 min forever (confirmed 17h crash-loop, stale counter climbing
# 800->805 min). Interactive logon spawns the bot in the SAME session as MT5, so
# restarts actually stick. This matches how the bots must run anyway (they need
# the interactive MT5 GUI terminal), so no coverage is lost: if the user is
# logged off, neither the bots nor MT5 run, and no watchdog is needed.
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null

Write-Host "Registered Task Scheduler '$TaskName' -- runs watchdog.ps1 every 5 min (Interactive/Administrator)" -ForegroundColor Green
Write-Host "Verify: (Get-ScheduledTask -TaskName '$TaskName').Principal" -ForegroundColor Cyan
Write-Host "Remove: Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Cyan
