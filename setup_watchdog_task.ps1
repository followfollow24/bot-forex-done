# =============================================================================
# setup_watchdog_task.ps1 — รันครั้งเดียวเพื่อตั้ง watchdog.ps1 ให้ยิงอัตโนมัติ
# ทุก 5 นาทีผ่าน Windows Task Scheduler
# =============================================================================
# วิธีใช้ (รันครั้งเดียวพอ):
#   cd C:\Users\Administrator\Desktop
#   .\setup_watchdog_task.ps1
#
# เช็คว่าตั้งสำเร็จ:
#   Get-ScheduledTask -TaskName "ForexBotWatchdog"
#
# ลบ task (ถ้าต้องการปิด watchdog):
#   Unregister-ScheduledTask -TaskName "ForexBotWatchdog" -Confirm:$false
# =============================================================================

$TaskName   = "ForexBotWatchdog"
$ScriptPath = "$env:USERPROFILE\Desktop\watchdog.ps1"

if (-not (Test-Path $ScriptPath)) {
    Write-Host "ERROR: ไม่พบ $ScriptPath — วาง watchdog.ps1 ไว้ที่ Desktop ก่อน" -ForegroundColor Red
    exit 1
}

# ลบ task เดิมถ้ามีอยู่แล้ว (กันซ้ำเวลารันสคริปต์นี้ซ้ำ)
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "พบ task '$TaskName' เดิมอยู่แล้ว — ลบก่อนตั้งใหม่" -ForegroundColor Yellow
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
# restarts actually stick. This also matches how the bots must run anyway (they
# need the interactive MT5 GUI terminal), so no coverage is lost: if the user is
# logged off, neither the bots nor MT5 run, and no watchdog is needed.
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null

Write-Host "ตั้ง Task Scheduler '$TaskName' สำเร็จ — จะรัน watchdog.ps1 ทุก 5 นาที (Interactive/Administrator)" -ForegroundColor Green
Write-Host "ตรวจสอบ: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "ปิด/ลบ:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Cyan
