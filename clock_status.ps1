# clock_status.ps1 -- is the clock-scalp bot actually working?
#
# A heartbeat is not proof of work. Earlier in this project four bots sat
# in an MT5 "IPC send failed" loop for seven days while every check said
# healthy, so this reports three things that can disagree with each other
# and shows all three rather than reducing them to one verdict:
#
#   PROCESS   is python running clock_scalp_bot.py, and only once
#   LOG       when did it last write -- a live process with a frozen log
#             is the failure mode that hid for a week
#   BROKER    what the terminal says is open, asked directly
#
# Read-only. Starts nothing, sends nothing, closes nothing.

$ErrorActionPreference = "Continue"
$repo = Join-Path $env:USERPROFILE "Desktop\bot_repo"
Set-Location $repo

Write-Host ""
Write-Host "  CLOCK SCALP STATUS  --  $(Get-Date -Format 'ddd dd MMM HH:mm:ss')" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------------"

$procs = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" |
           Where-Object { $_.CommandLine -like "*clock_scalp_bot*" })
if ($procs.Count -eq 0) {
    Write-Host "  PROCESS   NOT RUNNING -- nothing will trade at 19:30" -ForegroundColor Yellow
} else {
    foreach ($p in $procs) {
        $up = (Get-Date) - $p.CreationDate
        Write-Host ("  PROCESS   RUNNING  pid {0}  up {1:N1}h" -f $p.ProcessId, $up.TotalHours) -ForegroundColor Green
        $mode = if ($p.CommandLine -like "*--live*") { "LIVE -- real orders" } else { "DRY RUN -- sends nothing" }
        Write-Host ("            mode: {0}" -f $mode)
    }
    if ($procs.Count -gt 1) {
        Write-Host ("  WARNING   {0} copies are running -- they would each open a position" -f $procs.Count) -ForegroundColor Red
    }
}

$log = Join-Path $repo "clock_scalp.log"
if (Test-Path $log) {
    $age = (Get-Date) - (Get-Item $log).LastWriteTime
    $colour = if ($age.TotalHours -gt 25) { "Red" } else { "Green" }
    # The bot is idle by design between bells, so a quiet log is normal
    # until it passes a day. Anything past 25h means it missed a bell.
    Write-Host ("  LOG       last written {0:N1}h ago{1}" -f $age.TotalHours,
        $(if ($age.TotalHours -gt 25) { "  -- it has MISSED a bell" } else { "  (quiet between bells is normal)" })) -ForegroundColor $colour
} else {
    Write-Host "  LOG       clock_scalp.log does not exist yet" -ForegroundColor Yellow
}

if (Test-Path (Join-Path $repo "STOP_CLOCK_SCALP")) {
    Write-Host "  KILL      STOP_CLOCK_SCALP is present -- entries are BLOCKED" -ForegroundColor Yellow
} else {
    Write-Host "  KILL      no kill switch -- entries allowed"
}

& python _status.py

if (Test-Path $log) {
    Write-Host ""
    Write-Host "  last 15 log lines" -ForegroundColor Cyan
    Get-Content $log -Tail 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
}
Write-Host ""
