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

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -User "SYSTEM" | Out-Null

Write-Host "ตั้ง Task Scheduler '$TaskName' สำเร็จ — จะรัน watchdog.ps1 ทุก 5 นาที" -ForegroundColor Green
Write-Host "ตรวจสอบ: Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Cyan
Write-Host "ปิด/ลบ:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor Cyan
