# deploy_btc_retune.ps1 -- 2026-08-05 btc_h1_manual retune.
#
#   --adx-min 18 -> 10
#   --touch-tolerance 0.012   (new flag; class default was 0.0015)
#   --risk 1.90 -> 1.00 in watchdog_h1.ps1 (running process was ALREADY 1.00
#                                           from the 2026-08-02 rebalance; the
#                                           watchdog file was stale and would
#                                           have reverted it on any restart)
#
# Only btc_h1_manual changes. All other bots are left alone.
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. refuse to restart while btc_h1_manual holds a position ===" -ForegroundColor Cyan
$openBtc = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print(len([p for p in ps if p.magic==666120])); mt5.shutdown()"
if ([int]$openBtc -ne 0) {
    Write-Host "ABORT: btc_h1_manual (magic 666120) has $openBtc open position(s). Close first." -ForegroundColor Red
    exit 1
}
Write-Host "  btc_h1_manual flat, safe to proceed" -ForegroundColor Green

Write-Host "=== 2. pull + copy ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py, watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 3. stop btc_h1_manual only ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "btc_h1_manual" } |
    ForEach-Object {
        Write-Host "  stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
    }
Start-Sleep -Seconds 3

Write-Host "=== 4. restart with retuned params ===" -ForegroundColor Cyan
Set-Location $Desktop
$btcArgs = "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --manual-exit --adx-min 10 --touch-tolerance 0.012 --max-positions 1 --risk 1.00 --allow-real"
Start-Process python -ArgumentList $btcArgs -WorkingDirectory $Desktop

Write-Host "=== 5. verify (waiting for warm-up) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 30

Write-Host "--- running command line ---" -ForegroundColor Yellow
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "btc_h1_manual" } |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host "--- startup banner from the bot's own log ---" -ForegroundColor Yellow
$log = "$Desktop\forex_btcusdc_btc_h1_manual.log"
if (Test-Path $log) {
    Select-String -Path $log -Pattern "Strategy|Risk/trade|Magic|ADX|EMA" | Select-Object -Last 8
} else {
    Write-Host "  log not found yet: $log" -ForegroundColor Red
}

Write-Host "=== 6. total bot count (expect 9) ===" -ForegroundColor Cyan
$n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Measure-Object).Count
Write-Host "  python.exe processes: $n"
