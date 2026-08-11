# redeploy_audit_fixes.ps1 -- 2026-08-11. Deploys the 4 critical bug fixes
# + watchdog coverage found in the full-fleet audit (commit 8820e6a):
#   daily_sleeves_bot.py : exit-result-check, state-from-broker, day-gate retry
#   news_gemini_bot.py   : demo/real gate, time-stop-result-check, breaker fix
#   watchdog_h1.ps1       : 7 previously-uncovered bots added
#
# CODE-ONLY changes -- no risk/alloc/flag values changed, so this is a
# straight stop+restart with the SAME args each bot is already running
# (captured from the real running command lines, not retyped from memory).
# Does NOT require positions to be flat: daily_sleeves_bot.py's _recover()
# re-derives tracked state from the broker on startup, and none of these
# bots pin an entry-time-only parameter the way H1 SL/TP does.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. pull latest code ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item daily_sleeves_bot.py, news_gemini_bot.py, watchdog_h1.ps1 $Desktop -Force
Write-Host "  watchdog_h1.ps1 updated on Desktop (takes effect next scheduled run, no restart needed for it)" -ForegroundColor Green

Write-Host "=== 2. stop the 3 affected bots ===" -ForegroundColor Cyan
Set-Location $Desktop
$targets = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
           Where-Object {
               $_.CommandLine -match "--variant-tag funding_contrarian" -or
               $_.CommandLine -match "--variant-tag btc_combo_lb" -or
               $_.CommandLine -match "news_gemini_bot\.py"
           }
Write-Host "  found $($targets.Count) matching process(es) (expected 3)"
foreach ($p in $targets) {
    Write-Host "  stopping PID $($p.ProcessId): $($p.CommandLine)"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 3

Write-Host "=== 3. restart with unchanged args ===" -ForegroundColor Cyan
Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve funding --variant-tag funding_contrarian --risk 0.3 --allow-real" -WorkingDirectory $Desktop -WindowStyle Normal
Start-Sleep -Seconds 5
Start-Process python -ArgumentList "daily_sleeves_bot.py --sleeve combo --variant-tag btc_combo_lb --alloc 0.10 --allow-real" -WorkingDirectory $Desktop -WindowStyle Normal
Start-Sleep -Seconds 5
Start-Process python -ArgumentList "news_gemini_bot.py --allow-real" -WorkingDirectory $Desktop -WindowStyle Normal
Start-Sleep -Seconds 15

Write-Host "=== 4. verify ===" -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -match "--variant-tag funding_contrarian" -or
            $_.CommandLine -match "--variant-tag btc_combo_lb" -or
            $_.CommandLine -match "news_gemini_bot\.py"
        }
Write-Host "  processes running: $($live.Count) (expected 3)"
foreach ($p in $live) { Write-Host ("  PID {0}: {1}" -f $p.ProcessId, $p.CommandLine) }
if ($live.Count -eq 3) {
    Write-Host "  AUDIT FIXES DEPLOYED - watch each bot's console for the startup banner" -ForegroundColor Green
    Write-Host "  news_gemini should show 'account=LIVE (--allow-real)' in its banner now." -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - INVESTIGATE" -ForegroundColor Red
}
