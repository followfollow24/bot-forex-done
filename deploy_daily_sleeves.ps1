# deploy_daily_sleeves.ps1 -- 2026-08-08. First deploy of the 2 new daily
# portfolio sleeves (daily_sleeves_bot.py). NEW CODE, never executed a real
# order before -- preflight (_preflight_daily_sleeves.py) only proves signal
# correctness against 2471+2393+3240 days of reference history, NOT that
# order placement/resize/close works on this VPS+broker. This script adds a
# short --dry-run smoke test (connects to MT5+Binance, computes, writes
# heartbeat, sends NO real orders) before flipping either bot to --allow-real,
# so a connectivity/crash bug shows up before any capital is at risk.
#
# Started SMALL on the Real Cent account per explicit user instruction
# ("cent account, doesn't affect anything"): funding sleeve risk 0.3%/trade,
# combo sleeve alloc 10% of equity -- see watchdog_h1.ps1 for the same args
# (kept in sync so a watchdog restart cannot silently change them).
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. pull latest code ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item daily_sleeves_bot.py $Desktop -Force
Copy-Item _preflight_daily_sleeves_vps.py $Desktop -Force
if (-not (Test-Path "$Desktop\newbot_refs")) { New-Item -ItemType Directory "$Desktop\newbot_refs" | Out-Null }
Copy-Item newbot_refs\* "$Desktop\newbot_refs" -Force -Recurse
Copy-Item watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 2. VPS preflight: real Binance + real MT5 fetch vs frozen reference ===" -ForegroundColor Cyan
Set-Location $Desktop
python _preflight_daily_sleeves_vps.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "ABORT: preflight failed on VPS -- do not deploy." -ForegroundColor Red
    exit 1
}
Write-Host "  preflight PASS (live Binance fetch + live MT5 fetch both match frozen reference)" -ForegroundColor Green

Write-Host "=== 3. dry-run smoke test (no real orders) ===" -ForegroundColor Cyan
$dryFunding = Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve funding --variant-tag funding_smoketest --dry-run" -WorkingDirectory $Desktop -PassThru -WindowStyle Normal
$dryCombo   = Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve combo --variant-tag combo_smoketest --dry-run" -WorkingDirectory $Desktop -PassThru -WindowStyle Normal
Write-Host "  waiting 90s for MT5 connect + first heartbeat..."
Start-Sleep -Seconds 90
$fOk = (Test-Path "$Desktop\HEARTBEAT_CRYPTO_FUNDING_SMOKETEST") -and
       ((Get-Item "$Desktop\HEARTBEAT_CRYPTO_FUNDING_SMOKETEST").LastWriteTimeUtc -gt (Get-Date).ToUniversalTime().AddMinutes(-2))
$cOk = (Test-Path "$Desktop\HEARTBEAT_BTCUSDC_COMBO_SMOKETEST") -and
       ((Get-Item "$Desktop\HEARTBEAT_BTCUSDC_COMBO_SMOKETEST").LastWriteTimeUtc -gt (Get-Date).ToUniversalTime().AddMinutes(-2))
Write-Host ("  funding smoke heartbeat: {0}" -f $(if ($fOk) {"OK"} else {"MISSING/STALE"}))
Write-Host ("  combo   smoke heartbeat: {0}" -f $(if ($cOk) {"OK"} else {"MISSING/STALE"}))
Stop-Process -Id $dryFunding.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $dryCombo.Id -Force -ErrorAction SilentlyContinue
Remove-Item "$Desktop\HEARTBEAT_CRYPTO_FUNDING_SMOKETEST" -ErrorAction SilentlyContinue
Remove-Item "$Desktop\HEARTBEAT_BTCUSDC_COMBO_SMOKETEST" -ErrorAction SilentlyContinue
if (-not ($fOk -and $cOk)) {
    Write-Host "ABORT: smoke test failed -- check the console windows for the error before retrying." -ForegroundColor Red
    exit 1
}
Write-Host "  smoke test PASS (MT5 connect + Binance fetch + heartbeat all working)" -ForegroundColor Green

Write-Host "=== 4. start LIVE (real money, small size) ===" -ForegroundColor Cyan
Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve funding --variant-tag funding_contrarian --risk 0.3 --allow-real" -WorkingDirectory $Desktop -WindowStyle Normal
Start-Sleep -Seconds 5
Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve combo --variant-tag btc_combo_lb --alloc 0.10 --allow-real" -WorkingDirectory $Desktop -WindowStyle Normal
Start-Sleep -Seconds 20

Write-Host "=== 5. verify ===" -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "daily_sleeves_bot\.py" }
Write-Host "  daily-sleeve processes running: $($live.Count) (expected 2)"
foreach ($p in $live) { Write-Host ("  PID {0}: {1}" -f $p.ProcessId, $p.CommandLine) }
if ($live.Count -eq 2) {
    Write-Host "  DAILY SLEEVES LIVE ON REAL CENT ACCOUNT - DONE" -ForegroundColor Green
    Write-Host "  Next UTC-midnight decision window is when the first real signal fires -- watch Telegram." -ForegroundColor Yellow
} else {
    Write-Host "  SOMETHING IS OFF - INVESTIGATE" -ForegroundColor Red
}
