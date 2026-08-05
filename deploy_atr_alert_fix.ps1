# deploy_atr_alert_fix.ps1 -- 2026-08-05 real-time +/-1xATR alert fix.
#
# WHAT CHANGED (affects all 9 bots, they all run --manual-exit):
#   The ATR-milestone Telegram alert used to hang off process_bar(), which the
#   main loop only calls when a bar CLOSES -- once an HOUR on the H1 bots, once
#   a DAY on gold_daily_breakout, and evaluated against the closed bar's close.
#   A spike to +2xATR that retraced before the bar closed produced no alert at
#   all. It now runs from a new on_poll() hook every poll (~30s) against the
#   LIVE tick (bid for longs / ask for shorts, i.e. the price you'd actually
#   close at). The existing per-level ratchet still fires one message per whole
#   ATR level per trade -- verified in simulation: 160 polls over a path that
#   crossed +1, +2 and -1 xATR produced exactly 3 alerts, not 160.
#
# SAFETY: this script does NOT retype any bot's arguments. It captures each
# running process's actual command line, stops it, and relaunches that exact
# string -- so no bot can silently change config here. Open positions are
# recovered from each bot's state file on restart (normal, already-exercised
# path), and the single-instance lock prevents the watchdog double-launching
# anything mid-restart.
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
    # strip the leading quoted python.exe path, keep the script + all args
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

Write-Host "=== 3. pull + copy the two changed files ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py, gold_manual_exit_bot.py $Desktop -Force

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
$hasHook = Select-String -Path "$Desktop\forex_live_bot_gold_cwider.py" -Pattern "def on_poll" -Quiet
$hasTick = Select-String -Path "$Desktop\gold_manual_exit_bot.py" -Pattern "_live_price" -Quiet
Write-Host "  on_poll hook present in bot file      : $hasHook"
Write-Host "  live-tick pricing present in exit bot : $hasTick"

Write-Host "=== 8. any bot that recovered a position should log it ===" -ForegroundColor Cyan
Get-ChildItem "$Desktop\forex_*.log" | ForEach-Object {
    $l = Select-String -Path $_ -Pattern "\[STATE\] loaded|RECOVER" | Select-Object -Last 1
    if ($l) { Write-Host ("  {0,-42} {1}" -f $_.Name, $l.Line.Substring([Math]::Max(0,$l.Line.Length-60))) }
}
