# =============================================================================
# watchdog.ps1 - Defense layer #2 (external) for forex_live_bot_gold_cwider.py
# =============================================================================
# Check STATUS line of 2 bots. If stale beyond threshold -> kill that process
# (only the stale one, leave others alone) then restart with same args as deploy.ps1
#
# Why this layer exists (even after fixing timeout inside bot):
#   Layer 1 (_call_with_timeout in .py) converts MT5-IPC-hang into TimeoutError
#   to trigger reconnect -- but the hung thread is NOT killed (Python can't kill
#   blocking C-calls). If IPC dies deeply, reconnect may not help because the old
#   handle is still stuck in the process. The only reliable fix is to kill the
#   whole process and let Windows reclaim resources -- that is this script's job.
#
# Usage:
#   Schedule via Task Scheduler every 5-10 minutes (see setup_watchdog_task.ps1)
#   Manual test: cd C:\Users\Administrator\Desktop; .\watchdog.ps1
#
# Caution:
#   - Do not run more often than every 5 minutes (avoid race with deploy.ps1)
#   - If deploy.ps1 is running, watchdog may see stale log during restart and
#     restart again -- low impact (just double-restart) but disable Task if worried
# =============================================================================

$DESKTOP = "$env:USERPROFILE\Desktop"
$SYMBOL  = "xauusd"

# Bot definitions -- args must match deploy.ps1 exactly
$bots = @(
    @{
        Variant      = "adx20tp7"
        StaleMinutes = 30
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx20tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 20 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real"
    },
    @{
        Variant      = "adx18tp7"
        StaleMinutes = 30
        Args         = "forex_live_bot_gold_cwider.py --variant-tag adx18tp7 --sl-atr 3.0 --tp-atr 7.0 --adx-min 18 --timeframe 15m --max-positions 3 --risk 0.30 --allow-real"
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
    $logFile = "$DESKTOP\forex_${SYMBOL}_$variant.log"

    if (-not (Test-Path $logFile)) {
        WLog "[$variant] log not found ($logFile) -- skipping (bot may not have started yet)"
        continue
    }

    # Find latest STATUS line in last 300 lines
    # Do NOT use (Get-Item).LastWriteTime alone -- error-loops (log.error spam during
    # reconnect) keep the file "active" even when main loop is frozen. Must check
    # STATUS line specifically because it means the loop actually completed a cycle.
    $statusLine = Get-Content $logFile -Tail 300 -ErrorAction SilentlyContinue |
                  Select-String "== STATUS ==" | Select-Object -Last 1

    if (-not $statusLine) {
        WLog "[$variant] no STATUS line in last 300 lines -- treating as stale"
        $staleMin = 9999
    } else {
        # Timestamp format: "2026-07-03 01:26:00,123 [INFO] == STATUS == ..."
        $tsStr = ($statusLine.Line -split '\[')[0].Trim()
        try {
            $tsClean = $tsStr.Substring(0, 19)
            $ts = [datetime]::ParseExact($tsClean, "yyyy-MM-dd HH:mm:ss", $null)
            $staleMin = (New-TimeSpan -Start $ts -End (Get-Date)).TotalMinutes
        } catch {
            WLog "[$variant] cannot parse timestamp from '$tsStr' -- treating as stale (fail-safe)"
            $staleMin = 9999
        }
    }

    if ($staleMin -le $bot.StaleMinutes) {
        WLog "[$variant] OK -- STATUS $([math]::Round($staleMin,1)) min ago (threshold=$($bot.StaleMinutes))"
        continue
    }

    WLog "[$variant] STALE! $([math]::Round($staleMin,1)) min > threshold $($bot.StaleMinutes) -- restarting"

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
        WLog "[$variant] no process found (already dead) -- starting fresh"
    }

    Start-Process python -ArgumentList $bot.Args -WorkingDirectory $DESKTOP -WindowStyle Normal
    WLog "[$variant] restarted: python $($bot.Args)"
    Start-Sleep -Seconds 3
}

$runningCount = (Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                  Where-Object { $_.CommandLine -like "*forex_live_bot_gold_cwider.py*" }).Count
WLog "=== watchdog run complete -- python bots running: $runningCount / 2 ==="
