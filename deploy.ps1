# deploy.ps1 — pull latest code from GitHub and restart bots
# Usage: cd Desktop; .\deploy.ps1

$DESKTOP = "$env:USERPROFILE\Desktop"
$REPO    = "$DESKTOP\bot_repo"

Write-Host "=== Bot Deploy Script ===" -ForegroundColor Cyan

# 1. Check all bots are flat before doing anything
Write-Host "`n[1] Checking positions..." -ForegroundColor Yellow
$variants = @("adx20tp7","m5tp7","adx18tp7")
$allFlat = $true
foreach ($v in $variants) {
    $logFile = "$DESKTOP\forex_xauusd_$v.log"
    if (Test-Path $logFile) {
        $line = Get-Content $logFile -Tail 1
        $pos = [regex]::Match($line, "position=\[(.+?)\]").Groups[1].Value
        if ($pos -eq "-") {
            Write-Host "  $v : flat [-]" -ForegroundColor Green
        } else {
            Write-Host "  $v : position=$pos  <-- NOT FLAT, aborting!" -ForegroundColor Red
            $allFlat = $false
        }
    } else {
        Write-Host "  $v : log not found (assuming flat)" -ForegroundColor Yellow
    }
}

if (-not $allFlat) {
    Write-Host "`nAborted — wait for all positions to close first." -ForegroundColor Red
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

# 3. Copy updated .py files to Desktop
Write-Host "`n[3] Copying .py files to Desktop..." -ForegroundColor Yellow
Copy-Item "$REPO\*.py" $DESKTOP -Force
Write-Host "  Done." -ForegroundColor Green

# 4. Stop running bots
Write-Host "`n[4] Stopping bots..." -ForegroundColor Yellow
$running = Get-Process python* -ErrorAction SilentlyContinue
if ($running) {
    Stop-Process -Id $running.Id -Force
    Start-Sleep -Seconds 2
    Write-Host "  Stopped: $($running.Id -join ', ')" -ForegroundColor Green
} else {
    Write-Host "  No bots running." -ForegroundColor Yellow
}

# 5. Restart all 3 bots
Write-Host "`n[5] Starting bots..." -ForegroundColor Yellow
Set-Location $DESKTOP

Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 2

Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag m5tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 5m --max-positions 3 --risk 0.30" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 2

Start-Process python -ArgumentList "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30" -WorkingDirectory $DESKTOP -WindowStyle Normal
Start-Sleep -Seconds 3

# 6. Verify
Write-Host "`n[6] Verifying..." -ForegroundColor Yellow
$count = (Get-Process python* -ErrorAction SilentlyContinue).Count
$color = if ($count -eq 3) { "Green" } else { "Red" }
Write-Host "  Running python processes: $count / 3" -ForegroundColor $color

if ($count -eq 3) {
    Write-Host "`nDeploy complete!" -ForegroundColor Cyan
} else {
    Write-Host "`nWarning: expected 3 bots, got $count. Check manually." -ForegroundColor Red
}
