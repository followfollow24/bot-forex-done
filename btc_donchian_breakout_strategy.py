#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
btc_donchian_breakout_strategy.py -- LIVE strategy class for the BTC H1
Donchian-channel breakout bot (variant tag: btc_h1_breakout, run alongside
the existing btc_h1_manual pullback bot as a second, structurally different
BTC signal -- selected via --strategy donchian --timeframe 1h).

WHY THIS EXISTS: btc_h1_manual (EMA20 pullback inside an H4 trend) trades
~0.35x/day. Looking for ways to add trading frequency without destroying
edge, this reuses the SAME Donchian-breakout idea already validated for
gold_daily_breakout_strategy.py (that class's mechanics are entirely
generic -- a rolling channel + ATR margin filter, no gold-specific logic
at all) but on H1 bars instead of Daily, with its own frozen parameters.

VALIDATION SUMMARY (2026-07-31, BTCUSDc H1, spread=$10, real costs):
  - OOS (2nd half, params frozen on 1st half): PF=1.17, Sharpe=0.87,
    CAGR=+11.54%, DD=19.2%, ~0.31 trades/day.
  - Full-history (8.9y): PF=1.13, Sharpe=0.62, CAGR=+7.22%, DD=21.3%,
    7/10 calendar years profitable (2017/2021/2026 negative, -3.6% to
    -15.6% -- modest, not devastating).
  - Parameter sensitivity: PF 1.06-1.19 and Sharpe 0.36-0.80 across the
    ENTIRE win=70-100 x margin=0.15-0.35 grid -- a robust plateau, not a
    lucky single point.
  - Correlation to the existing btc_h1_manual pullback signal (monthly
    returns): 0.29 -- moderately diversified, not a duplicate. Combined
    equal-weight: Sharpe 1.14 (pullback alone 1.16), DD 15.2% (vs 20.0%/
    17.0% individually) -- real DD reduction, modest CAGR dilution
    (17.77% pullback-alone -> 12.97% combined) since averaging a stronger
    and a weaker return stream.
  - ETH does NOT show this edge at any window tested (PF<=1.08 at every
    setting) -- this strategy is BTC-only, do not reuse for ETH without
    fresh validation.

Entry: close beyond an 80-bar (H1) high/low Donchian channel by more than
0.25xATR. Exit: SL=2.0xATR, ATR trailing stop (3.0xATR, activates at
1.0xATR profit) -- same mechanics as GoldDailyDonchianBreakout, just a
different DONCH_WIN/BREAKOUT_MARGIN_ATR and a much shorter entry-bar
duration (H1 instead of Daily), which the shared bucket-free rolling-window
implementation handles unchanged (no resampling step exists in this class
at all, so there's nothing timeframe-specific to get wrong).
"""
from __future__ import annotations

from gold_daily_breakout_strategy import GoldDailyDonchianBreakout


class BTCH1DonchianBreakout(GoldDailyDonchianBreakout):
    """H1 Donchian-channel breakout for BTCUSDc. See module docstring for
    the full validation summary and why this exists."""

    name = "BTC H1 Donchian Breakout"
    short_name = "BTCH1BO"

    DONCH_WIN = 80
    BREAKOUT_MARGIN_ATR = 0.25
