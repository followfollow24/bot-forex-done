# deploy_tp5.ps1 -- 2026-08-07: add a broker-side safety TP at 5xATR to all bots.
#
# WHY: with --tp-atr 999 there is effectively no take-profit, so a position that
# runs far into profit can round-trip all the way back to the stop while the
# user is asleep or away. The +/-ATR Telegram alerts do not protect against
# this -- they need the bot process to be alive and are only a notification.
# A TP is submitted to MT5 as part of the entry order, so the BROKER closes the
# position even if the bot process hangs (which happened to btc_h1_manual on
# 2026-08-06).
#
# TRADEOFF (measured, adx10/touch0.012 live configs, real costs, full history):
#   BTC   Sharpe 1.30 -> 1.26   CAGR +21.5% -> +16.2%   DD 22.3% -> 17.9%
#   GOLD  Sharpe 0.58 -> 0.32   CAGR  +6.3% ->  +1.6%   DD 23.9% -> 15.9%
#   ETH   Sharpe 0.21 -> 0.20   CAGR  +0.8% ->  +0.7%   DD 11.0% -> 11.2%
# Gold gives up the most because its edge depends on a few large winners.
# Accepted deliberately by the user in exchange for unattended downside safety.
#
# SAFETY: does NOT retype any bot's arguments. Captures each running process's
# real command line and only rewrites the --tp-atr value, so no other parameter
# can drift. Refuses to run if ANY bot currently holds an open position (an
# open position keeps the TP it was opened with -- restarting would not add a
# TP to it, and the mismatch would be silent and confusing).
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. refuse to run while ANY bot holds a position ===" -ForegroundColor Cyan
$open = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print(len(ps)); mt5.shutdown()"
Write-Host "  open positions: $open"
if ([int]$open -ne 0) {
    Write-Host "ABORT: close them first (an already-open position keeps its original TP=999)." -ForegroundColor Red
    exit 1
}
Write-Host "  all flat, safe to proceed" -ForegroundColor Green

Write-Host "=== 2. capture each bot's REAL command line ===" -ForegroundColor Cyan
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" }
if ($bots.Count -eq 0) { Write-Host "ABORT: no bots running" -ForegroundColor Red; exit 1 }
Write-Host "  found $($bots.Count) bot process(es)"

$relaunch = @()
foreach ($b in $bots) {
    $args = $b.CommandLine -replace '^\s*"[^"]+"\s*', ''
    if ($args -notmatch 'forex_live_bot_gold_cwider\.py') {
        Write-Host "ABORT: could not parse args from: $($b.CommandLine)" -ForegroundColor Red
        exit 1
    }
    $tag = if ($args -match '--variant-tag\s+(\S+)') { $Matches[1] } else { "?" }

    # rewrite ONLY --tp-atr; everything else is preserved byte-for-byte
    if ($args -match '--tp-atr\s+\S+') {
        $newArgs = $args -replace '--tp-atr\s+\S+', '--tp-atr 5.0'
    } else {
        $newArgs = $args + ' --tp-atr 5.0'
    }

    $relaunch += [pscustomobject]@{ Pid = $b.ProcessId; Tag = $tag; Args = $newArgs }
    Write-Host ("  {0,-22} PID {1}" -f $tag, $b.ProcessId)
}
if ($relaunch.Count -ne 9) {
    Write-Host "WARNING: expected 9 bots, captured $($relaunch.Count). Continuing anyway." -ForegroundColor Yellow
}

Write-Host "=== 3. pull latest watchdog (so a watchdog restart keeps TP=5) ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 4. stop all captured bots ===" -ForegroundColor Cyan
foreach ($r in $relaunch) {
    Write-Host "  stopping $($r.Tag) (PID $($r.Pid))"
    Stop-Process -Id $r.Pid -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5

Write-Host "=== 5. relaunch each with --tp-atr 5.0 ===" -ForegroundColor Cyan
Set-Location $Desktop
foreach ($r in $relaunch) {
    Write-Host "  starting $($r.Tag)"
    Start-Process python -ArgumentList $r.Args -WorkingDirectory $Desktop
    Start-Sleep -Seconds 4
}

Write-Host "=== 6. verify every bot now shows TP=5.0 ===" -ForegroundColor Cyan
Start-Sleep -Seconds 30
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" }
Write-Host "  bots running: $($live.Count) (expected $($relaunch.Count))"
$bad = 0
foreach ($p in $live) {
    $tag = if ($p.CommandLine -match '--variant-tag\s+(\S+)') { $Matches[1] } else { "?" }
    $tp  = if ($p.CommandLine -match '--tp-atr\s+(\S+)')      { $Matches[1] } else { "MISSING" }
    $ok  = if ($tp -eq "5.0") { "OK" } else { "<-- WRONG"; $bad++ }
    Write-Host ("  {0,-22} tp-atr={1,-8} {2}" -f $tag, $tp, $ok)
}
if ($bad -eq 0 -and $live.Count -eq $relaunch.Count) {
    Write-Host "  ALL BOTS ON TP=5.0" -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - INVESTIGATE" -ForegroundColor Red
}
