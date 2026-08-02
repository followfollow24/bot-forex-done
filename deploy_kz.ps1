# deploy_kz.ps1 -- apply the 2026-08-02 risk-parity + crypto-killzone change.
#
# New risk structure (max combined risk if all five BTC sleeves align and
# all stop out simultaneously = 3.30% of account, down from 4.20%):
#     btc_h1_manual    1.00%   (was 1.90% -- was carrying ~45% of portfolio risk)
#     btc_h1_breakout  1.00%   (unchanged, not restarted)
#     btc_amd          0.50%   + --crypto-killzone
#     btc_lqsweep      0.50%   + --crypto-killzone
#     btc_tpo          0.30%   + --crypto-killzone
#
# ASCII-only on purpose: PowerShell 5.1 fails to parse a no-BOM .ps1 that
# contains non-ASCII bytes (confirmed on this VPS previously).

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. refusing to restart while any position is open ===" -ForegroundColor Cyan
$open = & python -c "import MetaTrader5 as mt5; mt5.initialize(); print(len(mt5.positions_get() or [])); mt5.shutdown()"
if ([int]$open -ne 0) {
    Write-Host "ABORT: $open position(s) still open. Close them first." -ForegroundColor Red
    exit 1
}
Write-Host "  flat, safe to proceed" -ForegroundColor Green

Write-Host "=== 2. pull + copy ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py, ict_tools_strategies.py $Desktop -Force

Write-Host "=== 3. stop the four sleeves being changed ===" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match "btc_amd|btc_lqsweep|btc_tpo|btc_h1_manual" } |
    ForEach-Object {
        Write-Host "  stopping PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
    }
Start-Sleep -Seconds 3

Write-Host "=== 4. restart with new params ===" -ForegroundColor Cyan
Set-Location $Desktop

$launches = @(
    @{ Tag = "btc_h1_manual"; Args = "--symbol BTCUSDc --variant-tag btc_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --manual-exit --adx-min 18 --max-positions 1 --risk 1.00 --allow-real" },
    @{ Tag = "btc_amd";       Args = "--symbol BTCUSDc --variant-tag btc_amd --timeframe 1h --strategy tool_amd --crypto-killzone --sl-atr 2.0 --tp-atr 999 --manual-exit --risk 0.50 --allow-real" },
    @{ Tag = "btc_lqsweep";   Args = "--symbol BTCUSDc --variant-tag btc_lqsweep --timeframe 1h --strategy tool_lqsweep --crypto-killzone --sl-atr 2.0 --tp-atr 999 --manual-exit --risk 0.50 --allow-real" },
    @{ Tag = "btc_tpo";       Args = "--symbol BTCUSDc --variant-tag btc_tpo --timeframe 1h --strategy tool_tpo --crypto-killzone --sl-atr 2.0 --tp-atr 999 --manual-exit --risk 0.30 --allow-real" }
)

foreach ($l in $launches) {
    Write-Host "  starting $($l.Tag)"
    Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py $($l.Args)" -WorkingDirectory $Desktop
    Start-Sleep -Seconds 6
}

Write-Host "=== 5. verify (waiting for warm-up) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 25
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Select-Object ProcessId, CommandLine | Format-List

Write-Host "=== 6. confirm each restarted bot's live params from its log ===" -ForegroundColor Cyan
foreach ($t in @("btc_h1_manual", "btc_amd", "btc_lqsweep", "btc_tpo")) {
    $log = "$Desktop\forex_btcusdc_$t.log"
    if (Test-Path $log) {
        Write-Host "--- $t ---" -ForegroundColor Yellow
        Select-String -Path $log -Pattern "Strategy|Risk/trade|Magic" | Select-Object -Last 3
    }
}
