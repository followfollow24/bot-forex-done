# =============================================================================
# deploy_now.ps1 - ONE-SHOT deploy of commit 8aa3192 (lock->tempdir + heartbeat)
# =============================================================================
# APPROVED to run WITH open positions (2026-07-06): both bots held an open
# LONG 0.03 when this deploy was authorized. recover_position() re-adopts any
# broker position matching the bot's magic on restart, and broker-side SL/TP
# stay active during the brief restart window. This script therefore does NOT
# flat-check (unlike deploy.ps1) -- that gate was intentionally waived by the
# operator for this run.
#
# Order matters: stop the old bots FIRST, THEN delete the old Desktop .lock
# files (they are held open by the running process; deleting before stopping
# would fail "file in use"), THEN start the new bots.
# =============================================================================
$ErrorActionPreference = "Continue"
$DESKTOP = "$env:USERPROFILE\Desktop"
$REPO    = "$DESKTOP\bot_repo"

Write-Host "=== [1] git pull + copy new code ===" -ForegroundColor Cyan
Set-Location $REPO
git pull
Copy-Item "$REPO\*.py" $DESKTOP -Force
Copy-Item "$REPO\watchdog.ps1" $DESKTOP -Force -ErrorAction SilentlyContinue

Write-Host "`n=== [2] stop running bots ===" -ForegroundColor Yellow
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }
if ($running) {
    $running | ForEach-Object {
        Write-Host "  Stop-Process -Id $($_.ProcessId) ($($_.CommandLine))"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 4
} else {
    Write-Host "  (none running)"
}

Write-Host "`n=== [3] delete OLD Desktop lock files (new code uses temp dir) ===" -ForegroundColor Yellow
foreach ($lk in @("xauusd_adx20tp7.lock", "xauusd_adx18tp7.lock", "xauusd_cwider.lock")) {
    $p = "$DESKTOP\$lk"
    if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue; Write-Host "  deleted $lk" }
}

Write-Host "`n=== [4] start both bots (--risk 0.30 --allow-real) ===" -ForegroundColor Yellow
Set-Location $DESKTOP
Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3
Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 10

Write-Host "`n=== [5] verify processes ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" } |
  Select-Object ProcessId, CommandLine | Format-List

Write-Host "=== [6] heartbeat files (should update within ~30s) ===" -ForegroundColor Cyan
Get-ChildItem "$DESKTOP\HEARTBEAT_*" -ErrorAction SilentlyContinue |
  Select-Object Name, LastWriteTime | Format-Table -AutoSize

Write-Host "=== [7] new-temp-dir lock files ===" -ForegroundColor Cyan
Get-ChildItem "$env:TEMP\forexbot_*.lock" -ErrorAction SilentlyContinue |
  Select-Object FullName | Format-List

Write-Host "`n=== deploy_now complete -- check each bot console for: REAL-MONEY CONFIRMED, [RECOVER] adopted position, balance ===" -ForegroundColor Green
