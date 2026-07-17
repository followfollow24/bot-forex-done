# =============================================================================
# watchdog.ps1 - External defense layer for forex_live_bot_gold_cwider.py
# =============================================================================
# Checks each bot's HEARTBEAT_<SYMBOL>_<VARIANT> file (a plain-text timestamp
# written every main-loop iteration, independent of the Python logging module).
# If a bot's heartbeat is stale beyond its threshold, kill that specific process
# and restart it with the same args used by deploy.ps1. Only the stale variant
# is touched -- others are left alone.
#
# [FIX b] Why heartbeat file instead of parsing "== STATUS ==" from the log:
#   A real incident showed the RotatingFileHandler silently stop writing to the
#   .log file while the actual bot process kept running and trading correctly
#   for hours afterward (proven by successful OPEN/CLOSE-SL events and console
#   output). Watchdog logic based on the log file's last STATUS line would have
#   seen "stale" and wrongly killed a perfectly healthy bot. HEARTBEAT_FILE is
#   written with a direct open()/write() call with no logging module involved,
#   so it cannot fail the same silent way -- if the process is alive and looping,
#   the heartbeat file's mtime updates every poll_interval_sec (default 30s),
#   regardless of whatever caused the file-log handler issue.
#
# Why this layer exists at all (even with the in-bot MT5-call timeout):
#   Layer 1 (_call_with_timeout in the .py) converts an MT5 IPC hang into a
#   TimeoutError to trigger reconnect -- but the truly-hung thread is never
#   killed (Python cannot kill a blocking C-extension call). If the IPC is
#   deeply broken, reconnect may not help since the old handle is still stuck
#   inside the process. The only guaranteed fix is killing the whole process so
#   Windows reclaims the resource -- that is this script's job.
#
# Usage:
#   Run via Task Scheduler every 5 minutes (see setup_watchdog_task.ps1)
#   Manual test: cd C:\Users\Administrator\Desktop; .\watchdog.ps1
#
# Cautions:
#   - Do not run more often than every 5 minutes (avoid racing deploy.ps1,
#     which stops every forex bot process).
#   - If deploy.ps1 happens to run at the same moment, watchdog may see a
#     temporarily stale heartbeat and restart redundantly -- low impact (just a
#     redundant restart), but disable the Task before running deploy.ps1 if worried.
# =============================================================================

$DESKTOP = "C:\Users\Administrator\Desktop"  # hardcoded & explicit: task now runs as interactive Administrator (see setup_watchdog_task.ps1) so restarts land in the MT5 session

# Bot definitions -- args must match deploy.ps1 exactly.
# --allow-real is required: without it the bot exits immediately on a non-demo
# (Real Cent) account, leaving any open position unmonitored on every restart.
# Symbol is per-bot now (was a single global $SYMBOL) since BTC-HF variants use
# a different symbol (BTCUSDc) than gold (xauusd) -- must match the slug
# _make_paths() uses (symbol.lower()) for each bot's own heartbeat filename.
$bots = @(
    @{
        Symbol       = "xauusd"
        Variant      = "adx20tp7"
        StaleMinutes = 5   # heartbeat updates every poll_interval_sec (~30s) -- 5 min is a generous buffer
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real"
    },
    @{
        Symbol       = "xauusd"
        Variant      = "adx18tp7"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real"
    },
    @{
        Symbol       = "btcusdc"
        Variant      = "btc_cons"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_cons --sl-atr 4.0 --tp-atr 12.0 --adx-min 15 --timeframe 15m --max-positions 1 --risk 0.20 --allow-real"
    },
    @{
        Symbol       = "btcusdc"
        Variant      = "btc_aggr"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_aggr --sl-atr 2.5 --tp-atr 7.5 --adx-min 12 --timeframe 15m --max-positions 1 --risk 0.20 --allow-real"
    },
    @{
        Symbol       = "xauusd"
        Variant      = "regime22"
        StaleMinutes = 5
        # Gold regime filter (gold_regime_live_strategy.py): frozen ADX>22 + ADX-rising +
        # EMA-gap>1.2xATR(H1) on top of the same SL3/TP7 M15 entry. Real-engine validated
        # 2026-07-12: 2,848 trades/13yr, PF=1.29, MaxDD=10.3%, WF-A 12/14yr. magic=555103.
        Args         = "forex_live_bot_gold_cwider.py --variant-tag regime22 --sl-atr 3.0 --tp-atr 7.0 --adx-min 22 --regime-filter --timeframe 15m --max-positions 3 --risk 0.30 --allow-real"
    }
)

# Watchdog writes its own log separate from bot logs (one file per day)
$wlogFile = "$DESKTOP\watchdog_$(Get-Date -Format 'yyyy-MM-dd').log"
function WLog($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $wlogFile -Value $line
}

WLog "=== watchdog run start ==="

foreach ($bot in $bots) {
    $variant = $bot.Variant
    $heartbeatFile = "$DESKTOP\HEARTBEAT_$($bot.Symbol.ToUpper())_$($variant.ToUpper())"

    if (-not (Test-Path $heartbeatFile)) {
        WLog "[$variant] heartbeat file not found ($heartbeatFile) -- treating as STALE (never started, or running from a different folder)"
        $staleMin = 9999
    } else {
        $lastWrite = (Get-Item $heartbeatFile).LastWriteTimeUtc
        $staleMin  = (New-TimeSpan -Start $lastWrite -End (Get-Date).ToUniversalTime()).TotalMinutes
    }

    if ($staleMin -le $bot.StaleMinutes) {
        WLog "[$variant] OK -- heartbeat $([math]::Round($staleMin,1)) min ago (threshold=$($bot.StaleMinutes))"
        continue
    }

    WLog "[$variant] STALE! $([math]::Round($staleMin,1)) min > threshold $($bot.StaleMinutes) min -- restarting"

    # Find process by CommandLine (safer than killing all python.exe)
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
             Where-Object { $_.CommandLine -like "*--variant-tag $variant*" }

    if ($procs) {
        foreach ($p in $procs) {
            WLog "[$variant] Stop-Process -Id $($p.ProcessId) -Force"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 5
    } else {
        WLog "[$variant] no running process found (already dead silently) -- starting fresh"
    }

    Start-Process python -ArgumentList $bot.Args -WorkingDirectory $DESKTOP -WindowStyle Normal
    WLog "[$variant] restarted: python $($bot.Args)"
    Start-Sleep -Seconds 3
}

$runningCount = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }).Count
WLog "=== watchdog run complete -- python bots running: $runningCount / 5 ==="
