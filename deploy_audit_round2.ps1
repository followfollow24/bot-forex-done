# deploy_audit_round2.ps1 -- 2026-08-11. Deploys the "fix all bugs" round
# (commit 9c3ad75) covering the remaining moderate/minor audit findings:
#   news_gemini_bot.py         : dedup timing/normalization/pruning, dead
#                                 branch removed, state-corruption alert,
#                                 last_poll accuracy
#   daily_sleeves_bot.py        : docstring fix, pip_size key mismatch fix
#   forex_live_bot_gold_cwider.py : equity-stop file-is-truth, MT5 timeout
#                                 wrapper gap, HISTORY_BARS consistency
#   forex_config.py / forex_executor.py : BTCUSDC/ETHUSDC price decimals,
#                                 removed dead aliases
#
# forex_config.py and forex_executor.py are imported by EVERY live bot, so
# this restarts all 12 processes (9x forex_live_bot_gold_cwider.py variants
# + 2x daily_sleeves_bot.py sleeves + 1x news_gemini_bot.py), each with its
# own REAL captured command line -- never retyped from memory.
#
# CODE-ONLY changes -- no risk/alloc/flag values changed. Positions are
# diffed before/after for the 9 cwider bots (SL/TP-pinned); daily_sleeves
# re-derives state from the broker on startup; news_gemini has no SL/TP
# state that a restart could disturb beyond what its own state.json tracks.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

Write-Host "=== 1. snapshot open positions BEFORE (must match after) ===" -ForegroundColor Cyan
$before = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print('|'.join(sorted(f'{p.magic}:{p.symbol}:{p.type}:{p.volume}' for p in ps)) or 'NONE'); mt5.shutdown()"
Write-Host "  $before"

Write-Host "=== 2. capture every bot's REAL command line ===" -ForegroundColor Cyan
$bots = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -match "forex_live_bot_gold_cwider\.py" -or
            $_.CommandLine -match "daily_sleeves_bot\.py" -or
            $_.CommandLine -match "news_gemini_bot\.py"
        }
if ($bots.Count -eq 0) { Write-Host "ABORT: no bots running" -ForegroundColor Red; exit 1 }
Write-Host "  found $($bots.Count) bot process(es) (expected 12)"

$relaunch = @()
foreach ($b in $bots) {
    $cl = $b.CommandLine
    $args = $cl -replace '^\s*"[^"]+"\s*', ''
    $tag = if ($args -match '--variant-tag\s+(\S+)') { $Matches[1] }
           elseif ($args -match 'news_gemini_bot\.py') { "news_gemini" }
           else { "?" }
    $relaunch += [pscustomobject]@{ Pid = $b.ProcessId; Tag = $tag; Args = $args }
    Write-Host ("  PID {0,-6} {1}" -f $b.ProcessId, $tag)
}
if ($relaunch.Count -ne 12) {
    Write-Host "WARNING: expected 12 bots, captured $($relaunch.Count). Continuing anyway." -ForegroundColor Yellow
}

Write-Host "=== 3. pull + copy the changed files ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py, daily_sleeves_bot.py, news_gemini_bot.py, forex_config.py, forex_executor.py $Desktop -Force

Write-Host "=== 4. stop all captured bots ===" -ForegroundColor Cyan
foreach ($r in $relaunch) {
    Write-Host "  stopping $($r.Tag) (PID $($r.Pid))"
    Stop-Process -Id $r.Pid -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5

Write-Host "=== 5. relaunch each with its OWN captured args ===" -ForegroundColor Cyan
Set-Location $Desktop
foreach ($r in $relaunch) {
    Write-Host "  starting $($r.Tag)"
    Start-Process python -ArgumentList $r.Args -WorkingDirectory $Desktop
    Start-Sleep -Seconds 4
}

Write-Host "=== 6. verify (waiting for warm-up) ===" -ForegroundColor Cyan
Start-Sleep -Seconds 35

$after = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=mt5.positions_get() or []; print('|'.join(sorted(f'{p.magic}:{p.symbol}:{p.type}:{p.volume}' for p in ps)) or 'NONE'); mt5.shutdown()"
$n = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object {
          $_.CommandLine -match "forex_live_bot_gold_cwider\.py" -or
          $_.CommandLine -match "daily_sleeves_bot\.py" -or
          $_.CommandLine -match "news_gemini_bot\.py"
      } | Measure-Object).Count

Write-Host ""
Write-Host "  bots running : $n  (expected $($relaunch.Count))" -ForegroundColor $(if($n -eq $relaunch.Count){"Green"}else{"Red"})
Write-Host "  positions BEFORE: $before"
Write-Host "  positions AFTER : $after"
if ($before -eq $after) {
    Write-Host "  POSITIONS MATCH - nothing was lost or duplicated" -ForegroundColor Green
} else {
    Write-Host "  POSITION MISMATCH - INVESTIGATE BEFORE WALKING AWAY" -ForegroundColor Red
}

Write-Host "=== 7. confirm the new code is actually live ===" -ForegroundColor Cyan
$hasEq = Select-String -Path "$Desktop\forex_live_bot_gold_cwider.py" -Pattern "EQUITY-STOP CLEARED" -Quiet
$hasDedup = Select-String -Path "$Desktop\news_gemini_bot.py" -Pattern "_normalize_url" -Quiet
$hasPip = Select-String -Path "$Desktop\daily_sleeves_bot.py" -Pattern "pip_value_usd_approx\[bsym\]" -Quiet
Write-Host "  cwider equity-stop file-is-truth fix present : $hasEq"
Write-Host "  news_gemini dedup normalization fix present  : $hasDedup"
Write-Host "  daily_sleeves pip_size key fix present        : $hasPip"
