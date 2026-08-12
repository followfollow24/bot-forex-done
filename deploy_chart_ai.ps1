# deploy_chart_ai.ps1 -- 2026-08-12. Deploys chart_ai_trader.py SAFELY.
#
# [!!] ORDER MATTERS. watchdog_h1.ps1 now contains a chart_ai_trader entry,
# and the watchdog AUTO-STARTS any bot whose heartbeat is missing. So if
# the new watchdog_h1.ps1 were copied to the Desktop first, the watchdog's
# next scheduled run would launch chart_ai_trader.py --allow-real against
# the REAL account at 0.30%/trade BEFORE anyone had verified the AI prompt
# actually discriminates. This script therefore:
#
#   1. drops the STOP_CHART_AI_TRADER kill-switch FIRST -- the watchdog
#      explicitly skips any bot with a kill-switch present, so even if the
#      watchdog fires mid-deploy it cannot start this bot.
#   2. copies the bot + preflight (NOT the watchdog yet)
#   3. runs the preflight control test (synthetic uptrend / downtrend /
#      pure-noise charts through BOTH providers) and STOPS if it fails
#   4. leaves the final "remove the kill-switch and let it trade" step to
#      a human, deliberately -- see the end of this script.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. drop the kill-switch BEFORE anything else ===" -ForegroundColor Cyan
Set-Content -Path "$Desktop\STOP_CHART_AI_TRADER" -Value "deploy in progress $(Get-Date -Format s)" -Encoding ASCII
Write-Host "  STOP_CHART_AI_TRADER created -- watchdog cannot start this bot now" -ForegroundColor Green

Write-Host "=== 2. pull + copy bot and preflight (NOT the watchdog yet) ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item chart_ai_trader.py, _preflight_chart_ai.py, _test_chart_ai_logic.py $Desktop -Force
Set-Location $Desktop

Write-Host "=== 3. logic unit tests (no API calls) ===" -ForegroundColor Cyan
python _test_chart_ai_logic.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "  LOGIC TESTS FAILED -- aborting, kill-switch left in place" -ForegroundColor Red
    exit 1
}

Write-Host "=== 4. preflight control test (real API calls, both providers) ===" -ForegroundColor Cyan
Write-Host "  Feeding synthetic uptrend / downtrend / PURE NOISE charts to both models."
Write-Host "  The critical check: pure noise must NOT produce a confident directional"
Write-Host "  consensus. If it does, this bot would invent setups out of nothing."
python _preflight_chart_ai.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "  PREFLIGHT FAILED OR COULD NOT RUN -- aborting, kill-switch left in place" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== 5. REVIEW THE PREFLIGHT OUTPUT ABOVE BEFORE CONTINUING ===" -ForegroundColor Yellow
Write-Host "  Read the per-chart verdicts. Specifically confirm:" -ForegroundColor Yellow
Write-Host "    - the NOISE chart produced NO TRADE consensus" -ForegroundColor Yellow
Write-Host "    - the trend charts were not answered with wild confidence" -ForegroundColor Yellow
Write-Host ""
Write-Host "  This script deliberately STOPS here. It does NOT start the bot and does" -ForegroundColor Yellow
Write-Host "  NOT install the new watchdog entry, because this is an UNVALIDATED" -ForegroundColor Yellow
Write-Host "  strategy about to trade a REAL account with real money." -ForegroundColor Yellow
Write-Host ""
Write-Host "  To go live once you are satisfied with the preflight output, run:" -ForegroundColor Cyan
Write-Host "    cd `$env:USERPROFILE\Desktop\bot_repo" -ForegroundColor White
Write-Host "    Copy-Item watchdog_h1.ps1 `$env:USERPROFILE\Desktop -Force" -ForegroundColor White
Write-Host "    Remove-Item `$env:USERPROFILE\Desktop\STOP_CHART_AI_TRADER" -ForegroundColor White
Write-Host "    Start-Process python -ArgumentList 'chart_ai_trader.py --risk 0.30 --allow-real' -WorkingDirectory `$env:USERPROFILE\Desktop" -ForegroundColor White
Write-Host ""
Write-Host "  To abandon instead: just leave STOP_CHART_AI_TRADER in place." -ForegroundColor Cyan
