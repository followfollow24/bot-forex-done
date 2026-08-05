# deploy_recover_silent_close_fix.ps1 -- 2026-08-05 recover_position() fix.
#
# WHAT CHANGED (affects all 9 bots):
#   recover_position() runs on every startup. When a tracked position (from
#   state.json) was no longer at the broker -- closed while the bot was down,
#   or missed by the once-per-hour close-detection gap fixed earlier today --
#   it was silently dropped: a log warning only, NO Telegram alert, NO P&L.
#   Now calls the same best-effort _log_broker_close() used for a live-detected
#   close (real fill price via deal history where available, Telegram CLOSE
#   alert, P&L), with a 24h lookback since downtime length is unknown.
#   Also: an ADOPTED broker position (no local record) no longer hardcodes
#   entry_atr=0.0 (which permanently silenced its milestone alerts) -- it now
#   estimates from the buffer's latest ATR.
#
# SAFETY: same pattern as every deploy today -- captures each running
# process's REAL command line and relaunches that exact string, so no bot's
# config can drift here. Diffs open positions before/after the restart.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. snapshot open positions BEFORE (must match after) ===" -ForegroundColor Cyan
$before = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print('|'.join(sorted(f'{p.magic}:{p.symbol}:{p.type}:{p.volume}' for p in ps)) or 'NONE'); mt5.shutdown()"
Write-Host "  $before"

Write-Host "=== 2. capture each bot's REAL command line ===" -ForegroundColor Cyan
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" }
if ($bots.Count -eq 0) { Write-Host "ABORT: no bots running" -ForegroundColor Red; exit 1 }
Write-Host "  found $($bots.Count) bot process(es)"

$relaunch = @()
foreach ($b in $bots) {
    $cl = $b.CommandLine
    $args = $cl -replace '^\s*"[^"]+"\s*', ''
    if ($args -notmatch 'forex_live_bot_gold_cwider\.py') {
        Write-Host "ABORT: could not parse args from: $cl" -ForegroundColor Red
        exit 1
    }
    $tag = if ($args -match '--variant-tag\s+(\S+)') { $Matches[1] } else { "?" }
    $relaunch += [pscustomobject]@{ Pid = $b.ProcessId; Tag = $tag; Args = $args }
    Write-Host ("  PID {0,-6} {1}" -f $b.ProcessId, $tag)
}
if ($relaunch.Count -ne 9) {
    Write-Host "WARNING: expected 9 bots, captured $($relaunch.Count). Continuing anyway." -ForegroundColor Yellow
}

Write-Host "=== 3. pull + copy the changed file ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py $Desktop -Force

Write-Host "=== 4. stop all captured bots ===" -ForegroundColor Cyan
foreach ($r in $relaunch) {
    Write-Host "  stopping $($r.Tag) (PID $($r.Pid))"
    Stop-Process -Id $r.Pid -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5

Write-Host "=== 5. relaunch each with its OWN captured args ===" -ForegroundColor Cyan
Set-Location $Desktop
foreach ($r in $relaunch) {
    Write-Host "  starting $($r.Tag)"
    Start-Process python -ArgumentList $r.Args -WorkingDirectory $Desktop
    Start-Sleep -Seconds 4
}

Write-Host "=== 6. verify (waiting for warm-up) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 35

$after = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print('|'.join(sorted(f'{p.magic}:{p.symbol}:{p.type}:{p.volume}' for p in ps)) or 'NONE'); mt5.shutdown()"
$n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" } | Measure-Object).Count

Write-Host ""
Write-Host "  bots running : $n  (expected $($relaunch.Count))" -ForegroundColor $(if($n -eq $relaunch.Count){"Green"}else{"Red"})
Write-Host "  positions BEFORE: $before"
Write-Host "  positions AFTER : $after"
if ($before -eq $after) {
    Write-Host "  POSITIONS MATCH - nothing was lost or duplicated" -ForegroundColor Green
} else {
    Write-Host "  POSITION MISMATCH - INVESTIGATE BEFORE WALKING AWAY" -ForegroundColor Red
}

Write-Host "=== 7. confirm the new code is actually live ===" -ForegroundColor Cyan
$hasFix = Select-String -Path "$Desktop\forex_live_bot_gold_cwider.py" -Pattern "best-effort close report" -Quiet
Write-Host "  recover_position() silent-drop fix present : $hasFix"

Write-Host "=== 8. any RECOVER events this restart (should now show alerts sent, not just warnings) ===" -ForegroundColor Cyan
Get-ChildItem "$Desktop\forex_*.log" | ForEach-Object {
    $l = Select-String -Path $_ -Pattern "RECOVER|TELEGRAM" | Select-Object -Last 2
    if ($l) { Write-Host "--- $($_.Name) ---"; $l | ForEach-Object { Write-Host ("  " + $_.Line) } }
}
