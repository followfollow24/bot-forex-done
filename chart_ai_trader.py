#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_ai_trader.py -- a standalone live bot whose ENTIRE entry signal is
"what does the AI see on the H1 chart", not price-action rules, not news.
Built at the user's explicit request (2026-08-12) as a separate bot from
news_gemini_bot.py -- deliberately NOT wired into it, so this bot's
failure modes can't touch the news/chart-veto system already live and
trading, and vice versa.

DUAL-CONSENSUS, LIKE news_gemini_bot.py'S NEWS STAGE (not like its chart
VETO stage): Gemini and OpenAI each independently receive the SAME chart
image PLUS the same numeric market payload (close, EMA20/50, ATR, recent
swing high/low, last 5 bars), and each independently returns
LONG/SHORT/WAIT with absolute entry/sl/tp. A trade opens only if BOTH
return the same LONG or SHORT, after which their levels are averaged and
validated. This is a full trading-decision gate (unlike
log_anomaly_scanner.py's union-of-findings design) because a wrong entry
here costs real money, same reasoning as news_gemini's own news stage.

RISK TIER: the user explicitly chose to size this like a normal technical
bot (0.30-0.50%/trade), not news_gemini's ultra-conservative 0.15% --
despite this being an equally unvalidated signal source. Defaults to the
conservative end of that explicit range (0.30%) since "sized like a
technical bot" was about magnitude, not a claim that this has technical
bots' walk-forward-validated track record. Adjust via --risk.

EXIT MANAGEMENT: pure broker-side SL/TP -- placed once at entry, then left
alone (no code-driven time-stop, no trailing, no discretionary
re-evaluation of open positions). [2026-08-12] The DISTANCES are no longer
fixed: both models place entry/sl/tp per setup, the two are averaged and
validated (correct sides, stop inside the 1.5-2.0xATR band the prompt
asks for, R:R >= 1.5, entry close to the live price). The DISTANCES are
then applied to the actual fill rather than the AI's stated prices, so
price moving between decision and fill cannot distort the intended risk
or reward. Because lot sizing divides by the actual stop distance, a
wider AI-chosen stop produces a smaller lot -- the $-risk per trade stays
pinned to --risk no matter what the models pick, which is what makes
delegating the stop safe.

** UNVALIDATED STRATEGY -- no historical backtest exists for "AI reads a
chart and decides long/short/none". Live by explicit user decision,
same class of risk as news_gemini_bot.py at launch. **

Usage:
  python chart_ai_trader.py --dry-run             paper mode, no real orders
  python chart_ai_trader.py --allow-real           confirms trading a REAL
                                                   (non-demo) account
  python chart_ai_trader.py --risk 0.30 --poll-min 60 --allow-real
"""
import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from forex_config import ForexConfig
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 671xxx family: unused by any existing bot (555/666/667/668 are the
# forex_live_bot_gold_cwider.py + daily_sleeves_bot.py families,
# 669001 is news_gemini_bot.py).
MAGIC = 671001

# [2026-08-14] ETHUSDC dropped from the default set on measured cost.
# _straddle_geometry.py swept ~3,800 M15 entries per symbol and reported
# spread as a fraction of the intended stop distance -- the honest unit,
# since it is what the cost actually competes against:
#     XAUUSDc  0.020R/trade  -> breakeven WR 40.8%
#     BTCUSDc  0.068R/trade  -> breakeven WR 42.7%
#     ETHUSDC  0.093R/trade  -> breakeven WR 43.7%   (4.6x gold)
# ETH hands back ~9% of every trade's risk before direction is even
# considered.
#
# [2026-08-15] REMOVED ENTIRELY at the user's explicit instruction: ETH is
# not to be traded again by any bot. It is gone from ALL_SYMBOLS, not just
# from the default, so --symbols cannot bring it back -- an unknown symbol
# is rejected at startup. This is deliberate: leaving it "restorable"
# meant one CLI flag stood between the account and a symbol whose drag is
# 4.6x gold's, and after MAX_SPREAD_R rose to 0.12 the cost gate no longer
# blocks ETH either, so the symbol list was the last guard.
#
# Live evidence behind the instruction: ETH went 0 wins from 7 chart_ai
# trades, 5 of which never showed even +0.1R, and eth_h1_manual lost
# 335.14 on its single trade at the highest risk setting in the fleet.
#
# Do NOT re-add. If it is ever reconsidered, that is a new decision needing
# fresh measurement, not a revert of this line.
SYMBOLS = {
    "XAUUSD": "gold",
    "BTCUSDC": "BTC",
}
ALL_SYMBOLS = dict(SYMBOLS)

# Live per-trade spread ceiling, as a fraction of the stop distance.
# The table above is a historical average; this is the same quantity
# measured at the moment of entry, so it also catches a symbol whose
# spread blows out temporarily (news, rollover, thin book) even if its
# average is fine.
#
# 0.08 originally encoded the same decision as the symbol list -- above
# BTC's measured 0.068, below ETH's 0.093 -- rather than anything derived
# from expected value.
#
# [2026-08-15] Raised to 0.12. BTC volatility halved and the gate began
# rejecting every inverted setup: the stop distance fell from ~200 points
# to 81-102 while the $10 spread stayed put, so the same trade now costs
# 0.098-0.123R instead of 0.050R. Four consensus decisions in one morning
# were skipped, which stalls the 40-trade sample the stopping rule needs.
#
# Why 0.12 and not 0.15: this is NOT merely "pay a bit more". Every trade
# behind the +0.353R walk-forward figure was taken at ~0.068R cost, in a
# higher-ATR regime. Widening the gate admits setups from a volatility
# regime with no measurements behind it at all -- tighter stops, the same
# noise, spread a larger share of the risk. 0.12 steps one notch outside
# the measured envelope (1.8x the sampled cost) and still rejects the
# most expensive case seen today; 0.15 would be 2.2x and admit everything.
# Required win rate moves 48.0% -> 49.8%, against a measured 62.5%.
#
# _atr_regime_geometry.py checks whether the low-ATR regime is actually
# worse. If it is, put this back to 0.08.
MAX_SPREAD_R = 0.12

TIMEFRAME = "15m"    # [2026-08-12] was 1h, changed at the user's request.
CHART_BARS = 160     # 160 M15 bars = ~40h of context. On H1 this was 120
                     # bars (~5 days); M15 needs more bars to still show a
                     # meaningful stretch of market rather than one session.
POLL_MIN = 15        # one M15 bar -- matches the entry timeframe, same way
                     # the H1 version polled hourly.

# [2026-08-12 v2] Reworked to the user's supplied spec. Three changes that
# matter, beyond the prompt rewrite:
#
#  1. The models are now given the NUMBERS (close, EMA20, EMA50, ATR,
#     recent swing high/low, last bars) alongside the chart image, instead
#     of having to read values off pixels. This is the single biggest
#     expected quality win: "price is above EMA20" becomes a comparison of
#     two floats it was handed rather than an eyeball judgement, and the
#     2-of-3 rule below is checkable rather than impressionistic.
#  2. They now return ABSOLUTE PRICES (entry/sl/tp), per the spec, rather
#     than ATR multiples. That is friendlier to state but needs more
#     validation -- a price is not scale-free, so a hallucinated or stale
#     number can be nonsense in a way a multiple cannot. See
#     cross_check_signal() for the side/sanity checks that result.
#  3. The confidence gate is GONE. The spec replaces it with the explicit
#     2-of-3 rule plus dual consensus, which is what stops a weak setup
#     now. Deliberate: the old CONF_MIN was the thing making the bot
#     almost never trade, and the user asked for it to not be so hard to
#     enter ("ไม่ออกไม้ยากเกินไป").
#
# SL band per the spec (1.5-2.0 x ATR), with a small tolerance either side
# so a model that lands at 1.45 or 2.1 is accepted rather than throwing the
# whole setup away; anything outside REJECTS the trade (it is not clamped
# silently -- an out-of-band stop means the model ignored the brief, and
# guessing what it meant with real money is worse than skipping).
SL_ATR_MIN, SL_ATR_MAX = 1.2, 2.5
MIN_RR = 1.5          # spec: TP must be at least 1:1.5 reward:risk
# Target used in INVERT mode, as a multiple of the stop distance. The
# models' own 1.5R is too far once the side is flipped -- price reverses
# before reaching it. Measured on all 29 real trades (see invert_decision):
# BTC inverted wins 46.7% at 1.5R (EV +0.107R) but 73.3% at 1.25R
# (EV +0.591R). Chosen a priori, then confirmed out-of-sample on the later
# half of a chronological split at EV +0.353R. Applies ONLY when inverting;
# normal-direction trades still use whatever target the models proposed.
INVERT_TP_R = 1.25
# The AI's stated entry must sit within this many ATR of the real current
# price, or the quote is stale/hallucinated and the setup is rejected.
MAX_ENTRY_DRIFT_ATR = 1.5

DEFAULT_RISK_PCT = 0.30
# [2026-08-13] Consecutive-loss breaker DISABLED by default at the user's
# request: the goal is now to accumulate an unbiased sample for analysis,
# and a breaker that halts after 3 losses truncates exactly the tail you
# need to measure -- you can never observe a 5- or 8-loss streak, so the
# streak distribution is censored and any WR estimate is biased upward.
# 0 = never auto-stop. Set --max-consec-losses 3 to restore the old guard.
#
# Note the split: this disables the ACTION, not the MEASUREMENT. The
# streak is still counted, still persisted, and now logged on every close,
# because that count is itself one of the statistics being collected.
MAX_CONSEC_LOSSES = 0

# Consecutive failed cycles for ONE provider before the bot says so. At the
# 15-minute default poll, 4 cycles is an hour of not trading -- long enough
# that a routine 503 blip has cleared, short enough to matter. Then a
# reminder every 24 cycles (~6h) so a multi-day outage stays visible without
# becoming noise.
PROVIDER_FAIL_ALERT_CYCLES = 4
PROVIDER_FAIL_REMIND_CYCLES = 24

# [2026-08-12] Position stacking. The user asked for the bot to be able to
# keep opening as the AI sees setups, rather than one-position-per-symbol.
# These are the bound on that: NOT a limit on the idea, a limit on the
# blast radius if the AI (or a bug) answers "long" every single cycle.
# Worst-case simultaneous risk = MAX_TOTAL_POSITIONS * risk_pct, since
# every position carries its own stop sized to risk_pct. At the defaults
# that is 6 x 0.30% = 1.8% of equity if every stop hits at once.
# Raise with --max-per-symbol / --max-total, but do the multiplication
# first: at --max-total 30 the same arithmetic gives 9%.
MAX_POSITIONS_PER_SYMBOL = 3
MAX_TOTAL_POSITIONS = 6

# [2026-08-14] Raised 60 -> 120 after measuring the providers directly
# (_gemini_probe.py, 5 calls per model):
#     gemini-flash-latest        3 ok / 2x 503, avg 71,009 ms
#     gemini-flash-lite-latest   5 ok / 0x 503, avg  6,073 ms
# The model in use averaged SEVENTY-ONE SECONDS against a 60s cap, so a
# meaningful share of what the log called "failures" were our own timeout
# firing before Google answered -- not Google refusing. A 60s cap would
# also have made the fallback below useless, since the fallback is exactly
# the slow model.
#
# The watchdog constraint is now met a better way: the heartbeat is
# written before EVERY provider attempt (see _safe_signal), not just
# between symbols, so the largest gap between heartbeats is ONE call --
# 120s against the watchdog's 300s -- regardless of how many models a
# cycle tries. That decouples the two settings entirely.
AI_CALL_TIMEOUT_SEC = 120

# [2026-08-13] NEWS VETO. News can only BLOCK a chart setup, never create
# one -- the 2-of-3 chart rule stays the sole source of entries, exactly
# as the user asked ("รักษากลยุทธ์หลักให้คมเหมือนเดิม"). A clean chart
# fighting a high-impact release (CPI, NFP, a rate decision) is the case
# this exists for: price often spikes through the stop before going the
# "right" way, so sitting out is worth more than being right eventually.
#
# Implementation note -- this deliberately REUSES news_gemini_bot's
# already-live, already-tested gemini_scan()/openai_scan() rather than
# writing a fresh "is there contradicting news?" prompt. Two reasons:
#   * those functions already solve the hard part (grounded search in two
#     passes, because neither provider reliably combines a search tool
#     with a forced JSON schema in one call)
#   * the contradiction test then becomes ORDINARY CODE -- "does any
#     surfaced candidate for this symbol point the opposite way, at
#     >= NEWS_VETO_CONF_MIN?" -- instead of a second LLM judgement.
#     Deterministic, unit-testable, and it cannot hallucinate a veto.
#
# Cost is kept near zero by fetching LAZILY: the scan only runs once a
# chart consensus actually wants to trade, and is cached for the rest of
# the cycle. Cycles with no signal (the vast majority) make no extra
# calls at all.
#
# Either provider surfacing contradicting news is enough to block --
# asymmetric on purpose, same as news_gemini's chart veto: a filter whose
# only power is to reduce trading does not need consensus to act.
NEWS_VETO_CONF_MIN = 0.70
NEWS_LOOKBACK_MIN = 180

# [2026-08-13] Two ENTRY filters, added on the user's plan to lift trade
# quality. Both are enforced in PYTHON as hard gates and ALSO described to
# the models in the payload -- the prompt makes the models cooperate, the
# code makes them obey. A prompt-only rule is a suggestion; these are not.
#
# EXPECTATION SET HONESTLY: these are expected to cut trade FREQUENCY and
# shallow out drawdown, not to raise %/day. The arithmetic: going from
# 3 trades/day at 45% WR to 1 trade/day at 55% WR is 0.1125%/day either
# way -- identical. They are worth doing for survivability; the daily
# return still comes from having more uncorrelated edges.
#
# 1) HIGHER-TIMEFRAME ALIGNMENT. M15 entries that fight the H4 trend are
#    the classic way a lower-timeframe system bleeds. The rest of this
#    fleet already works this way (H1 entry gated by H4 trend); chart_ai
#    was the only bot with no higher-timeframe context at all.
HTF_TIMEFRAME = "4h"
HTF_BARS = 120
MTF_TIMEFRAME = "1h"       # shown to the model as extra context, not gated
MTF_BARS = 120
REQUIRE_HTF_ALIGNMENT = True

# 2) KEY LEVELS. An entry floating in open space has no structure to lean
#    on; one near a level real participants watch does. Levels are computed
#    in PYTHON from daily bars (previous day H/L/C plus standard pivots) --
#    facts, not something the model is asked to recall or estimate.
#    1.5 ATR, not the 0.5 originally proposed: on gold M15 an ATR is ~6, so
#    0.5 ATR would demand price sit within ~3 points of a level, which
#    almost never happens and would silently stop the bot trading.
KEY_LEVEL_MAX_ATR = 1.5
REQUIRE_KEY_LEVEL = True


CHART_SIGNAL_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string"},   # "LONG" | "SHORT" | "WAIT"
        "entry": {"type": "number"},
        "sl": {"type": "number"},
        "tp": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["decision", "entry", "sl", "tp", "reason"],
}

ENTRY_PROMPT_TEMPLATE = """You are an AI trading-chart analysis system. \
Below is the latest market data for {symbol} on the M15 (15-minute) \
timeframe, plus a candlestick chart of the same data.

{payload}

ANALYSIS RULES -- score the setup against these three:
  1. CLEAR TREND: price is above BOTH EMAs (bullish) or below BOTH \
(bearish).
  2. PRICE ACTION: price has pulled back into the EMA zone, or is \
breaking out of the recent range.
  3. MOMENTUM: a reversal/continuation candle or volume supports the \
direction.

DECISION:
  * If AT LEAST 2 of the 3 rules are met, decide "LONG" or "SHORT".
  * If the market is too choppy, the signals conflict, or fewer than 2 \
rules are met, decide "WAIT".

LEVELS (only meaningful when the decision is LONG or SHORT):
  * entry: the current price, {price}.
  * sl: stop loss placed 1.5 to 2.0 x ATR away from entry. ATR is {atr}, \
so the stop distance must be between {sl_lo} and {sl_hi}. For LONG the \
stop goes BELOW entry; for SHORT it goes ABOVE.
  * tp: take profit giving a reward:risk of AT LEAST 1:{min_rr} -- i.e. \
the distance from entry to tp must be at least {min_rr} times the \
distance from entry to sl. For LONG the target is ABOVE entry; for \
SHORT it is BELOW.
  * reason: one or two sentences naming WHICH of the three rules were \
met and where you put the stop and target.

If the decision is "WAIT", still return numeric entry/sl/tp (they are \
ignored) -- for example entry {price} and any two nearby values.

Respond ONLY via the provided JSON schema."""


ENTRY_PROMPT_TEMPLATE_EXHAUSTION = """You are an AI trading-chart analysis \
system. Below is the latest market data for {symbol} on the M15 (15-minute) \
timeframe, plus a candlestick chart of the same data.

{payload}

At any point on a chart two different things can happen next, and your job \
is to say WHICH:
  A. CONTINUATION -- the move has room left and will extend.
  B. EXHAUSTION -- the move is stretched and will revert.

Count the evidence. Use the STRETCH figures given above.

EXHAUSTION evidence -- count how many of these four are TRUE:
  E1. |distance from EMA20| >= 1.5 ATR
  E2. position in range >= 0.85, or <= 0.15
  E3. consecutive same-direction closes >= 4
  E4. |net travel over the last 10 bars| >= 2.0 ATR

CONTINUATION evidence -- count how many of these four are TRUE:
  C1. |distance from EMA20| <= 0.75 ATR
  C2. position in range between 0.25 and 0.75
  C3. the H4 and H1 trends point the SAME way
  C4. the last 1-2 bars paused or pulled back against the move

DECISION -- apply this mechanically:
  * EXHAUSTION count >= 2 AND greater than the continuation count:
      trade AGAINST the run. If the run direction is UP -> "SHORT".
      If the run direction is DOWN -> "LONG".
  * CONTINUATION count >= 2 AND greater than the exhaustion count:
      trade WITH the move -- "LONG" if price is above both EMAs,
      "SHORT" if below both.
  * neither side reaches 2, or the two counts are EQUAL -> "WAIT".

"WAIT" is only for the case above. Do NOT answer "WAIT" merely because \
you feel uncertain, because both stories sound plausible, or because the \
setup is not perfect -- the counts decide, not your comfort level. A \
system that answers "WAIT" to everything is as useless as one that is \
always wrong.

Do NOT default to continuation just because the trend indicators agree. At \
a turning point they always agree. The higher-timeframe trend is context, \
not a veto: a counter-trend entry is allowed and expected when the \
exhaustion count wins.

LEVELS (only meaningful when the decision is LONG or SHORT):
  * entry: the current price, {price}.
  * sl: stop loss placed 1.5 to 2.0 x ATR away from entry. ATR is {atr}, \
so the stop distance must be between {sl_lo} and {sl_hi}. For LONG the \
stop goes BELOW entry; for SHORT it goes ABOVE.
  * tp: take profit giving a reward:risk of AT LEAST 1:{min_rr} -- i.e. \
the distance from entry to tp must be at least {min_rr} times the \
distance from entry to sl. For LONG the target is ABOVE entry; for \
SHORT it is BELOW.
  * reason: state the two counts explicitly, e.g. "E=3 (E1,E2,E4) vs C=1 \
-> exhaustion, run is up, so SHORT", then where you put the stop and \
target.

If the decision is "WAIT", still return numeric entry/sl/tp (they are \
ignored) -- for example entry {price} and any two nearby values.

Respond ONLY via the provided JSON schema."""

# Measurement switch, NOT a live setting. False means every live bot keeps
# the original trend-continuation prompt, so the running invert bot is
# unaffected even if the watchdog restarts it. _signal_accuracy.py sets this
# to True to A/B against the measured baseline. Only wire a CLI flag for it
# if the measurement actually beats that.
#
# [2026-08-15] First exhaustion run, 150 BTC samples:
#     gemini     -25.2 (p=0.004)  ->  +4.2 (p=0.597)
#     openai     -20.0 (p=0.007)  ->  0 directional calls
#     consensus  -35.9 (p=0.001)  ->  0 (cannot fire without openai)
# The reframing did what it was meant to: gemini's significant ANTI-edge
# disappeared. +4.2 at p=0.597 is not a positive edge, only the absence of
# a negative one -- but the negative one was the thing losing money.
#
# OpenAI, however, answered WAIT all 150 times with zero API errors. The
# first version asked it to "weigh both readings" and offered "genuinely
# unclear -> WAIT", which makes WAIT the safe answer to every chart. The
# baseline prompt drew 45 calls out of it precisely because it had a
# countable trigger ("at least 2 of 3 rules"), so the rewrite puts a
# countable trigger back: four numeric exhaustion tests, four continuation
# tests, higher count wins, WAIT only on a tie or when neither reaches 2.
EXHAUSTION_MODE = False


def build_exhaustion_context(candles: list, atr: float) -> dict:
    """The four numbers the original payload never supplied: how STRETCHED
    the move already is.

    Every input the models currently get -- price vs EMA20/EMA50, 5-bar
    momentum, H4/H1 trend -- measures trend DIRECTION, and the prompt then
    asks them to confirm continuation. At a local extreme all of those look
    their most bullish at the exact moment price is about to revert, which
    is what the live trades show: 16 of 29 never reached +0.1R and the
    median loser died in 2 M15 bars, i.e. entries landed on the turn.

    So the models were answering the question they were asked. These are
    the figures needed to ask a better one -- all pure functions of candles
    already in hand, no extra data source:

      stretch_atr : (price - EMA20) / ATR. How far price has run from its
                    own mean, in units of its own volatility.
      range_pos   : where price sits in the recent high-low range, 0..1.
                    0.98 means "at the very top", 0.5 means mid-range.
      run_bars    : consecutive same-direction closes ending now. A long
                    run is momentum AND an ageing move at the same time.
      travel_atr  : net distance covered over the last 10 bars, in ATR.
                    Separates a grind from a spike.
    """
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    price = closes[-1]
    ema20 = _ema(closes, 20)
    a = atr if (atr and math.isfinite(atr) and atr > 0) else 0.0

    look = min(20, len(candles))
    hi, lo = max(highs[-look:]), min(lows[-look:])
    rng = hi - lo
    # mid-range when the window is flat, so a zero range cannot read as an
    # extreme and manufacture a false exhaustion signal
    range_pos = (price - lo) / rng if rng > 0 else 0.5

    dirs = [1 if closes[i] > closes[i - 1] else -1 if closes[i] < closes[i - 1]
            else 0 for i in range(1, len(closes))]
    run_bars, run_dir = 0, "flat"
    if dirs and dirs[-1] != 0:
        run_dir = "up" if dirs[-1] > 0 else "down"
        for d in reversed(dirs):
            if d == dirs[-1]:
                run_bars += 1
            else:
                break

    n = min(10, len(closes) - 1)
    travel_atr = ((price - closes[-1 - n]) / a) if (a > 0 and n > 0) else 0.0

    return {
        "stretch_atr": ((price - ema20) / a) if a > 0 else 0.0,
        "range_pos": range_pos,
        "run_bars": run_bars,
        "run_dir": run_dir,
        "travel_atr": travel_atr,
    }


# Code-level entry gate derived from the 29 real trades (see
# _entry_conditions.py). Default OFF until it clears out-of-sample
# validation -- shipping a threshold fitted on the trades that suggested
# it is the exact mistake that put this bot live unvalidated.
STRETCH_GATE = False
STRETCH_MAX_ATR = 2.0        # |price - EMA20| in ATR
TRAVEL_MAX_ATR = 2.0         # |net move over 10 bars| in ATR
RUN_MIN, RUN_MAX = 2, 4      # consecutive same-direction closes, [min, max)


def entry_conditions_allow(ex: dict):
    """Block the market states that lost every single time in the live
    sample. Returns (allowed, reason).

    Rebuilding the market state at all 29 real entries and splitting by
    condition produced three bands with ZERO winners between them:

        |distance from EMA20| >= 2.0 ATR      0 wins / 7
        |net travel over 10 bars| >= 2.0 ATR  0 wins / 7
        fewer than 2 consecutive same-way closes   0 wins / 11
        4 or more consecutive same-way closes      0 wins / 5

    against an overall 4/29 = 14%. Applying all three would have kept
    every one of the four winners and removed 19 of the 25 losers -- 44%
    on what remains.

    The shape is coherent rather than three unrelated cuts. Both extremes
    fail: a market that has already run hard (large stretch, large travel,
    a long unbroken sequence) is done moving, and a market with no
    sequence at all has nothing to move. What survives is the middle --
    a modest, ongoing move with room left.

    ** That 44% is in-sample. The thresholds were read off the same 29
    trades they are scored on, only 9 survive the filter, and a rule fitted
    that tightly usually evaporates. Hence STRETCH_GATE defaults False.
    _stretch_gate_validate.py scores the same rule on thousands of
    historical bars; wire it live only if it holds there. **
    """
    s = abs(float(ex.get("stretch_atr", 0.0)))
    t = abs(float(ex.get("travel_atr", 0.0)))
    r = int(ex.get("run_bars", 0))
    if s >= STRETCH_MAX_ATR:
        return False, f"stretched {s:.2f}ATR from EMA20 (>= {STRETCH_MAX_ATR})"
    if t >= TRAVEL_MAX_ATR:
        return False, f"travelled {t:.2f}ATR in 10 bars (>= {TRAVEL_MAX_ATR})"
    if r < RUN_MIN:
        return False, f"no established run ({r} bars < {RUN_MIN})"
    if r >= RUN_MAX:
        return False, f"run already {r} bars (>= {RUN_MAX})"
    return True, "ok"


def _ema(values: list, span: int) -> float:
    k = 2.0 / (span + 1.0)
    prev = values[0]
    for v in values:
        prev = v * k + prev * (1 - k)
    return prev


def build_market_payload(candles: list, atr: float) -> dict:
    """The numeric context the models get alongside the chart image.

    This is the spec's key idea: hand the model the actual figures rather
    than making it read them off pixels, so "price is above EMA20" is a
    float comparison instead of an eyeball judgement. Pure function of the
    candle list -- unit-testable, no MT5 needed.

    candles: MT5 fetch_ohlcv format [[ts_ms, o, h, l, c, v], ...] ascending.
    """
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    price = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    look = min(20, len(candles))          # recent swing window
    recent_high = max(highs[-look:])
    recent_low = min(lows[-look:])
    # last few bars verbatim, so the momentum rule has something concrete
    tail = []
    for c in candles[-5:]:
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        v = float(c[5]) if len(c) > 5 else 0.0
        tail.append({"o": o, "h": h, "l": l, "c": cl, "v": v})
    return {
        "price": price, "ema20": ema20, "ema50": ema50, "atr": atr,
        "recent_high": recent_high, "recent_low": recent_low,
        "bars": len(candles), "tail": tail,
    }


def format_payload(symbol: str, p: dict) -> str:
    """Render the payload as the compact text block the spec describes."""
    price, ema20, ema50 = p["price"], p["ema20"], p["ema50"]
    trend = ("above BOTH EMAs (bullish structure)" if price > ema20 and price > ema50
             else "below BOTH EMAs (bearish structure)" if price < ema20 and price < ema50
             else "BETWEEN the EMAs (no clear trend)")
    lines = [
        f"Current Data ({symbol}, M15, {p['bars']} bars):",
        f"  Close={price:.2f}  EMA20={ema20:.2f}  EMA50={ema50:.2f}  ATR={p['atr']:.2f}",
        f"  Recent High={p['recent_high']:.2f}  Recent Low={p['recent_low']:.2f}",
        f"  Position vs EMAs: price is {trend}.",
        f"  Distance to EMA20 = {price - ema20:+.2f} "
        f"({(price - ema20) / p['atr']:+.2f} ATR), "
        f"to EMA50 = {price - ema50:+.2f} "
        f"({(price - ema50) / p['atr']:+.2f} ATR)",
        "  Last 5 bars (oldest first) O/H/L/C/V:",
    ]
    for b in p["tail"]:
        lines.append(f"    {b['o']:.2f} / {b['h']:.2f} / {b['l']:.2f} / "
                     f"{b['c']:.2f} / {b['v']:.0f}")

    ex = p.get("exhaustion")
    if ex:
        lines.append("  STRETCH (how far this move has already run):")
        lines.append(f"    distance from EMA20 = {ex['stretch_atr']:+.2f} ATR")
        lines.append(f"    position in the last-20-bar range = "
                     f"{ex['range_pos']:.2f}  (0.00 = at the low, "
                     f"1.00 = at the high)")
        lines.append(f"    consecutive {ex['run_dir']} closes = {ex['run_bars']}")
        lines.append(f"    net travel over the last 10 bars = "
                     f"{ex['travel_atr']:+.2f} ATR")

    htf = (p.get("htf") or {}).get("htf")
    mtf = (p.get("htf") or {}).get("mtf")
    if htf:
        lines.append(f"  HTF_Trend (H4): {htf['trend']} "
                     f"(close {htf['price']:.2f}, EMA20 {htf['ema20']:.2f}, "
                     f"EMA50 {htf['ema50']:.2f})")
        if ex:
            # The hard veto below would make a counter-trend call impossible
            # to express, so the exhaustion variant could never differ from
            # the baseline and the A/B would measure nothing.
            lines.append(f"    -> context only. A counter-trend entry is "
                         f"allowed here when the stretch figures argue for "
                         f"exhaustion.")
        else:
            lines.append(f"    -> you may only go LONG if this is BULLISH, "
                         f"or SHORT if this is BEARISH. If it is NEUTRAL, or "
                         f"points the other way, the answer is WAIT.")
    if mtf:
        lines.append(f"  MTF_Trend (H1, context only): {mtf['trend']}")

    kl = p.get("keylevels") or {}
    if kl.get("nearest"):
        d_atr = kl["distance"] / p["atr"] if p["atr"] else 0.0
        lv = kl["levels"]
        lines.append(f"  Key levels (from daily bars): prev_high "
                     f"{lv['prev_high']:.2f}  prev_low {lv['prev_low']:.2f}  "
                     f"pivot {lv['pivot']:.2f}  R1 {lv['r1']:.2f}  S1 {lv['s1']:.2f}")
        lines.append(f"    nearest = {kl['nearest']} @ {kl['nearest_price']:.2f}, "
                     f"{kl['distance']:.2f} away ({d_atr:.2f} ATR)")
        lines.append(f"    -> entries must be within {KEY_LEVEL_MAX_ATR} ATR of a "
                     f"key level. Do not enter in open space away from structure.")
    return "\n".join(lines)


def build_htf_context(htf_candles: list, mtf_candles: list) -> dict:
    """Higher-timeframe trend labels. Pure function of candle lists."""
    def label(cands):
        if not cands or len(cands) < 50:
            return None
        closes = [float(c[4]) for c in cands]
        e20, e50 = _ema(closes, 20), _ema(closes, 50)
        px = closes[-1]
        if px > e20 and e20 > e50:
            trend = "BULLISH"
        elif px < e20 and e20 < e50:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        return {"trend": trend, "price": px, "ema20": e20, "ema50": e50}
    return {"htf": label(htf_candles), "mtf": label(mtf_candles)}


def build_key_levels(daily_candles: list, price: float) -> dict:
    """Previous-day H/L/C plus standard floor-trader pivots, and the
    distance from `price` to whichever is nearest. Computed here rather
    than asked of the model: these are arithmetic, not judgement."""
    if not daily_candles or len(daily_candles) < 2:
        return {}
    prev = daily_candles[-2]           # last CLOSED day
    ph, pl, pc = float(prev[2]), float(prev[3]), float(prev[4])
    pp = (ph + pl + pc) / 3.0
    levels = {
        "prev_high": ph, "prev_low": pl, "prev_close": pc,
        "pivot": pp,
        "r1": 2 * pp - pl, "s1": 2 * pp - ph,
        "r2": pp + (ph - pl), "s2": pp - (ph - pl),
    }
    today = daily_candles[-1]
    levels["today_high"] = float(today[2])
    levels["today_low"] = float(today[3])
    nearest_name, nearest_dist = None, None
    for name, lv in levels.items():
        d = abs(price - lv)
        if nearest_dist is None or d < nearest_dist:
            nearest_name, nearest_dist = name, d
    return {"levels": levels, "nearest": nearest_name,
            "nearest_price": levels[nearest_name], "distance": nearest_dist}


def _fmt_prompt(symbol: str, bars: int, price: float, atr: float,
                payload_text: str) -> str:
    # Template chosen by a module switch rather than a new argument: adding
    # a parameter here would change the arity of every call path down to
    # gemini_chart_signal/openai_chart_signal, and a missed call site is
    # exactly the bug that left this bot inert for six hours twice.
    tpl = (ENTRY_PROMPT_TEMPLATE_EXHAUSTION if EXHAUSTION_MODE
           else ENTRY_PROMPT_TEMPLATE)
    return tpl.format(
        symbol=symbol, bars=bars, price=f"{price:.2f}", atr=f"{atr:.2f}",
        payload=payload_text,
        sl_lo=f"{SL_ATR_MIN * atr:.2f}", sl_hi=f"{SL_ATR_MAX * atr:.2f}",
        min_rr=MIN_RR)


def gemini_chart_signal(api_key: str, model: str, png: bytes, symbol: str,
                        price: float, bars: int, atr: float,
                        payload_text: str = "") -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr, payload_text)
    resp = client.models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CHART_SIGNAL_SCHEMA,
        ),
    )
    return json.loads(resp.text)


def openai_chart_signal(api_key: str, model: str, png: bytes, symbol: str,
                        price: float, bars: int, atr: float,
                        payload_text: str = "") -> dict:
    import base64
    from openai import OpenAI
    # reuse the strict-schema converter already built and tested for
    # news_gemini_bot.py rather than duplicating that subtle logic here.
    sys.path.insert(0, _BASE_DIR)
    from news_gemini_bot import _openai_strict_schema

    client = OpenAI(api_key=api_key)
    prompt = _fmt_prompt(symbol, bars, price, atr, payload_text)
    b64 = base64.b64encode(png).decode()
    resp = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            {"type": "input_text", "text": prompt},
        ]}],
        text={"format": {
            "type": "json_schema",
            "name": "chart_signal",
            "schema": _openai_strict_schema(CHART_SIGNAL_SCHEMA),
            "strict": True,
        }},
    )
    return json.loads(resp.output_text)


def cross_check_signal(gemini_result: dict, openai_result: dict,
                       price: float, atr: float) -> Optional[dict]:
    """Consensus + validation. Pure function -- no API, no MT5 -- so every
    path between an AI opinion and a live order is cheaply testable.

    Per the user's spec: trade only when BOTH models return the same
    LONG/SHORT decision, then average their entry/sl/tp. Returns None on
    any disagreement, any WAIT, or any validation failure.

    Absolute prices need more checking than the ATR multiples they
    replaced, because a price is not scale-free -- a stale or hallucinated
    number can be plausible-looking nonsense. Validated, in order:
      - both decisions are LONG or SHORT and identical
      - all three levels are finite and positive
      - sl/tp are on the CORRECT SIDES of entry for the direction (a
        LONG whose stop sits above entry is not a typo to fix, it is a
        model that lost the plot)
      - the averaged entry is within MAX_ENTRY_DRIFT_ATR of the real
        current price (catches a stale quote or a fabricated level)
      - the stop distance falls in the SL_ATR_MIN..MAX band the prompt
        asked for -- rejected, not clamped: silently "fixing" an
        out-of-band stop would trade a setup the model never proposed
      - reward:risk clears MIN_RR

    Returns distances as well as prices. The caller applies the DISTANCES
    to the real fill, which is what keeps the intended risk/reward intact
    if price moves between the decision and the fill.
    """
    g_dec = str(gemini_result.get("decision", "")).strip().upper()
    o_dec = str(openai_result.get("decision", "")).strip().upper()
    if g_dec not in ("LONG", "SHORT") or o_dec not in ("LONG", "SHORT"):
        return None
    if g_dec != o_dec:
        return None

    try:
        vals = [float(r[k]) for r in (gemini_result, openai_result)
                for k in ("entry", "sl", "tp")]
    except (TypeError, ValueError, KeyError):
        return None
    if any((not math.isfinite(v)) or v <= 0 for v in vals):
        return None
    if not math.isfinite(atr) or atr <= 0:
        return None

    entry = (float(gemini_result["entry"]) + float(openai_result["entry"])) / 2.0
    sl = (float(gemini_result["sl"]) + float(openai_result["sl"])) / 2.0
    tp = (float(gemini_result["tp"]) + float(openai_result["tp"])) / 2.0

    long_ = g_dec == "LONG"
    if long_ and not (sl < entry < tp):
        return None
    if (not long_) and not (tp < entry < sl):
        return None

    if abs(entry - price) > MAX_ENTRY_DRIFT_ATR * atr:
        return None

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist <= 0 or tp_dist <= 0:
        return None
    sl_mult = sl_dist / atr
    if not (SL_ATR_MIN <= sl_mult <= SL_ATR_MAX):
        return None
    rr = tp_dist / sl_dist
    if rr < MIN_RR:
        return None

    return {
        "signal": "long" if long_ else "short",
        "decision": g_dec,
        "entry": entry, "sl": sl, "tp": tp,
        "sl_dist": sl_dist, "tp_dist": tp_dist,
        "sl_atr_mult": sl_mult, "rr": rr,
        "gemini_reasoning": str(gemini_result.get("reason", "")),
        "openai_reasoning": str(openai_result.get("reason", "")),
    }


def invert_decision(decision: dict, tp_r: Optional[float] = None) -> dict:
    """Mirror a validated decision: opposite direction, stop distance kept,
    levels reflected across the entry. `tp_r` overrides the target, as a
    multiple of the STOP distance; None keeps the models' own target.

    [2026-08-15] Why the override exists. _loser_anatomy.py walked all 29
    real trades against M15 bars and recorded the most profit ever
    available before the 1R stop would have been touched. Two facts came
    out of it:

      - 16 of 29 trades never showed even +0.1R. Price went the wrong way
        from the first bar, so no exit rule could have saved them. Every
        one of 21 tested (symbol x target) combinations lost as traded.
      - inverted, 28 of 29 went into profit and 28 of 29 reached +0.5R.

    So the models locate the setup and size the stop well; the SIDE is
    backwards. But the target matters as much as the side, which is what
    the first invert attempt missed: on BTC, inverted at the models' usual
    1.5R the win rate is only 46.7% (EV +0.107R), while at 1.25R it is
    73.3% (EV +0.591R). Price reverses before reaching the far target.
    Hence 1.25R, and hence this override rather than a plain mirror.

    1.25R was NOT picked by fitting -- that is the point. Fitting the
    target on the same 29 trades that suggested the idea is circular, so
    _loser_anatomy.py also splits them chronologically and scores a flat
    1.25R, chosen a priori, on the later half alone:

        ALL      n=29  14/15 split   EV out +0.434R   WR 66.7%
        BTCUSDc  n=15   7/8  split   EV out +0.353R   WR 62.5%

    Out-of-sample EV lands at roughly half the in-sample figure -- the
    normal overfitting haircut -- but stays clearly positive rather than
    collapsing. That is a failure to refute, not a confirmation: the late
    halves are 15 and 8 trades from a single 3-day window.

    WHERE THE FLIP SITS, and why it matters: this runs AFTER consensus,
    validation, the HTF/key-level gates and the news veto -- i.e. it
    mirrors the pipeline's OUTPUT, exactly as _flip_test.py measured
    (that test mirrored logged orders, which had already passed every
    gate). Flipping earlier would feed a reversed direction into the HTF
    gate, which would then reject nearly everything, and would NOT be the
    thing the measurements were taken on.

    So the premise being traded is narrow and worth stating plainly: the
    models' ANALYSIS is treated as sound enough to locate a setup and size
    its stop, but its DIRECTION is taken as systematically backwards.

    ** Still a hypothesis under test. It has support on BTC from two
    independent measurements -- _signal_accuracy.py replayed 150 historical
    charts and found BTC consensus right 17.4% of the time against a 53.3%
    base rate (p=0.001), and these 15 live trades invert profitably -- but
    on GOLD the same replay found 50.0% against a 48.7% base (p=0.92),
    i.e. no information to invert. Trade BTC only until that disagreement
    is settled. And if the true cause is a direction bug in the pipeline,
    this cancels one error with another and breaks the moment the bug is
    fixed. Keep investigating the cause. **
    """
    out = dict(decision)
    entry = float(decision["entry"])
    sl_dist = float(decision["sl_dist"])
    tp_dist = float(decision["tp_dist"])
    if tp_r is not None:
        # _enter() sizes the real order from tp_dist, so tp_dist and rr must
        # be rewritten here too. Updating only the tp PRICE would leave a
        # stale distance behind and the broker would get the old target.
        tp_dist = float(tp_r) * sl_dist
        out["tp_dist"] = tp_dist
        out["rr"] = tp_dist / sl_dist if sl_dist > 0 else 0.0
        out["tp_r_override"] = float(tp_r)
    was_long = decision["signal"] == "long"
    now_long = not was_long
    out["signal"] = "long" if now_long else "short"
    out["decision"] = "LONG" if now_long else "SHORT"
    out["sl"] = entry - sl_dist if now_long else entry + sl_dist
    out["tp"] = entry + tp_dist if now_long else entry - tp_dist
    out["inverted_from"] = decision["decision"]
    return out


def entry_filters_allow(decision: dict, payload: dict, atr: float):
    """Hard entry gates: higher-timeframe alignment + proximity to a key
    level. Returns (allowed, reason). Pure function -- the whole point is
    that these are enforced by CODE, not left to the model's goodwill.

    FAILS CLOSED, unlike the news veto. The difference is deliberate: the
    news veto depends on a remote API that is down somewhere most days, so
    blocking on its absence would halt trading for reasons unrelated to
    the market. These two depend only on local MT5 candle reads, so
    missing data means something is genuinely wrong with our view of the
    market -- exactly when NOT to take a trade."""
    signal = decision.get("signal")
    entry = float(decision.get("entry", 0) or 0)

    if REQUIRE_HTF_ALIGNMENT:
        htf = (payload.get("htf") or {}).get("htf")
        if not htf:
            return False, "no HTF data (fail-closed)"
        trend = htf.get("trend")
        want = "BULLISH" if signal == "long" else "BEARISH"
        if trend != want:
            return False, f"HTF {trend} does not support {signal.upper()}"

    if REQUIRE_KEY_LEVEL:
        kl = payload.get("keylevels") or {}
        if not kl.get("nearest"):
            return False, "no key-level data (fail-closed)"
        if not (atr > 0 and math.isfinite(atr)):
            return False, "invalid ATR for key-level check"
        d_atr = abs(entry - kl["nearest_price"]) / atr
        if d_atr > KEY_LEVEL_MAX_ATR:
            return False, (f"entry {d_atr:.2f} ATR from nearest level "
                          f"{kl['nearest']} (max {KEY_LEVEL_MAX_ATR})")
    return True, "ok"


class ChartAITraderBot:
    def __init__(self, cfg: ForexConfig, risk_pct: float, poll_min: int,
                 max_per_symbol: int = MAX_POSITIONS_PER_SYMBOL,
                 max_total: int = MAX_TOTAL_POSITIONS,
                 max_consec_losses: int = MAX_CONSEC_LOSSES,
                 invert: bool = False):
        self.cfg = cfg
        self.risk_pct = risk_pct
        self.poll_min = poll_min
        # consecutive failed cycles per provider; in-memory, so a restart
        # re-alerts rather than staying quiet about an ongoing outage
        self.provider_fails: dict = {}
        self.max_per_symbol = max_per_symbol
        self.max_total = max_total
        self.max_consec_losses = max_consec_losses
        self.invert = invert

        self.stop_file = os.path.join(_BASE_DIR, "STOP_CHART_AI_TRADER")
        self.breaker_file = os.path.join(_BASE_DIR, "BREAKER_CHART_AI_TRADER")
        self.heartbeat_file = os.path.join(_BASE_DIR, "HEARTBEAT_CHART_AI_TRADER")
        self.state_file = os.path.join(_BASE_DIR, "chart_ai_trader_state.json")
        self.log_file = os.path.join(_BASE_DIR, "forex_bot_chart_ai_trader.log")

        self.log = self._setup_logging()
        self.connector = MT5Connector(cfg, self.log)
        self.executor = ForexOrderExecutor(self.connector, cfg, self.log)
        self._io = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mt5-io-chartai")
        self._mt5_timeout = 90.0

        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")
        # [2026-08-14] PRIMARY + FALLBACK, ordered. The lite model is
        # primary because it measured 5/5 available at ~6s while the
        # heavier one measured 3/5 at ~71s; the heavier one stays as the
        # fallback because it is presumably the better chart reader, and
        # a slow good answer beats no answer. Override either via env.
        # _dedupe matters in practice, not just in theory: the VPS .env
        # already pinned GEMINI_MODEL=gemini-flash-latest, so the first
        # deploy of this chain produced ['gemini-flash-latest',
        # 'gemini-flash-latest'] -- a "fallback" that retried the same
        # overloaded model and silently bought nothing. Order-preserving.
        def _dedupe(seq):
            out = []
            for x in seq:
                if x and x not in out:
                    out.append(x)
            return out

        self.gemini_models = _dedupe([
            os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"),
            os.environ.get("GEMINI_MODEL_FALLBACK", "gemini-flash-latest"),
        ])
        self.openai_models = _dedupe([
            os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            os.environ.get("OPENAI_MODEL_FALLBACK", ""),
        ])
        # kept for the news-veto scan, which takes a single model
        self.gemini_model = self.gemini_models[0]
        self.openai_model = self.openai_models[0]

        self.state = self._load_state()
        self.symbols: dict = {}
        self._news_cache: Optional[list] = None
        # per-cycle record of which providers actually answered, so a trade
        # entered while the veto was blind is identifiable AFTERWARDS -- see
        # _news_veto_state().
        self._news_ok: list = []
        self._news_failed: list = []

    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger("ChartAITraderBot")

    def _mt5(self, func, *args, **kw):
        fut = self._io.submit(func, *args, **kw)
        return fut.result(timeout=self._mt5_timeout)

    def _heartbeat(self):
        try:
            with open(self.heartbeat_file, "w", encoding="utf-8") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _telegram(self, msg: str):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            import urllib.request, urllib.parse
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:
            self.log.warning(f"telegram failed: {e}")

    def _load_state(self) -> dict:
        default = {"positions": {}, "consec_losses": 0, "last_poll": ""}
        if not os.path.exists(self.state_file):
            return default
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.log.error(f"[STATE] {self.state_file} exists but failed to "
                           f"load ({e}) -- starting from a BLANK state "
                           f"(tracked positions/loss-streak lost; broker "
                           f"positions will still be found on next check)")
            self._telegram(f"⚠️ chart_ai_trader: state file corrupted/unreadable "
                           f"({type(e).__name__}) -- resumed with blank state, "
                           f"please verify no position was orphaned")
            return default

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def _own_positions(self, bsym: Optional[str] = None) -> list:
        pos = [p for p in (self._mt5(self.connector.get_open_positions) or [])
              if p.get("magic") == MAGIC]
        if bsym:
            pos = [p for p in pos if p.get("symbol") == bsym]
        return pos

    def _atr14(self, bsym: str) -> Optional[float]:
        # must use the SAME timeframe the chart is drawn on -- an H1 ATR
        # is several times an M15 ATR, so mixing them would silently scale
        # every AI-chosen stop distance (and therefore every lot size).
        candles = self._mt5(self.connector.fetch_ohlcv, bsym, TIMEFRAME, 60)
        if not candles or len(candles) < 20:
            return None
        trs = []
        prev_close = None
        for c in candles:
            h, l, cl = float(c[2]), float(c[3]), float(c[4])
            tr = (h - l) if prev_close is None else max(
                h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
            prev_close = cl
        trs = trs[-14:]
        if len(trs) < 14 or any(not math.isfinite(t) for t in trs):
            return None
        return sum(trs) / len(trs)

    def _closed_position_net_pnl(self, position_id: str) -> Optional[float]:
        """None means 'unknown', never 0.0 -- same fail-safe reasoning as
        news_gemini_bot.py's identical method: a deal-history lookup
        failure must never look like a breakeven trade to the breaker."""
        try:
            deals = self._mt5(self.connector.get_position_deals, position_id, 1440)
        except Exception as e:
            self.log.warning(f"deal history lookup failed for {position_id}: {e}")
            return None
        if not deals:
            self.log.warning(f"deal history EMPTY for {position_id} -- "
                             f"cannot determine outcome")
            return None
        return sum(d.get("profit", 0.0) + d.get("swap", 0.0) + d.get("commission", 0.0)
                  for d in deals)

    def _watch_positions(self):
        known = self.state.get("positions", {})
        if not known:
            return
        live = {p["id"] for p in self._own_positions()}
        for k in list(known):
            info = known[k]
            if info.get("ticket") not in live:
                net = self._closed_position_net_pnl(info["ticket"])
                if net is None:
                    self.log.error(f"[BROKER-CLOSE] {k} closed but outcome "
                                   f"UNKNOWN (deal history unavailable) -- "
                                   f"loss-streak left UNCHANGED, not reset")
                    self._telegram(f"⚠️ chart_ai_trader: {k} closed at broker "
                                   f"but P&L could not be determined — breaker "
                                   f"streak NOT updated, please check manually")
                else:
                    self._update_loss_streak(net)
                    self.log.info(f"[CLOSED] {k} pnl={net:+.2f} -- {info}")
                    self._telegram(f"\U0001F534 CLOSED chart_ai_trader: {k} "
                                   f"entry={info.get('entry')} pnl={net:+.2f}")
                known.pop(k, None)
        self._save_state()

    def _update_loss_streak(self, net_pnl: float):
        if net_pnl < 0:
            self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1
        else:
            self.state["consec_losses"] = 0
        # measurement continues regardless of whether the breaker acts --
        # the streak length is one of the statistics being collected, and
        # it is only meaningful if it is allowed to run past 3.
        streak = self.state["consec_losses"]
        best = max(streak, int(self.state.get("worst_streak", 0)))
        self.state["worst_streak"] = best
        self.log.info(f"[STREAK] consecutive losses now {streak} "
                      f"(worst seen {best}); breaker "
                      f"{'OFF' if self.max_consec_losses <= 0 else 'at ' + str(self.max_consec_losses)}")
        if self.max_consec_losses > 0 and streak >= self.max_consec_losses:
            with open(self.breaker_file, "w", encoding="utf-8") as f:
                f.write(f"{self.state['consec_losses']} consecutive losses as of "
                       f"{datetime.now(timezone.utc).isoformat()}")
            self.log.error(f"[BREAKER] {self.state['consec_losses']} consecutive "
                           f"losses -- auto-stopping new entries")
            self._telegram(f"⛔ chart_ai_trader AUTO-STOPPED: "
                           f"{self.state['consec_losses']} consecutive losses. "
                           f"Delete {os.path.basename(self.breaker_file)} to resume "
                           f"after review.")

    def _news_candidates(self) -> list:
        """Union of both providers' recent news candidates, fetched at most
        once per poll cycle and only when something actually wants to
        trade. Returns [] if both providers fail -- see _news_allows for
        why that means "no veto" rather than "no trade"."""
        if self._news_cache is not None:
            return self._news_cache
        sys.path.insert(0, _BASE_DIR)
        from news_gemini_bot import gemini_scan, openai_scan
        out = []
        self._news_ok, self._news_failed = [], []
        for name, fn, key, model in (
                ("gemini", gemini_scan, self.gemini_key, self.gemini_model),
                ("openai", openai_scan, self.openai_key, self.openai_model)):
            if not key:
                continue
            try:
                cands = self._call_with_timeout(
                    fn, AI_CALL_TIMEOUT_SEC, key, model, NEWS_LOOKBACK_MIN) or []
                for c in cands:
                    c["_provider"] = name
                out.extend(cands)
                self._news_ok.append(name)
            except Exception as e:
                self._news_failed.append(name)
                self.log.warning(f"[NEWS/{name}] scan failed ({str(e)[:150]}) "
                                 f"-- this provider contributes no veto")
        self._news_cache = out
        self.log.info(f"[NEWS] {len(out)} candidate(s); veto={self._news_veto_state()} "
                      f"(ok={self._news_ok or '-'} failed={self._news_failed or '-'})")
        return out

    def _news_veto_state(self) -> str:
        """How much of the news veto was actually working for this cycle.

        [2026-08-13] Added because the fail-OPEN design is only defensible
        if a trade taken with a blind veto is IDENTIFIABLE later. Without
        this, reconstructing "was the veto up when this position opened?"
        means correlating separate log lines by timestamp across hundreds
        of forward-test trades. The state is stamped onto the [OPEN] line,
        the Telegram alert and the saved position record instead.
          ok        every configured provider answered
          degraded  at least one answered, at least one did not
          blind     none answered -- the trade passed BECAUSE the veto
                    could not run, not because news agreed with it
        """
        if not self._news_ok and not self._news_failed:
            return "not-run"
        if not self._news_ok:
            return "blind"
        if self._news_failed:
            return "degraded(-" + ",".join(self._news_failed) + ")"
        return "ok"

    def _news_allows(self, canon: str, signal: str) -> bool:
        """False = a high-impact story points the OTHER way, skip the trade.

        FAILS OPEN. If both scans error (503s from these providers are a
        near-daily event in this fleet's logs), there are no candidates, so
        nothing contradicts, so the trade proceeds on the chart consensus
        that already passed every gate. That is the deliberate choice: a
        veto stage that cannot be reached must not silently become a
        kill-switch on the whole strategy. The failure is logged per
        provider above so a persistently blind veto is visible."""
        opposite = "short" if signal == "long" else "long"
        for c in self._news_candidates():
            if c.get("symbol") != canon:
                continue
            if str(c.get("signal", "")).lower() != opposite:
                continue
            try:
                conf = float(c.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                continue
            if conf < NEWS_VETO_CONF_MIN:
                continue
            head = str(c.get("headline", ""))[:200]
            self.log.info(f"[NEWS VETO] {canon} {signal} cancelled by "
                          f"{c.get('_provider')}: {opposite} news conf={conf:.2f} "
                          f":: {head}")
            self._telegram(
                f"\U0001F6AB NEWS VETO {canon} {signal.upper()}\n"
                f"Chart said {signal.upper()}, but {c.get('_provider')} found "
                f"{opposite.upper()} news (conf {conf:.2f}):\n{head}")
            return False
        return True

    def _call_with_timeout(self, fn, timeout: float, *a, **kw):
        """Bound an external call's wall-clock time, same defence the rest of
        this fleet applies to MT5 calls (_call_with_timeout in
        forex_live_bot_gold_cwider.py). A hung thread cannot be killed in
        Python, so it leaks -- but control returns to the caller, which is
        the point: the loop keeps running, the heartbeat keeps ticking, and
        the watchdog does not restart a bot that is merely waiting."""
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            return ex.submit(fn, *a, **kw).result(timeout=timeout)
        finally:
            ex.shutdown(wait=False)

    def _safe_signal(self, name: str, fn, api_key: str, models: list,
                     png: bytes, symbol: str, price: float, bars: int,
                     atr: float, payload_text: str) -> Optional[dict]:
        # [2026-08-12 FIX] These were bare calls with NO timeout. Observed
        # live: the bot sat inside one for 7+ minutes with zero log output,
        # its heartbeat frozen the whole time. The google-genai / openai
        # clients set no default socket timeout, so a stalled connection
        # blocks the ONLY loop thread indefinitely. Two consequences, both
        # seen: the bot looks alive (process up) while doing nothing, and
        # the heartbeat goes past watchdog_h1.ps1's 5-minute staleness
        # threshold -- so the watchdog restarts it mid-cycle, forever.
        # Every MT5 call in this fleet has had this guard for months; the
        # AI calls were simply never given one.
        for i, model in enumerate(models):
            # heartbeat before EVERY attempt: this is what lets the timeout
            # be raised without the watchdog mistaking a slow provider for
            # a hung process. Largest heartbeat gap == one call, not one
            # symbol's worth of calls.
            self._heartbeat()
            try:
                r = self._call_with_timeout(
                    fn, AI_CALL_TIMEOUT_SEC, api_key, model, png, symbol,
                    price, bars, atr, payload_text)
                self._note_provider_ok(name)
                return r
            except Exception as e:
                role = "primary" if i == 0 else f"fallback {i}"
                more = " -- trying fallback" if i + 1 < len(models) else ""
                self.log.warning(f"[{name.upper()}] {model} ({role}) failed: "
                                 f"{str(e)[:160]}{more}")
        self.log.warning(f"[{name.upper()}] all {len(models)} model(s) failed "
                         f"-- skip this symbol this cycle")
        self._note_provider_failure(name, str(e)[:200] if models else "no models")
        return None

    # ── provider health ──────────────────────────────────────────────────
    # [2026-08-16] chart_ai sat unable to trade for ~12 hours when the
    # OpenAI credits ran out and said NOTHING. _safe_signal returned None
    # and the caller did a bare `return`, so the log looked like an ordinary
    # quiet cycle. The outage was only noticed because news_gemini_bot
    # happens to alert on the same failure -- had chart_ai been running
    # alone, the whole invert experiment would have been paused
    # indefinitely with no signal at all.
    #
    # A dual-consensus bot cannot trade without BOTH providers, so a dead
    # provider is a full outage, not a skipped cycle. It just does not look
    # like one from the outside.
    #
    # Not alerting on single failures: transient 503s reached ~40% at times
    # and one skipped cycle is genuinely routine. The alert fires only once
    # a provider has failed PROVIDER_FAIL_ALERT_CYCLES cycles in a row --
    # at a 15-minute poll that is an hour of not trading, which is worth
    # interrupting someone for. Recovery is announced too, so the channel
    # answers "is it back?" without anyone opening a log.
    def _note_provider_failure(self, name: str, detail: str):
        n = self.provider_fails.get(name, 0) + 1
        self.provider_fails[name] = n
        if n == PROVIDER_FAIL_ALERT_CYCLES:
            mins = n * self.poll_min
            self._telegram(
                f"\U0001F6D1 chart_ai_trader: {name} has failed {n} cycles in "
                f"a row (~{mins} min). Dual consensus needs BOTH providers, "
                f"so NO trades are being taken and the log will look normal."
                f"\n{detail}")
        elif n > PROVIDER_FAIL_ALERT_CYCLES and n % PROVIDER_FAIL_REMIND_CYCLES == 0:
            mins = n * self.poll_min
            self._telegram(f"\U0001F6D1 chart_ai_trader: {name} still down "
                           f"({n} cycles, ~{mins//60}h). Still no trades.")

    def _note_provider_ok(self, name: str):
        if self.provider_fails.get(name, 0) >= PROVIDER_FAIL_ALERT_CYCLES:
            self._telegram(f"✅ chart_ai_trader: {name} recovered after "
                           f"{self.provider_fails[name]} failed cycles — "
                           f"trading can resume.")
        self.provider_fails[name] = 0

    def _evaluate_symbol(self, canon: str, bsym: str):
        # [2026-08-12] Stacking allowed at the user's explicit request ("ถ้า
        # ai เห็นว่าควรเปิดก็เปิดหลายๆไม้ได้เรื่อยๆ"): this used to be a hard
        # "any open position -> skip". It is now a COUNT check instead,
        # because unbounded is not the same as unlimited -- at a 15-minute
        # cadence an AI that keeps answering "long" would open ~96
        # positions per symbol per day, and since each one risks
        # self.risk_pct at its own stop, a correlated move against a stack
        # that size is an account-ending event rather than a drawdown.
        # The caps below bound worst-case simultaneous risk to
        # max_total * risk_pct (see the startup banner, which prints it).
        held = self._own_positions(bsym)
        total = len(self._own_positions())
        if len(held) >= self.max_per_symbol:
            self.log.info(f"[{canon}] SKIP -- already holding {len(held)} "
                          f"position(s), per-symbol cap is {self.max_per_symbol}")
            return
        if total >= self.max_total:
            self.log.info(f"[{canon}] SKIP -- {total} open position(s) across "
                          f"all symbols, total cap is {self.max_total}")
            return

        candles = self._mt5(self.connector.fetch_ohlcv, bsym, TIMEFRAME, CHART_BARS)
        if not candles or len(candles) < 30:
            self.log.warning(f"[{canon}] only {len(candles) if candles else 0} "
                             f"bars -- skip (need >=30 for a usable chart)")
            return
        price = float(candles[-1][4])

        # ATR is computed BEFORE the AI call now: the model is told the
        # current ATR so its sl/tp multiples are anchored to the same unit
        # the bot will actually use to convert them into prices. Computing
        # it first also means a bad-ATR cycle costs zero API calls.
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{canon}] invalid ATR -- skip (no API call made)")
            return

        payload = build_market_payload(candles, atr)
        # extra context: higher timeframes and daily-derived key levels.
        # Local MT5 reads, so they cost no API tokens -- but a failure here
        # must not silently disable the gates that depend on them, hence
        # the explicit None handling in the gate block further down.
        try:
            htf_c = self._mt5(self.connector.fetch_ohlcv, bsym, HTF_TIMEFRAME, HTF_BARS)
            mtf_c = self._mt5(self.connector.fetch_ohlcv, bsym, MTF_TIMEFRAME, MTF_BARS)
            payload["htf"] = build_htf_context(htf_c, mtf_c)
        except Exception as e:
            self.log.warning(f"[{canon}] HTF fetch failed: {e}")
            payload["htf"] = {"htf": None, "mtf": None}
        try:
            daily_c = self._mt5(self.connector.fetch_ohlcv, bsym, "1d", 30)
            payload["keylevels"] = build_key_levels(daily_c, price)
        except Exception as e:
            self.log.warning(f"[{canon}] daily fetch failed: {e}")
            payload["keylevels"] = {}
        payload_text = format_payload(canon, payload)

        sys.path.insert(0, _BASE_DIR)
        from news_gemini_bot import render_chart_png
        try:
            png = render_chart_png(candles, canon, "")
        except Exception as e:
            self.log.warning(f"[{canon}] chart render failed: {e} -- skip")
            return

        gemini_result = self._safe_signal("gemini", gemini_chart_signal,
                                          self.gemini_key, self.gemini_models,
                                          png, canon, price, len(candles), atr,
                                          payload_text)
        if gemini_result is None:
            return
        if not self.openai_key:
            self.log.info(f"[{canon}] OPENAI not configured -- dual-consensus "
                          f"unavailable, no entry this cycle (fail-safe)")
            return
        openai_result = self._safe_signal("openai", openai_chart_signal,
                                          self.openai_key, self.openai_models,
                                          png, canon, price, len(candles), atr,
                                          payload_text)
        if openai_result is None:
            return

        self.log.info(
            f"[{canon}] gemini={gemini_result.get('decision')} "
            f"(e={gemini_result.get('entry')} sl={gemini_result.get('sl')} "
            f"tp={gemini_result.get('tp')})  "
            f"openai={openai_result.get('decision')} "
            f"(e={openai_result.get('entry')} sl={openai_result.get('sl')} "
            f"tp={openai_result.get('tp')})")
        decision = cross_check_signal(gemini_result, openai_result, price, atr)
        if decision is None:
            self.log.info(f"[{canon}] no consensus / failed validation this cycle")
            return

        self.log.info(f"[{canon}] CONSENSUS {decision['decision']} "
                      f"entry={decision['entry']:.2f} sl={decision['sl']:.2f} "
                      f"tp={decision['tp']:.2f} "
                      f"(sl={decision['sl_atr_mult']:.2f}xATR, R:R {decision['rr']:.2f})")
        ok, why = entry_filters_allow(decision, payload, atr)
        if not ok:
            self.log.info(f"[{canon}] SKIP entry-filter: {why}")
            return

        # heartbeat before the news stage: it can add up to two more timed
        # calls, and refreshing here keeps the largest gap between two
        # heartbeats at 2 x AI_CALL_TIMEOUT_SEC rather than 4 (see case 19).
        self._heartbeat()
        if not self._news_allows(canon, decision["signal"]):
            return

        if self.invert:
            decision = invert_decision(decision, tp_r=INVERT_TP_R)
            self.log.info(f"[{canon}] INVERTED -> {decision['decision']} "
                          f"sl={decision['sl']:.2f} tp={decision['tp']:.2f} "
                          f"rr={decision['rr']:.2f} "
                          f"(was {decision['inverted_from']})")
        self._enter(canon, bsym, decision)

    def _enter(self, canon: str, bsym: str, decision: dict) -> bool:
        signal = decision["signal"]
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{canon}] invalid ATR -- skip entry")
            return False

        eq = self._mt5(self.connector.get_equity)
        bid, ask = self._mt5(self.connector.get_current_price, bsym)
        if bid <= 0 or ask <= 0:
            self.log.warning(f"[{canon}] invalid price -- skip entry")
            return False
        long_ = signal == "long"
        px = ask if long_ else bid

        # cost gate: refuse to pay more than MAX_SPREAD_R of the risk just
        # to get in and out. Uses the LIVE quote, so a temporary blowout is
        # caught even on a normally-cheap symbol.
        spread = abs(ask - bid)
        sd_check = float(decision["sl_dist"])
        if sd_check > 0:
            spread_r = spread / sd_check
            if spread_r > MAX_SPREAD_R:
                self.log.info(f"[{canon}] SKIP -- spread {spread:.4f} is "
                              f"{spread_r:.3f}R of the {sd_check:.4f} stop, "
                              f"over the {MAX_SPREAD_R}R ceiling")
                return False
        # Apply the AI's DISTANCES to the real execution price, not its
        # stated absolute levels. Price can move between the decision and
        # the fill; anchoring to the fill preserves the intended stop
        # distance (and therefore the risk sizing below) and the intended
        # reward:risk, instead of silently widening or tightening both.
        sd = float(decision["sl_dist"])
        td = float(decision["tp_dist"])
        sl = px - sd if long_ else px + sd
        tp = px + td if long_ else px - td

        pip_size = self.cfg.get_pip_size(bsym)
        pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
        sd_pips = sd / pip_size
        if sd_pips <= 0 or pip_value <= 0:
            self.log.warning(f"[{canon}] invalid pip_value -- skip entry")
            return False
        risk_cash = eq * self.risk_pct / 100.0
        lot = round(risk_cash / (sd_pips * pip_value), 2)
        if lot < 0.01:
            self.log.warning(f"[{canon}] lot rounds to 0 -- skip entry")
            return False
        actual_risk_pct = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")
        if actual_risk_pct > self.risk_pct * 1.5:
            self.log.error(f"[{canon}] SIZING SANITY CHECK FAILED: lot={lot} "
                           f"implies {actual_risk_pct:.2f}% > 1.5x intended "
                           f"{self.risk_pct}% -- REFUSING to open")
            self._telegram(f"⛔ chart_ai_trader: {canon} sizing sanity check "
                           f"failed — entry refused")
            return False

        side = "long" if long_ else "short"
        result = self._mt5(self.executor.open_position, bsym, side, lot,
                          sl, tp, "CHARTAI-" + side[:4].upper())
        if not result:
            self.log.error(f"[{canon}] open failed")
            self._telegram(f"⚠️ chart_ai_trader: {canon} order failed to open")
            return False

        fill = float(result.get("fill_price", px) or px)
        self.state.setdefault("positions", {})[f"{canon}-{result.get('trade_id')}"] = {
            "bsym": bsym, "ticket": str(result.get("trade_id", "") or ""),
            "side": side, "entry": fill, "sl": sl, "tp": tp, "lot": lot,
            "opened_utc": datetime.now(timezone.utc).isoformat(),
            "news_veto": self._news_veto_state(),
        }
        self._save_state()

        veto_state = self._news_veto_state()
        self.log.info(f"[OPEN] {side.upper()} {bsym} lot={lot} fill={fill:.2f} "
                      f"sl={sl:.2f} tp={tp:.2f} (risk={actual_risk_pct:.2f}%) "
                      f"news_veto={veto_state}")
        if veto_state in ("blind", "not-run"):
            self.log.warning(f"[OPEN] {bsym} entered with news veto {veto_state.upper()} "
                             f"-- no news check protected this trade (fail-open)")
        self._telegram(
            f"\U0001F7E2 CHART-AI ENTRY {side.upper()} {bsym}\n"
            f"lot={lot}  fill={fill:.2f}  SL={sl:.2f} TP={tp:.2f}\n"
            f"AI-chosen: SL {decision['sl_atr_mult']:.2f}xATR  "
            f"R:R {decision['rr']:.2f}\n"
            f"news veto: {veto_state}"
            + ("\n\U0001F501 INVERTED (models said "
               f"{decision['inverted_from']})" if decision.get('inverted_from') else "")
            + "\n"
            f"gemini: {decision['gemini_reasoning'][:250]}\n"
            f"openai: {decision['openai_reasoning'][:250]}")
        return True

    def run(self):
        if not self._mt5(self.connector.connect):
            self.log.error("MT5 connect failed")
            sys.exit(1)

        info = self.connector.get_account_info()
        is_demo = self.connector.is_demo()
        if not self.cfg.dry_run and not is_demo and not self.cfg.allow_real:
            self.log.error(
                "REFUSING TO START: account is NOT confirmed DEMO "
                f"(type={info.get('type', 'UNKNOWN')}) and --allow-real was "
                "not passed. Use --dry-run to test, or --allow-real to "
                "confirm you intend to trade this real account.")
            sys.exit(1)
        acct_tag = ("DEMO" if is_demo else
                   ("LIVE (--allow-real)" if self.cfg.allow_real else "UNKNOWN"))

        for canon in SYMBOLS:
            self.symbols[canon] = self.connector.resolve_symbol(canon)
        eq = self._mt5(self.connector.get_equity)
        self.log.info("=" * 70)
        self.log.info(f"  CHART AI TRADER (dual-provider consensus)  magic={MAGIC}  "
                      f"account={acct_tag}  symbols={self.symbols}")
        self.log.info(f"  equity={eq:.2f}  risk/trade={self.risk_pct}%  "
                      f"poll={self.poll_min}min  timeframe={TIMEFRAME}  "
                      f"rule=2-of-3")
        self.log.info(f"  SL/TP: chosen per-setup by the AI as absolute "
                      f"prices; stop must land in {SL_ATR_MIN}-{SL_ATR_MAX}"
                      f"xATR and R:R >= {MIN_RR}, else the setup is REJECTED "
                      f"(entry drift limit {MAX_ENTRY_DRIFT_ATR}xATR)")
        self.log.info(f"  spread ceiling: skip if spread > {MAX_SPREAD_R}R of "
                      f"the stop (measured: gold 0.020R, BTC 0.068R, ETH 0.093R)")
        self.log.info(f"  entry filters: HTF alignment ({HTF_TIMEFRAME}) "
                      f"{'ON' if REQUIRE_HTF_ALIGNMENT else 'off'}, key-level "
                      f"proximity <= {KEY_LEVEL_MAX_ATR}xATR "
                      f"{'ON' if REQUIRE_KEY_LEVEL else 'off'} -- both fail CLOSED")
        self.log.info(f"  news veto: ON -- a chart setup is cancelled if either "
                      f"provider surfaces opposite-direction news at conf >= "
                      f"{NEWS_VETO_CONF_MIN} (lookback {NEWS_LOOKBACK_MIN}min, "
                      f"fetched lazily, fails OPEN)")
        self.log.info(f"  stacking: up to {self.max_per_symbol}/symbol, "
                      f"{self.max_total} total -> worst-case simultaneous "
                      f"risk {self.max_total * self.risk_pct:.2f}% of equity "
                      f"if every stop hits at once")
        self.log.info(f"  gemini models={self.gemini_models} (primary first, "
                      f"fallback on failure)  timeout={AI_CALL_TIMEOUT_SEC}s/call")
        openai_status = ("configured" if self.openai_key else
                        "NOT SET -- dual-consensus unavailable, bot will idle "
                        "(no new entries) until OPENAI_API_KEY is added to .env")
        self.log.info(f"  openai models={self.openai_models}  {openai_status}")
        if self.invert:
            self.log.warning(
                f"  *** INVERT MODE: every order is the OPPOSITE of what the "
                f"models decided. Stop distance kept; TARGET REPLACED with "
                f"{INVERT_TP_R}xSL (the models' 1.5R wins only 46.7% once "
                f"flipped, vs 73.3% at 1.25R). Basis: 29 real trades -- 16 "
                f"never showed +0.1R as traded, 28 of 29 did inverted; flat "
                f"1.25R scored EV +0.353R out-of-sample on BTC. HYPOTHESIS "
                f"UNDER TEST -- late half is 8 trades from one 3-day window, "
                f"and a fixed upstream direction bug would kill it. ***")
        if self.max_consec_losses <= 0:
            self.log.warning("  consecutive-loss breaker: OFF -- the bot will NOT "
                             "auto-stop after losing streaks (data-collection "
                             "mode, by explicit user decision). Remaining "
                             "protection: per-trade SL, position caps, kill-switch.")
        else:
            self.log.info(f"  consecutive-loss breaker: stop after "
                          f"{self.max_consec_losses} losses")
        self.log.info(f"  kill-switch: {os.path.basename(self.stop_file)}  "
                      f"breaker: {os.path.basename(self.breaker_file)}")
        self.log.info("  ** UNVALIDATED STRATEGY -- no historical backtest exists "
                      "for this signal source. Live by explicit user decision. **")
        self.log.info("=" * 70)
        self._telegram(f"\U0001F680 START chart_ai_trader  equity={eq:.2f}  "
                       f"risk={self.risk_pct}%/trade  poll={self.poll_min}min  "
                       f"dual-consensus={'ON' if self.openai_key else 'OFF (idling)'}")

        while True:
            try:
                self._heartbeat()
                self._watch_positions()
                now = datetime.now(timezone.utc)

                last = self.state.get("last_poll", "")
                elapsed_min = (999999.0 if not last else
                              (now - datetime.fromisoformat(last)).total_seconds() / 60.0)
                due = elapsed_min >= self.poll_min
                if due:
                    if os.path.exists(self.stop_file):
                        self.log.info("[KILL-SWITCH] present -- skipping poll cycle")
                    elif os.path.exists(self.breaker_file):
                        self.log.warning("[BREAKER] consecutive-loss breaker active "
                                         "-- skipping poll cycle (clear file to resume)")
                    else:
                        # refetch at most once per cycle; clear the health
                        # record too so a stale "ok" can't leak into a later
                        # cycle whose scan actually failed.
                        self._news_cache = None
                        self._news_ok, self._news_failed = [], []
                        for canon, bsym in self.symbols.items():
                            # [2026-08-12] refresh the heartbeat BETWEEN
                            # symbols, not just once per loop iteration: a
                            # poll cycle makes 6 network calls and can run
                            # for minutes, which previously let the
                            # heartbeat go stale enough for the watchdog to
                            # restart a perfectly healthy, merely-busy bot.
                            self._heartbeat()
                            try:
                                self._evaluate_symbol(canon, bsym)
                            except Exception as e:
                                self.log.error(f"[{canon}] evaluate error: {e}")
                        self.state["last_poll"] = now.isoformat()
                        self._save_state()
            except Exception as e:
                self.log.error(f"loop error: {e}")
            time.sleep(60)


def build_cfg(dry_run: bool, allow_real: bool) -> ForexConfig:
    """Build the live config. Factored out of main() purely so a test can
    assert on it -- see the magic-number note below.

    [2026-08-12 FIX] cfg.magic_number was NEVER SET here. ForexConfig
    defaults it to 20240101, so every order this bot placed went out under
    that shared default instead of MAGIC=671001, while _own_positions()
    filtered incoming positions on `magic == MAGIC`. The bot therefore
    could not see its own trades. Two consequences, both observed live on
    the very first real trade (XAUUSDc long 0.04 @ 4413.988, ticket
    4132289820):
      - _watch_positions() found the ticket missing from its own
        (magic-filtered, hence empty) view and declared it CLOSED with
        pnl=+0.00 less than two minutes after opening, then stopped
        tracking it -- while the position was in fact still open at the
        broker, with only an entry deal in history.
      - far worse, the "already-positioned" guard in _evaluate_symbol()
        also reads _own_positions(), so it would have kept returning
        empty: the bot could have opened an unbounded stack of duplicate
        positions on the same symbol, each sized as if it were the first.
    Every other bot in this fleet wires this (forex_live_bot_gold_cwider.py,
    news_gemini_bot.py, daily_sleeves_bot.py); this one simply never did.
    """
    cfg = ForexConfig()
    cfg.dry_run = dry_run
    cfg.allow_real = allow_real
    cfg.magic_number = MAGIC
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PCT,
                    help=f"%% equity risked per trade (default {DEFAULT_RISK_PCT})")
    ap.add_argument("--poll-min", type=int, default=POLL_MIN,
                    help=f"minutes between chart re-checks (default {POLL_MIN})")
    ap.add_argument("--max-per-symbol", type=int, default=MAX_POSITIONS_PER_SYMBOL,
                    help=f"max simultaneous positions on ONE symbol "
                         f"(default {MAX_POSITIONS_PER_SYMBOL})")
    ap.add_argument("--max-total", type=int, default=MAX_TOTAL_POSITIONS,
                    help=f"max simultaneous positions across ALL symbols "
                         f"(default {MAX_TOTAL_POSITIONS}). Worst-case "
                         f"simultaneous risk is this x --risk, since every "
                         f"position carries its own stop sized to --risk.")
    ap.add_argument("--max-consec-losses", type=int, default=MAX_CONSEC_LOSSES,
                    help=f"auto-stop after this many consecutive losses "
                         f"(default {MAX_CONSEC_LOSSES}; 0 = never stop, which "
                         f"is the setting for collecting an uncensored sample)")
    ap.add_argument("--symbols", default=",".join(SYMBOLS),
                    help=f"comma-separated symbols to trade (default and only "
                         f"permitted set: {','.join(SYMBOLS)}). ETHUSDC was "
                         f"removed entirely on 2026-08-15 and is NOT "
                         f"restorable here -- see the SYMBOLS comment.")
    ap.add_argument("--invert", action="store_true",
                    help="trade the OPPOSITE of the models' direction, keeping "
                         "their stop/target distances. Hypothesis under test "
                         "(see invert_decision docstring); not a validated edge.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (add it to .env)")
        sys.exit(1)
    if not os.environ.get("OPENAI_API_KEY"):
        print("[WARN] OPENAI_API_KEY not set -- dual-consensus unavailable, "
             "bot will idle (no new entries) until it's added")

    cfg = build_cfg(args.dry_run, args.allow_real)
    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] live mode needs MT5 (Windows) -- use --dry-run to test elsewhere")
        sys.exit(1)

    want = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    unknown = [x for x in want if x not in ALL_SYMBOLS]
    if unknown:
        print(f"[ERROR] unknown symbol(s): {unknown}. "
             f"Known: {list(ALL_SYMBOLS)}")
        sys.exit(1)
    SYMBOLS.clear()
    SYMBOLS.update({k: ALL_SYMBOLS[k] for k in want})

    bot = ChartAITraderBot(cfg, risk_pct=args.risk, poll_min=args.poll_min,
                          max_per_symbol=args.max_per_symbol,
                          max_total=args.max_total,
                          max_consec_losses=args.max_consec_losses,
                          invert=args.invert)
    bot.run()


if __name__ == "__main__":
    main()
