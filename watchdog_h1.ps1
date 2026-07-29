# =============================================================================
#  watchdog_h1.ps1 -- keeps the H1 manual-exit bot set alive
# =============================================================================
#  Replaces the previous watchdog, which still pointed at the retired M15 set
#  (adx20tp7 / adx18tp7 / regime22 / btc_cons / btc_aggr). Those were stopped on
#  2026-07-29 because at the cost they actually pay -- ~$2.85 spread+slippage
#  against a ~$6 M15 ATR on gold -- the M15 family backtests at PF ~0.5. Leaving
#  the old watchdog enabled would have resurrected them.
#
#  ASCII-ONLY. PowerShell 5.1 without a BOM misparses non-ASCII, and this file
#  is launched by Task Scheduler where a parse failure is silent.
#
#  Heartbeat logic is unchanged from the original: each bot rewrites its
#  HEARTBEAT_<SYMBOL>_<VARIANT> file every poll (~30s regardless of entry
#  timeframe, so 5 minutes stays a generous staleness threshold even though
#  these bots trade on H1 bars).
# =============================================================================

$DESKTOP = "C:\Users\Administrator\Desktop"
$PYTHON  = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
$LOGFILE = "$DESKTOP\watchdog_h1_$(Get-Date -Format 'yyyy-MM-dd').log"

function WLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $LOGFILE -Value $line
}

# -----------------------------------------------------------------------------
#  Bot definitions. Args must match EXACTLY what was launched by hand on
#  2026-07-29, or a watchdog restart would silently change live parameters:
#    --timeframe 1h   entry timeframe (NOT 15m -- that is the whole point)
#    --tp-atr 999     TP disabled; the user closes by hand
#    --risk 1.90      1.9%, not 2.0: lot is rounded to 2dp, and a round-up past
#                     the cfg cap of 2.0% makes the bot SKIP the trade entirely
# -----------------------------------------------------------------------------
$bots = @(
    @{
        Symbol       = "btcusdc"
        Variant      = "btc_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --adx-min 18 --max-positions 1 --risk 1.90 --allow-real"
    },
    @{
        Symbol       = "ethusdc"
        Variant      = "eth_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol ETHUSDc --variant-tag eth_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --adx-min 18 --max-positions 1 --risk 1.90 --allow-real"
    },
    @{
        Symbol       = "xauusdc"
        Variant      = "gold_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol XAUUSDc --variant-tag gold_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 999 --adx-min 22 --regime-filter --max-positions 1 --risk 1.90 --allow-real"
    }
)

foreach ($bot in $bots) {
    $variant = $bot.Variant

    # A kill-switch means the operator deliberately stopped this bot. Restarting
    # it would override that decision, so skip it entirely -- the STOP_ file only
    # blocks NEW entries inside the bot, it does not stop the process, so without
    # this check the watchdog and the operator would fight each other.
    $stopFile = "$DESKTOP\STOP_$($bot.Symbol.ToUpper())_$($variant.ToUpper())"
    if (Test-Path $stopFile) {
        WLog "[$variant] kill-switch present ($stopFile) -- NOT restarting"
        continue
    }

    $heartbeatFile = "$DESKTOP\HEARTBEAT_$($bot.Symbol.ToUpper())_$($variant.ToUpper())"
    $needRestart = $false

    if (-not (Test-Path $heartbeatFile)) {
        WLog "[$variant] heartbeat file not found ($heartbeatFile) -- treating as STALE"
        $needRestart = $true
    } else {
        $lastWrite = (Get-Item $heartbeatFile).LastWriteTimeUtc
        $staleMin  = ((Get-Date).ToUniversalTime() - $lastWrite).TotalMinutes
        if ($staleMin -gt $bot.StaleMinutes) {
            WLog "[$variant] STALE: $([math]::Round($staleMin,1)) min > threshold $($bot.StaleMinutes) min -- restarting"
            $needRestart = $true
        } else {
            WLog "[$variant] OK -- heartbeat $([math]::Round($staleMin,1)) min ago (threshold=$($bot.StaleMinutes))"
        }
    }

    if ($needRestart) {
        $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
                 Where-Object { $_.CommandLine -like "*--variant-tag $variant*" }
        if ($procs) {
            foreach ($p in $procs) {
                WLog "[$variant] killing stuck PID $($p.ProcessId)"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 3
        } else {
            WLog "[$variant] no running process found (already dead) -- starting fresh"
        }
        Start-Process $PYTHON -ArgumentList $bot.Args -WorkingDirectory $DESKTOP -WindowStyle Normal
        WLog "[$variant] restarted"
    }
}
