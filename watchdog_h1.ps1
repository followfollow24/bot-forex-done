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
#    --timeframe 1h    entry timeframe (NOT 15m -- that is the whole point)
#    --tp-atr 999      TP disabled; the user closes by hand
#    --manual-exit     loads GoldManualExitBot (gold_manual_exit_bot.py), which
#                      sends a Telegram alert every time unrealized P&L crosses
#                      a new whole-ATR milestone in either direction. Without
#                      this flag the bot just sits at TP=999 with NO alerts at
#                      all -- added 2026-07-29 after the first deploy omitted
#                      it. The class is symbol-generic (reads self.bsym /
#                      self.variant_tag), so it works unchanged for BTC/ETH/gold.
#    --risk 1.90       1.9%, not 2.0: lot is rounded to 2dp, and a round-up past
#                      the cfg cap of 2.0% makes the bot SKIP the trade entirely
#
#  [2026-08-05] btc_h1_manual updated -- TWO changes, both deliberate:
#    1. --risk 1.90 -> 1.00. The 2026-08-02 risk-parity rebalance (deploy_kz.ps1)
#       changed the RUNNING process to 1.00 but never updated this file, so any
#       watchdog restart would have silently reverted the approved rebalance back
#       to 1.90. This file now matches what is actually live.
#    2. --adx-min 18 -> 10, plus --touch-tolerance 0.012 (class default 0.0015).
#       Validated 2026-08-05 on the fixed engine, BTC H1, real costs:
#         OOS split  train PF 1.51/Sharpe 1.93 -> OOS PF 1.29/Sharpe 1.00
#         yearly WF  9/10 years PF>1 (only 2022 bear fails, PF 0.69)
#         vs the old adx18/0.0015 config: OOS Sharpe 0.81, CAGR +11%
#       Trade frequency is essentially unchanged (~0.33 -> ~0.36 trades/day);
#       the gain is signal QUALITY, not more trades. Live-path causal check
#       passed: HybridTrendPullback (never precompute'd, as live runs it) gives
#       0 signal mismatches vs the validated FastHybridTrendPullback path over
#       1,155 real BUY/SELL signals.
#
#  [2026-08-05] gold_h1_manual updated -- and TWO drifts corrected at the same
#  time. What was ACTUALLY running differed from this file in two ways: the
#  running process had NO --regime-filter and --risk 0.30, while this file said
#  --regime-filter and --risk 1.90. A watchdog restart would have switched the
#  regime filter ON and multiplied risk by 6x, silently.
#    new config: --adx-min 10 --touch-tolerance 0.012 --risk 0.30, no regime filter
#    Validated at the CORRECT gold spread of 0.24 (see the spread note below):
#      OOS split  train PF 1.24 / Sharpe 0.62 -> OOS PF 1.21 / Sharpe 0.57
#      yearly WF  11/14 years PF>1
#      old live (adx22, no regime, touch 0.0015): OOS PF 1.15 / Sharpe 0.27
#    Trades roughly TRIPLE (48/yr -> 141/yr) while quality improves.
#
#  GOLD SPREAD WARNING: the real XAUUSDc spread is $0.24 (measured live from
#  MT5 2026-08-05: point 0.001, 240 points). Many backtest scripts in this repo
#  hardcode 2.85, which is ~12x too large -- it is 255% of gold's M15 ATR, which
#  no broker charges. Every gold "failure" computed at 2.85 must be re-tested
#  before being believed; gold_h1_manual itself flips from PF 0.63 to PF 1.07
#  purely from that correction.
#
#  [2026-08-07] --tp-atr 999 -> 5.0 on all three. User wants a real broker-side
#  safety TP so a big unrealized gain can't fully round-trip back to a loss
#  while unattended (asleep / not watching). This is a genuine MT5 TP order
#  sent at position-open (forex_live_bot_gold_cwider.py _open_position()), so
#  it fires even if the bot process hangs -- unlike the milestone Telegram
#  alerts, which need the process alive to send.
#  Backtested tradeoff before choosing this (adx10/touch0.012 configs, real
#  costs): BTC Sharpe 1.30->1.26 CAGR +21.5%->+16.2%; GOLD Sharpe 0.58->0.32
#  CAGR +6.3%->+1.6% (the biggest hit -- gold's edge leans on a few large
#  winners); ETH Sharpe 0.21->0.20 (near neutral). A trailing-stop-after-+5xATR
#  alternative preserves more edge (gold Sharpe actually improves to 0.60) but
#  requires live code that does not exist yet -- modify_sl() in
#  forex_executor.py has zero callers anywhere in this repo, so no live bot
#  has ever actually trailed a stop. User chose the flat TP now over waiting
#  for that to be built and causally verified.
# -----------------------------------------------------------------------------
#  [2026-08-07 #2] SL/TP retune on the three trend-pullback manual bots:
#    btc_h1_manual  : --sl-atr 3.0 -> 2.5   --tp-atr 5.0 -> 15.0
#    gold_h1_manual : --sl-atr 3.0 -> 2.5   --tp-atr 5.0 -> 15.0
#    eth_h1_manual  : --sl-atr 3.0 (kept)   --tp-atr 5.0 -> 15.0
#  Validated same day (fixed engine, real costs, corrected $0.24 gold spread):
#    BTC  SL2.5/TP15: PF 1.28 Sharpe 1.33 CAGR +22.9% | OOS half 2: Sharpe 0.90
#         CAGR +20.0% (beats live SL3/TP5's OOS +14.7%) | yearly WF 9/10 PF>1
#    GOLD SL2.5/TP15: PF 1.19 Sharpe 0.71 CAGR +5.5% DD 14.6% | OOS half 2
#         Sharpe 0.98 (train 0.37 -- no overfit signature) | yearly WF 10/14
#    ETH  edge too thin to rank SL2.5 vs SL3.0 (all noise-level); kept SL3.0,
#         which points slightly better at TP15 (Sharpe 0.27 vs 0.12).
#  Neighbour grid around 2.5/15 is a plateau (BTC Sharpe 1.21-1.35), not a
#  lone spike. TP15 is still hit ~8-11 times/yr (6-7% of trades) -- rare by
#  design; it is the unattended-safety cap, not the routine exit. Exit mix at
#  TP15: ~58-64% SL, ~29-36% max-hold timeout (64 bars), rest TP.
#  EXPECTATION SET HONESTLY: combined portfolio backtest = ~+40%/yr = ~0.1%/day.
#  The user's stated 0.3%/day goal is NOT reachable from SL/TP tuning at
#  acceptable risk (3x risk -> DD from ~58% toward wipeout); it needs 3-5 more
#  uncorrelated edges (same conclusion as the 2026-07-09 target reset).
#  The other 6 bots (donchian/tool_*/momentum families) keep TP5: SL2.5/TP15
#  was validated on the trend-pullback family only.
# -----------------------------------------------------------------------------
#  [2026-08-08] ENTRY GATES added to two bots (validated 2026-08-07 on the
#  SL2.5/TP15 base, adversarially verified, live-path causal check passed
#  with 0 mismatches -- see gate_* functions in forex_live_bot_gold_cwider.py):
#    gold_h1_manual : --block-hours 20-01
#        blocks entries whose fill lands in UTC [20:00,01:00) -- the
#        pre-rollover / late-NY dead zone. Sharpe 0.71->0.95, CAGR
#        +5.50->+7.07%, DD 14.6->12.3%, yearly PF>1 10/14 -> 12/14.
#    btc_h1_manual  : --xasset-short-gate ETHUSDc:36:168
#        blocks SHORT entries unless ETH/BTC ratio EMA36>EMA168 (H1).
#        Sharpe 1.33->1.47, CAGR +22.9->+24.2%, DD 22.6->12.2%;
#        2022 bear loss -23.5%->-12.1%.
#  REJECTED in the same round (do not add): funding-a70 filter and the
#  a70+r36S stack -- both worse than base in the recent OOS half.
#  Gates are fail-open: any evaluation error = entry allowed = old behavior.
# -----------------------------------------------------------------------------
$bots = @(
    @{
        Symbol       = "btcusdc"
        Variant      = "btc_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol BTCUSDc --variant-tag btc_h1_manual --timeframe 1h --sl-atr 2.5 --tp-atr 15.0 --manual-exit --adx-min 10 --touch-tolerance 0.012 --max-positions 1 --risk 1.00 --xasset-short-gate ETHUSDc:36:168 --allow-real"
    },
    @{
        Symbol       = "ethusdc"
        Variant      = "eth_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol ETHUSDc --variant-tag eth_h1_manual --timeframe 1h --sl-atr 3.0 --tp-atr 15.0 --manual-exit --adx-min 18 --max-positions 1 --risk 1.90 --allow-real"
    },
    @{
        Symbol       = "xauusdc"
        Variant      = "gold_h1_manual"
        StaleMinutes = 5
        Args         = "forex_live_bot_gold_cwider.py --symbol XAUUSDc --variant-tag gold_h1_manual --timeframe 1h --sl-atr 2.5 --tp-atr 15.0 --manual-exit --adx-min 10 --touch-tolerance 0.012 --max-positions 1 --risk 0.30 --block-hours 20-01 --allow-real"
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
