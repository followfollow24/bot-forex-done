# deploy_gold_retune.ps1 -- 2026-08-05 gold_h1_manual retune.
#
#   --adx-min 22 -> 10
#   --touch-tolerance 0.012   (was the 0.0015 class default)
#   --regime-filter           REMOVED (the running process never had it anyway;
#                             only the stale watchdog file claimed it)
#   --risk 0.30               unchanged (watchdog file wrongly said 1.90)
#
# Validated at the REAL gold spread of $0.24 (MT5-measured 2026-08-05), not the
# bogus 2.85 constant that appears in many scripts in this repo:
#   OOS split  train PF 1.24 / Sharpe 0.62 -> OOS PF 1.21 / Sharpe 0.57
#   yearly WF  11/14 years PF>1
#   previous   OOS PF 1.15 / Sharpe 0.27
#
# Only gold_h1_manual changes. All other bots are left alone.
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. refuse to restart while gold_h1_manual holds a position ===" -ForegroundColor Cyan
# magic = SYMBOL_MAGIC["XAUUSDC"] 555003 + VARIANT_MAGIC_OFFSET["gold_h1_manual"] 140
$openGold = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print(len([p for p in ps if p.magic==555143])); mt5.shutdown()"
Write-Host "  positions on gold_h1_manual magic 555143: $openGold"
if ([int]$openGold -ne 0) {
    Write-Host "ABORT: close them first, then re-run." -ForegroundColor Red
    exit 1
}
Write-Host "  flat, safe to proceed" -ForegroundColor Green

Write-Host "=== 2. pull + copy ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py, watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 3. stop gold_h1_manual only ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "gold_h1_manual" } |
    ForEach-Object {
        Write-Host "  stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
    }
Start-Sleep -Seconds 3

Write-Host "=== 4. restart with retuned params ===" -ForegroundColor Cyan
Set-Location $Desktop
$goldArgs = "forex_live_bot_gold_cwider.py --symbol XAUUSDc --variant-tag gold_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --manual-exit --adx-min 10 --touch-tolerance 0.012 --max-positions 1 --risk 0.30 --allow-real"
Start-Process python -ArgumentList $goldArgs -WorkingDirectory $Desktop

Write-Host "=== 5. verify (waiting for warm-up) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 30

Write-Host "--- running command line ---" -ForegroundColor Yellow
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "gold_h1_manual" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host "--- startup banner from the bot's own log ---" -ForegroundColor Yellow
$log = "$Desktop\forex_xauusdc_gold_h1_manual.log"
if (Test-Path $log) {
    Select-String -Path $log -Pattern "Strategy|Risk/trade|Magic|Entry TF" | Select-Object -Last 6
} else {
    Write-Host "  log not found: $log" -ForegroundColor Red
}

Write-Host "=== 6. total bot count (expect 9) ===" -ForegroundColor Cyan
$n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Measure-Object).Count
Write-Host "  python.exe processes: $n"
