# deploy_gates_sl25tp15.ps1 -- 2026-08-08. SUPERSEDES deploy_sl25_tp15.ps1.
# One restart applies BOTH pending changes to the 3 trend-pullback bots:
#
#   btc_h1_manual  : --sl-atr 2.5 --tp-atr 15.0  + --xasset-short-gate ETHUSDc:36:168
#   gold_h1_manual : --sl-atr 2.5 --tp-atr 15.0  + --block-hours 20-01
#   eth_h1_manual  : --sl-atr 3.0 --tp-atr 15.0    (no gate -- none validated for ETH)
#
# VALIDATION (2026-08-07/08, fixed engine, real costs, $0.24 gold spread):
#   SL2.5/TP15 : BTC PF1.28 Sh1.33 CAGR+22.9% | GOLD PF1.19 Sh0.71 CAGR+5.5%
#   + r36S gate: BTC Sh 1.33->1.47, DD 22.6->12.2%, 2022 loss -23.5%->-12.1%
#   + time gate: GOLD Sh 0.71->0.95, CAGR +5.50->+7.07%, yearly 10/14->12/14
#   Live-path causal check passed (0 mismatches, _verify_gate_live_path.py).
#   Gates are FAIL-OPEN: any evaluation error = entry allowed = old behavior.
#
# SAFETY: refuses to run if any of the THREE bots holds a position (the SL/TP
# part is entry-time-only). Pulls latest bot code + watchdog first, captures
# each bot's REAL command line and rewrites/appends only the changed flags.
# ASCII-only: PowerShell 5.1 fails to parse a no-BOM .ps1 with non-ASCII bytes.

$ErrorActionPreference = "Stop"
$Desktop = "$env:USERPROFILE\Desktop"
$Repo    = "$Desktop\bot_repo"

$TARGETS = @{
    "btc_h1_manual"  = @{ Magic = 666120; Sl = "2.5"; Tp = "15.0"
                          GateFlag = "--xasset-short-gate"; GateVal = "ETHUSDc:36:168" }
    "gold_h1_manual" = @{ Magic = 555143; Sl = "2.5"; Tp = "15.0"
                          GateFlag = "--block-hours"; GateVal = "20-01" }
    "eth_h1_manual"  = @{ Magic = 667130; Sl = "3.0"; Tp = "15.0"
                          GateFlag = ""; GateVal = "" }
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

Write-Host "=== 2. pull latest code (bot .py with gates + watchdog) ===" -ForegroundColor Cyan
Set-Location $Repo
git pull origin main
Copy-Item forex_live_bot_gold_cwider.py $Desktop -Force
Copy-Item watchdog_h1.ps1 $Desktop -Force

Write-Host "=== 3. capture the 3 bots' REAL command lines ===" -ForegroundColor Cyan
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
    if ($t.GateFlag -ne "") {
        if ($newArgs -match [regex]::Escape($t.GateFlag) + '\s+\S+') {
            $newArgs = $newArgs -replace ([regex]::Escape($t.GateFlag) + '\s+\S+'), "$($t.GateFlag) $($t.GateVal)"
        } else {
            # insert BEFORE --allow-real so the safety flag stays last
            $newArgs = $newArgs -replace '--allow-real', "$($t.GateFlag) $($t.GateVal) --allow-real"
        }
    }
    $relaunch += [pscustomobject]@{ Pid = $p.ProcessId; Tag = $tag; Args = $newArgs }
    Write-Host ("  {0,-18} PID {1}" -f $tag, $p.ProcessId)
    Write-Host ("      -> {0}" -f $newArgs)
}
if ($relaunch.Count -ne 3) {
    Write-Host "ABORT: expected 3 target bots running, found $($relaunch.Count)." -ForegroundColor Red
    exit 1
}

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
    $t = $TARGETS[$tag]
    $sl = if ($p.CommandLine -match '--sl-atr\s+(\S+)') { $Matches[1] } else { "?" }
    $tp = if ($p.CommandLine -match '--tp-atr\s+(\S+)') { $Matches[1] } else { "?" }
    $gateOk = $true
    if ($t.GateFlag -ne "") {
        $gateOk = $p.CommandLine -match ([regex]::Escape($t.GateFlag) + '\s+' + [regex]::Escape($t.GateVal))
    }
    $ok = ($sl -eq $t.Sl -and $tp -eq $t.Tp -and $gateOk)
    if (-not $ok) { $bad++ }
    $gateTxt = if ($t.GateFlag -ne "") { "$($t.GateFlag)=$(if ($gateOk) {'ON'} else {'MISSING'})" } else { "no-gate" }
    Write-Host ("  {0,-18} sl-atr={1,-5} tp-atr={2,-6} {3,-34} {4}" -f $tag, $sl, $tp, $gateTxt, $(if ($ok) { "OK" } else { "<-- WRONG" }))
}
if ($bad -eq 0 -and $live.Count -eq 9) {
    Write-Host "  TARGETS ON SL2.5-3.0/TP15 + GATES - DONE" -ForegroundColor Green
} else {
    Write-Host "  SOMETHING IS OFF - INVESTIGATE" -ForegroundColor Red
}
