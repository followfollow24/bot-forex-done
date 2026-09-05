# start_gold_live.ps1 -- GOLD ONLY, REAL MONEY.
#
# THIS SCRIPT DOES NOT RUN BY ITSELF. It is not in watchdog_h1.ps1, no
# scheduled task calls it, and nothing starts it on boot. It exists so
# that going live is one deliberate command by the operator rather than
# a line assembled from memory at 19:29.
#
# BEFORE running it, run the pre-flight check, which sends nothing:
#     .\start_gold_live.ps1 -Check
#
# To go live it asks you to type LIVE. If that prompt will not accept
# input (RDP turns a Ctrl+V paste into a ^V control character, which
# reads as "not LIVE" and aborts), the same confirmation can be given on
# the command line instead:
#     .\start_gold_live.ps1 -IUnderstandRealMoney
#
# Configuration is the operator's, recorded here so it is never a guess:
#   XAUAUDm only  -- BTC was measured and dropped: 19:30 ranks 16th of 24
#                    hours for BTC, and 4 Sep was rank 2 of 348 sessions,
#                    a top-1% event rather than a pattern.
#   0.05 lot      -- their choice
#   SL 3xATR      -- their choice; ~69 points, ~249 AUD at 0.05 lot
#   decide +1s    -- earliest an entry may fire; watching runs from
#                    19:30:00.000 and continues until the gate clears or
#                    --max-wait expires
#   gate 8 USD    -- 11.1 points at 0.01 lot. The gate must be LARGER
#                    than the day's counter-move or it fires on the wrong
#                    side: on 4 Sep price ticked +8.5 up before falling
#                    132, so every gate under 8.5 bought the top. At 2
#                    points that day cost -96.6; at 11 it made +81.3.
#   exit fixed:30 -- hold thirty minutes rather than to the M15 close.
#                    Checked over the month, not just 4 Sep: it wins on
#                    trending days and gives a little back on choppy
#                    ones, +117.95 against +83.76 over the same 11 trades.
#   ONE position  -- pyramiding is off. --add-step-pts exists and is
#                    tested, but is not passed here.
#
# WHAT THIS CONFIGURATION WAS MEASURED TO DO
# (_last_month.py XAUAUDm 30 43.38 1 8 0.01 30):
#
#   21 sessions, the gate opened on 11 of them (52%), 6 winners
#   equity 43.38 -> 161.33 USD over the month
#   worst any trade went against it: -24.05 USD, against 43.38 of room
#   ZERO sessions reached a point that would have closed the account
#
# WHY 0.01 AND NOT THE 0.05 THE OPERATOR ASKED FOR. At 0.05 the account
# survives 12.0 points against; every one of these trades went 13 to 38
# points against at some moment, so the first one ends it. Selecting only
# the big days makes that worse, not better -- a 25-point gate fires on
# the five biggest days of the month and all five swung past 43 USD
# before paying. 0.05 needs roughly 140 USD of equity to survive last
# month at all, 200 with any margin for error.
#
# NONE OF THIS IS ESTABLISHED. One month, 11 trades, no train/TEST split,
# and 4 Sep alone is over half the profit. It is recorded here so the
# numbers travel with the command rather than living in a chat log.
#   exit m15close -- out when the M15 candle closes, 19:45:00
#   risk cap OFF  -- their explicit instruction
#
# WHAT THE MEASUREMENTS SAY, so it travels with the command: over 106
# tick-days this configuration averaged +0.88 points a trade, 43%
# winners, +337 AUD at 0.05 lot -- but train was -1.29 against TEST
# +3.06, so it is NOT established. It is the best-defined candidate
# tested, not a proven edge.

param([switch]$Check, [switch]$Dry, [switch]$IUnderstandRealMoney)

$ErrorActionPreference = "Stop"
$repo = Join-Path $env:USERPROFILE "Desktop\bot_repo"
Set-Location $repo

$common = @(
    "--symbols", "XAUAUDm",
    "--lot", "0.01",
    "--sl-atr", "3",
    "--decide-after", "1",
    "--max-wait", "900",
    "--gate-money", "8",
    "--exit-mode", "fixed:30"
)

if ($Check) {
    Write-Host "PRE-FLIGHT ONLY -- sends nothing" -ForegroundColor Cyan
    & python clock_scalp_bot.py @common --selftest
    exit $LASTEXITCODE
}

if ($Dry) {
    Write-Host "DRY RUN -- logs decisions, sends no orders" -ForegroundColor Cyan
    & python clock_scalp_bot.py @common
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "  THIS WILL TRADE REAL MONEY ON ACCOUNT " -NoNewline -ForegroundColor Yellow
Write-Host "XAUAUDm 0.05 lot, SL 3xATR, no risk cap." -ForegroundColor Yellow
Write-Host "  One trade per day at 19:30 Thai, closed at 19:45." -ForegroundColor Yellow
Write-Host ""
# The interactive prompt is the normal path. Over RDP a Ctrl+V at a
# Read-Host arrives as a literal ^V control character rather than the
# pasted word, which reads as "not LIVE" and aborts -- that happened on
# the first attempt here. -IUnderstandRealMoney is the same decision made
# on the command line instead, for when the prompt cannot be typed into.
# It is deliberately long: nobody types it by accident, and it is legible
# in shell history months later.
if (-not $IUnderstandRealMoney) {
    $answer = Read-Host "  Type LIVE to start, anything else to abort"
    if ($answer -cne "LIVE") {
        Write-Host "  aborted -- nothing started." -ForegroundColor Green
        Write-Host "  (if you pasted with Ctrl+V, it arrives as ^V here --" -ForegroundColor DarkGray
        Write-Host "   type the four letters, or use -IUnderstandRealMoney)" -ForegroundColor DarkGray
        exit 0
    }
} else {
    Write-Host "  confirmed on the command line." -ForegroundColor Yellow
}
& python clock_scalp_bot.py @common --live
