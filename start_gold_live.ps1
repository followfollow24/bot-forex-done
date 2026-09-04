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
# Configuration is the operator's, recorded here so it is never a guess:
#   XAUAUDm only  -- BTC was measured and dropped: 19:30 ranks 16th of 24
#                    hours for BTC, and 4 Sep was rank 2 of 348 sessions,
#                    a top-1% event rather than a pattern.
#   0.05 lot      -- their choice
#   SL 3xATR      -- their choice; ~69 points, ~249 AUD at 0.05 lot
#   decide +3s    -- direction read 3 seconds after 19:30:00
#   gate 3x spread-- skip the day unless the move clears 3.39 points
#   exit m15close -- out when the M15 candle closes, 19:45:00
#   risk cap OFF  -- their explicit instruction
#
# WHAT THE MEASUREMENTS SAY, so it travels with the command: over 106
# tick-days this configuration averaged +0.88 points a trade, 43%
# winners, +337 AUD at 0.05 lot -- but train was -1.29 against TEST
# +3.06, so it is NOT established. It is the best-defined candidate
# tested, not a proven edge.

param([switch]$Check, [switch]$Dry)

$ErrorActionPreference = "Stop"
$repo = Join-Path $env:USERPROFILE "Desktop\bot_repo"
Set-Location $repo

$common = @(
    "--symbols", "XAUAUDm",
    "--lot", "0.05",
    "--sl-atr", "3",
    "--decide-after", "3",
    "--min-move-spread", "3",
    "--exit-mode", "m15close"
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
$answer = Read-Host "  Type LIVE to start, anything else to abort"
if ($answer -cne "LIVE") {
    Write-Host "  aborted -- nothing started." -ForegroundColor Green
    exit 0
}
& python clock_scalp_bot.py @common --live
