# deploy.ps1 -- pull latest code from GitHub and restart bots
# Usage: cd Desktop; .\deploy.ps1
# Deploys 5 variants on Real Cent account (--allow-real):
#   gold:  adx20tp7, adx18tp7        (XAUUSDc, risk 0.30%, validated live since 2026-07-04)
#   BTC-HF: btc_cons, btc_aggr        (BTCUSDc, risk 0.20%, WF+27-window validated,
#                                       pre-flight-tested 2026-07-11)
#   gold regime22 (XAUUSDc, risk 0.30%, frozen ADX>22+ADX-rising+EMA-gap regime
#                  filter on real M15 engine: 2,848 trades/13yr PF=1.29 MaxDD=10.3%
#                  WF-A 12/14yr, pre-flight-tested 2026-07-17, 0 live trades before
#                  this deploy). magic=555103.
# m5tp7 is permanently retired -- not started here.

$DESKTOP = "$env:USERPROFILE\Desktop"
$REPO    = "$DESKTOP\bot_repo"

Write-Host "=== Bot Deploy Script (5 variants: gold x3 + BTC-HF x2) ===" -ForegroundColor Cyan

# 1. Check all bots are flat before doing anything
# NOTE: regime22 has never run live before this deploy (0 trades) -- there is
# nothing to check yet on its first-ever start, same as btc_cons/btc_aggr were
# on their first deploy. Once it has run at least once, add it to this loop.
Write-Host "`n[1] Checking positions (adx20tp7/adx18tp7 only -- BTC + regime22 have no prior run)..." -ForegroundColor Yellow
$variants = @("adx20tp7","adx18tp7")
$allFlat = $true
foreach ($v in $variants) {
    $logFile = "$DESKTOP\forex_xauusd_$v.log"
    if (Test-Path $logFile) {
        # Use last STATUS line in last 300 lines (not just -Tail 1 since
        # last line may be trades_today=0 not STATUS line)
        $statusLine = Get-Content $logFile -Tail 300 -ErrorAction SilentlyContinue |
                      Select-String "== STATUS ==" | Select-Object -Last 1
        if ($statusLine) {
            $pos = [regex]::Match($statusLine.Line, "position=\[(.+?)\]").Groups[1].Value
        } else {
            $pos = ""
        }
        if ($pos -eq "-") {
            Write-Host "  $v : flat [-]" -ForegroundColor Green
        } elseif ($pos -eq "") {
            Write-Host "  $v : no STATUS line found (assuming flat)" -ForegroundColor Yellow
        } else {
            Write-Host "  $v : position=$pos  <-- NOT FLAT, aborting!" -ForegroundColor Red
            $allFlat = $false
        }
    } else {
        Write-Host "  $v : log not found (assuming flat)" -ForegroundColor Yellow
    }
}

if (-not $allFlat) {
    Write-Host "`nAborted -- wait for all positions to close first." -ForegroundColor Red
    exit 1
}

# 2. git pull
Write-Host "`n[2] Pulling latest from GitHub..." -ForegroundColor Yellow
Set-Location $REPO
git pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed! Aborting." -ForegroundColor Red
    exit 1
}

# 3. Copy updated .py and .ps1 files to Desktop
Write-Host "`n[3] Copying .py and .ps1 files to Desktop..." -ForegroundColor Yellow
Copy-Item "$REPO\*.py" $DESKTOP -Force
Copy-Item "$REPO\watchdog.ps1" $DESKTOP -Force -ErrorAction SilentlyContinue
Copy-Item "$REPO\setup_watchdog_task.ps1" $DESKTOP -Force -ErrorAction SilentlyContinue
Write-Host "  Done." -ForegroundColor Green

# 4. Stop running bots
Write-Host "`n[4] Stopping bots..." -ForegroundColor Yellow
# Filter by CommandLine to only kill our forex bot processes,
# not every python.exe (pip, other scripts, VSCode, etc.)
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }
if ($running) {
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
    Write-Host "  Stopped PIDs: $($running.ProcessId -join ', ')" -ForegroundColor Green
} else {
    Write-Host "  No forex bots running." -ForegroundColor Yellow
}

# 5. Start 4 bots with --allow-real -- m5tp7 permanently retired
Write-Host "`n[5] Starting bots..." -ForegroundColor Yellow
Set-Location $DESKTOP

Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

# BTC-HF conservative (ADX15/SL4/TP12) -- WF-A 8/8yr PF>1, WF-B OOS Sharpe 1.71,
# 27-window 15/18. magic=666000. max-positions=1 matches what was walk-forward
# validated (single position at a time) -- NOT copied from gold's 3, since BTC-HF
# was never tested with concurrent positions.
Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_cons --sl-atr 4.0 --tp-atr 12.0 --adx-min 15 --timeframe 15m --max-positions 1 --risk 0.20 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

# BTC-HF aggressive (ADX12/SL2.5/TP7.5) -- WF-A 8/8yr PF>1, WF-B OOS Sharpe 2.21,
# 27-window 17/18 (best of all 4 variants). magic=666010.
Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_aggr --sl-atr 2.5 --tp-atr 7.5 --adx-min 12 --timeframe 15m --max-positions 1 --risk 0.20 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

# Gold regime filter (ADX22/SL3/TP7 + --regime-filter) -- frozen ADX>22 + ADX-rising +
# EMA-gap>1.2xATR(H1) on top of the same M15 pullback entry. Real-engine validated
# 2026-07-12: 2,848 trades/13yr PF=1.29 MaxDD=10.3% WF-A 12/14yr. magic=555103.
Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag regime22 --sl-atr 3.0 --tp-atr 7.0 --adx-min 22 --regime-filter --timeframe 15m --max-positions 3 --risk 0.30 --allow-real" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

# 6. Verify
Write-Host "`n[6] Verifying..." -ForegroundColor Yellow
$count = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }).Count
$color = if ($count -eq 5) { "Green" } else { "Red" }
Write-Host "  Running bot processes: $count / 5" -ForegroundColor $color

Write-Host "`n[6b] Check log for REAL-MONEY confirmation..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
foreach ($v in @("adx20tp7", "adx18tp7", "regime22")) {
    Write-Host "  --- $v (gold) ---" -ForegroundColor Cyan
    Get-Content "$DESKTOP\forex_xauusd_$v.log" -Tail 40 -ErrorAction SilentlyContinue |
        Select-String "REAL-MONEY|balance=|EQUITY_STOP|REFUSING|Magic"
}
foreach ($v in @("btc_cons", "btc_aggr")) {
    Write-Host "  --- $v (BTC-HF) ---" -ForegroundColor Cyan
    Get-Content "$DESKTOP\forex_btcusdc_$v.log" -Tail 40 -ErrorAction SilentlyContinue |
        Select-String "REAL-MONEY|balance=|EQUITY_STOP|REFUSING|Magic"
}

if ($count -eq 5) {
    Write-Host "`nDeploy complete! Verify 'REAL-MONEY MODE CONFIRMED' in log above for all 5." -ForegroundColor Cyan
} else {
    Write-Host "`nWarning: expected 5 bots, got $count. Check manually." -ForegroundColor Red
}
