# deploy_sl25_tp15.ps1 -- 2026-08-07: SL/TP retune on the 3 trend-pullback
# manual bots ONLY (btc_h1_manual, gold_h1_manual, eth_h1_manual).
#
#   btc_h1_manual  : --sl-atr 2.5  --tp-atr 15.0
#   gold_h1_manual : --sl-atr 2.5  --tp-atr 15.0
#   eth_h1_manual  : --sl-atr 3.0  --tp-atr 15.0   (SL kept; ETH edge too thin
#                                                    to rank 2.5 vs 3.0)
#
# The other 6 bots are NOT touched: SL2.5/TP15 was validated on the
# trend-pullback family only. They keep their TP5 safety cap.
#
# VALIDATION (fixed engine, real costs, $0.24 gold spread):
#   BTC  PF 1.28 Sharpe 1.33 CAGR +22.9% | OOS 2nd half Sharpe 0.90 CAGR +20.0%
#   GOLD PF 1.19 Sharpe 0.71 CAGR +5.5% DD 14.6% | OOS 2nd half Sharpe 0.98
#   Neighbour grid is a plateau, yearly WF: BTC 9/10, GOLD 10/14 years PF>1.
#
# SAFETY: refuses to run if any of the THREE bots holds a position (its TP/SL
# were submitted at entry and cannot be retrofitted by a restart). Rewrites
# ONLY the --sl-atr and --tp-atr tokens in each captured command line.
#
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

$TARGETS = @{
    "btc_h1_manual"  = @{ Magic = 666120; Sl = "2.5"; Tp = "15.0" }
    "gold_h1_manual" = @{ Magic = 555143; Sl = "2.5"; Tp = "15.0" }
    "eth_h1_manual"  = @{ Magic = 667130; Sl = "3.0"; Tp = "15.0" }
}

Write-Host "=== 1. refuse to run while any TARGET bot holds a position ===" -ForegroundColor Cyan
$magics = ($TARGETS.Values | ForEach-Object { $_.Magic }) -join ","
$open = & python -c "import MetaTrader5 as mt5; mt5.initialize(); ps=[p for p in (mt5.positions_get() or []) if p.magic in ($magics)]; print(len(ps)); mt5.shutdown()"
Write-Host "  open positions on target magics: $open"
if ([int]$open -ne 0) {
    Write-Host "ABORT: close them first (open positions keep their entry-time SL/TP)." -ForegroundColor Red
    exit 1
}
Write-Host "  targets flat, safe to proceed" -ForegroundColor Green

Write-Host "=== 2. capture the 3 bots' REAL command lines ===" -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" }
$relaunch = @()
foreach ($p in $procs) {
    $args = $p.CommandLine -replace '^\s*"[^"]+"\s*', ''
    if ($args -notmatch '--variant-tag\s+(\S+)') { continue }
    $tag = $Matches[1]
    if (-not $TARGETS.ContainsKey($tag)) { continue }
    $t = $TARGETS[$tag]
    $newArgs = $args -replace '--sl-atr\s+\S+', "--sl-atr $($t.Sl)"
    $newArgs = $newArgs -replace '--tp-atr\s+\S+', "--tp-atr $($t.Tp)"
    $relaunch += [pscustomobject]@{ Pid = $p.ProcessId; Tag = $tag; Args = $newArgs }
    Write-Host ("  {0,-18} PID {1}  -> sl-atr {2}, tp-atr {3}" -f $tag, $p.ProcessId, $t.Sl, $t.Tp)
}
if ($relaunch.Count -ne 3) {
    Write-Host "ABORT: expected 3 target bots running, found $($relaunch.Count)." -ForegroundColor Red
    exit 1
}

Write-Host "=== 3. pull latest watchdog (so a watchdog restart keeps the new SL/TP) ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 4. restart the 3 bots ===" -ForegroundColor Cyan
foreach ($r in $relaunch) {
    Write-Host "  stopping $($r.Tag) (PID $($r.Pid))"
    Stop-Process -Id $r.Pid -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 5
Set-Location $Desktop
foreach ($r in $relaunch) {
    Write-Host "  starting $($r.Tag)"
    Start-Process python -ArgumentList $r.Args -WorkingDirectory $Desktop
    Start-Sleep -Seconds 4
}

Write-Host "=== 5. verify ===" -ForegroundColor Cyan
Start-Sleep -Seconds 30
$live = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "forex_live_bot_gold_cwider\.py" }
Write-Host "  total bots running: $($live.Count) (expected 9)"
$bad = 0
foreach ($p in $live) {
    $tag = if ($p.CommandLine -match '--variant-tag\s+(\S+)') { $Matches[1] } else { "?" }
    if (-not $TARGETS.ContainsKey($tag)) { continue }
    $sl = if ($p.CommandLine -match '--sl-atr\s+(\S+)') { $Matches[1] } else { "?" }
    $tp = if ($p.CommandLine -match '--tp-atr\s+(\S+)') { $Matches[1] } else { "?" }
    $want = $TARGETS[$tag]
    $ok = ($sl -eq $want.Sl -and $tp -eq $want.Tp)
    if (-not $ok) { $bad++ }
    Write-Host ("  {0,-18} sl-atr={1,-5} tp-atr={2,-6} {3}" -f $tag, $sl, $tp, $(if ($ok) { "OK" } else { "<-- WRONG" }))
}
if ($bad -eq 0 -and $live.Count -eq 9) {
    Write-Host "  TARGETS ON SL2.5-3.0 / TP15 - DONE" -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - INVESTIGATE" -ForegroundColor Red
}
