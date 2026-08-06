# restart_btc_h1_manual.ps1 -- 2026-08-06 restart btc_h1_manual only.
#
# WHY: log for this bot went completely silent from 09:31 to (at least) 12:39
# today -- 3+ hours with zero TELEGRAM/ATR/SIGNAL activity, despite the open
# position (entry=64835.45, entry_atr=216.53) crossing -1xATR during that
# window (confirmed live: profit_atr ~ -1.48 to -1.50 with no milestone alert
# ever sent). Matches the previously-documented MT5-API-call-hang pattern in
# this codebase (project_bot_hang_issue.md, 2026-07-02: _fetch_closed_candles()
# has no timeout). Root cause not 100% confirmed (RDP session was too
# unresponsive to check Get-Process ... Responding directly), but the fix is
# the same either way: restart the process.
#
# SAFETY: captures the REAL running command line (does not retype args), and
# verifies the open position is still present with the SAME entry/lot after
# restart (recovered from state.json, which persists correctly).
#
# ASCII-only.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"

Write-Host "=== 1. find btc_h1_manual and snapshot its position ===" -ForegroundColor Cyan
$proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "btc_h1_manual" }
if (-not $proc) { Write-Host "ABORT: btc_h1_manual process not found" -ForegroundColor Red; exit 1 }
if ($proc.Count -gt 1) { Write-Host "ABORT: multiple matches, investigate manually" -ForegroundColor Red; exit 1 }
Write-Host "  PID $($proc.ProcessId)"
$args = $proc.CommandLine -replace '^\s*"[^"]+"\s*', ''
Write-Host "  args: $args"

$before = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=[p for p in (mt5.positions_get() or []) if p.magic==666120]; print([(p.symbol,p.type,p.volume,p.price_open) for p in ps]); mt5.shutdown()"
Write-Host "  position before: $before"

Write-Host "=== 2. stop it ===" -ForegroundColor Cyan
Stop-Process -Id $proc.ProcessId -Force
Start-Sleep -Seconds 4

Write-Host "=== 3. restart with the SAME args ===" -ForegroundColor Cyan
Set-Location $Desktop
Start-Process python -ArgumentList $args -WorkingDirectory $Desktop

Write-Host "=== 4. verify ===" -ForegroundColor Cyan
Start-Sleep -Seconds 25
$after = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=[p for p in (mt5.positions_get() or []) if p.magic==666120]; print([(p.symbol,p.type,p.volume,p.price_open) for p in ps]); mt5.shutdown()"
Write-Host "  position after : $after"

$newproc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
           Where-Object { $_.CommandLine -match "btc_h1_manual" }
Write-Host "  new PID: $($newproc.ProcessId)"

Write-Host "=== 5. tail of the fresh log ===" -ForegroundColor Cyan
$log = "$Desktop\forex_btcusdc_btc_h1_manual.log"
if (Test-Path $log) {
    Select-String -Path $log -Pattern "STATE|Strategy|Magic|Risk" | Select-Object -Last 6
}
