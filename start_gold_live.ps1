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
# ---------------------------------------------------------------------
# THE CONFIGURATION, and what each number was measured to do
# ---------------------------------------------------------------------
#   XAUAUDm only  -- BTC was measured and dropped: 19:30 ranks 16th of 24
#                    hours for BTC, and 4 Sep was rank 2 of 348 sessions,
#                    a top-1% event rather than a pattern.
#   gate 14 pts   -- fixed distance, not a money gate. The gate must be
#                    LARGER than the day's counter-move or it fires on the
#                    wrong side: on 4 Sep price ticked +8.5 up before
#                    falling 132, so every gate under 8.5 bought the top.
#                    Swept over 67 sessions at 0.01 lot: 11 pts won 45% of
#                    the time, 14 pts 60%, 17 pts 67%, 20 pts 71%, 25 pts
#                    75%. 14 had the best total (+194) with the win rate
#                    already above a coin flip.
#                    It is --gate-pts and not --gate-money deliberately.
#                    A money gate is a distance divided by the lot, so
#                    with two lot sizes in play the same 10 USD would mean
#                    14 points on one day and 2.8 on another.
#   0.01 ordinary -- the size on a normal day.
#   0.05 fast     -- the size when the gate falls within 10 seconds.
#                    Over three months six sessions did: 2 Jul (2.9s),
#                    14 Jul (1.3s), 7 Aug (1.6s), 12 Aug (6.3s), 3 Sep
#                    (1.7s), 4 Sep (1.4s). Those were much the largest
#                    runs of the period -- +84, +38 and +31 points against
#                    a typical day's single figures. Five of the six won.
#   floor 180     -- and this is the number that makes the tier survivable
#                    rather than fatal. Every one of those six sessions
#                    went 20 to 33 points AGAINST the entry before it
#                    paid: 71 to 121 USD at 0.05 lot. On the 43.38 the
#                    account held while this was written, all six ended
#                    it. Below 180 the bot takes the fast day at 0.01
#                    instead and says so in the log. It re-reads equity at
#                    every entry, so one bad fast day disarms the next.
#   SL 3xATR      -- operator's choice; ~69 points.
#   decide +1s    -- earliest an entry may fire; watching runs from
#                    19:30:00.000 until the gate clears or --max-wait.
#   exit fixed:30 -- hold thirty minutes rather than to the M15 close.
#                    Checked over a month: +117.95 against +83.76 on the
#                    same 11 trades.
#   ONE position  -- pyramiding is off. --add-step-pts exists and is
#                    tested, but is not passed here.
#   risk cap OFF  -- operator's explicit instruction.
#
# WHY 10 SECONDS AND NOT 5. Five measured better (+685 against +644) for
# one reason only: it excludes 12 Aug, at 6.3s the single fast session
# that lost. Picking the threshold that dodges the one loser in six is
# fitting to n=1. Ten seconds also matches the operator's own reading --
# their examples were 1, 1, 2 and 6 seconds.
#
# NONE OF THIS IS ESTABLISHED. Three months, 25 trades, six fast days, no
# train/TEST split, and 4 Sep alone is over half the profit of the fast
# tier. The ordinary-lot baseline is +194 over the same 67 sessions with
# no reliance on that day. Recorded here so the numbers travel with the
# command rather than living in a chat log.

param([switch]$Check, [switch]$Dry, [switch]$IUnderstandRealMoney)

$ErrorActionPreference = "Stop"
$repo = Join-Path $env:USERPROFILE "Desktop\bot_repo"
Set-Location $repo

$common = @(
    "--symbols", "XAUAUDm",
    "--lot", "0.01",
    "--fast-sec", "10",
    "--fast-lot", "0.05",
    "--fast-min-equity", "180",
    "--gate-pts", "14",
    "--sl-atr", "3",
    "--decide-after", "1",
    "--max-wait", "900",
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
Write-Host "  THIS WILL TRADE REAL MONEY." -ForegroundColor Yellow
Write-Host "  XAUAUDm, one trade a day at 19:30 Thai, held 30 minutes." -ForegroundColor Yellow
Write-Host "  0.01 lot normally; 0.05 when the gate falls inside 10s AND" -ForegroundColor Yellow
Write-Host "  equity is at least 180. SL 3xATR. No risk cap." -ForegroundColor Yellow
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
