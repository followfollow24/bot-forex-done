# deploy_chart_ai.ps1 -- one-command redeploy for chart_ai_trader.py.
#
# Written because the RDP session this repo is normally driven through kept
# dropping mid-deploy, and the manual sequence (pull, copy, test, find PID,
# taskkill, clear breaker, restart, verify) is 7 steps where step 5 needs a
# PID read off the screen -- easy to get wrong by hand, and every one of
# those steps touches a bot trading real money.
#
# Refuses to restart the bot if the unit tests fail. That ordering is the
# point: the tests are what stand between an edit and a live order.
#
# Usage (from anywhere):
#   powershell -ExecutionPolicy Bypass -File %USERPROFILE%\Desktop\bot_repo\deploy_chart_ai.ps1
#
#   -ClearBreaker   also delete BREAKER_CHART_AI_TRADER, resuming a bot that
#                   auto-stopped on a losing streak. Off by default: clearing
#                   it puts real money back into the market, so it should be
#                   a deliberate keystroke, not a side effect of redeploying.
#   -SkipTests      emergency escape hatch. Prints a warning.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

param(
    [switch]$ClearBreaker,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"
# [2026-08-15] INVERTED, BTC-only. Keep this string identical to the Args
# entry in watchdog_h1.ps1 -- if they drift, the watchdog silently relaunches
# the bot with different settings than this script started it with.
$Args    = "chart_ai_trader.py --risk 0.30 --allow-real --invert --symbols BTCUSDC"

Write-Host "=== 1. pull latest code ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item chart_ai_trader.py, _test_chart_ai_logic.py, trade_summary.py $Desktop -Force
Set-Location $Desktop

if (-not $SkipTests) {
    Write-Host "=== 2. unit tests (must pass before anything is restarted) ===" -ForegroundColor Cyan
    $out = & python _test_chart_ai_logic.py 2>&1
    $tail = ($out | Select-Object -Last 3) -join "`n"
    if ($out -match "ALL TESTS PASSED") {
        Write-Host "  ALL TESTS PASSED" -ForegroundColor Green
    } else {
        Write-Host "  TESTS FAILED -- NOT restarting the bot. Last output:" -ForegroundColor Red
        Write-Host $tail
        exit 1
    }
} else {
    Write-Host "=== 2. tests SKIPPED (-SkipTests) ===" -ForegroundColor Yellow
}

Write-Host "=== 3. stop the running bot ===" -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -like "*chart_ai_trader*" }
if ($procs) {
    foreach ($p in $procs) {
        Write-Host "  stopping PID $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 4
} else {
    Write-Host "  not running (nothing to stop)"
}

Write-Host "=== 4. breaker / kill-switch ===" -ForegroundColor Cyan
$breaker = "$Desktop\BREAKER_CHART_AI_TRADER"
$stopf   = "$Desktop\STOP_CHART_AI_TRADER"
if (Test-Path $stopf) {
    Write-Host "  ABORT: $stopf exists -- the operator deliberately stopped this bot." -ForegroundColor Red
    Write-Host "  Delete it by hand if you really mean to resume." -ForegroundColor Red
    exit 1
}
if (Test-Path $breaker) {
    if ($ClearBreaker) {
        Remove-Item $breaker -Force
        Write-Host "  breaker cleared (-ClearBreaker) -- bot will take new entries again" -ForegroundColor Yellow
    } else {
        Write-Host "  BREAKER PRESENT and NOT cleared: the bot will start but take no" -ForegroundColor Yellow
        Write-Host "  new entries. Re-run with -ClearBreaker to resume trading." -ForegroundColor Yellow
    }
} else {
    Write-Host "  no breaker file (bot is free to trade)"
}

Write-Host "=== 5. restart ===" -ForegroundColor Cyan
Start-Process python -ArgumentList $Args -WorkingDirectory $Desktop
Start-Sleep -Seconds 25

Write-Host "=== 6. verify ===" -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*chart_ai_trader*" }
if ($live) {
    foreach ($p in $live) { Write-Host "  running PID $($p.ProcessId)" -ForegroundColor Green }
} else {
    Write-Host "  NOT RUNNING -- check $Desktop\forex_bot_chart_ai_trader.log" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "--- startup banner ---" -ForegroundColor Cyan
Get-Content "$Desktop\forex_bot_chart_ai_trader.log" -Tail 25 |
    Select-String "CHART AI TRADER|risk/trade|SL/TP|entry filters|news veto|stacking|breaker|account="
