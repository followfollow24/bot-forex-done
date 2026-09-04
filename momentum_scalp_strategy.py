#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momentum_scalp_strategy.py -- LIVE strategy class that reproduces the
operator's own discretionary scalp (variant tag: manual_copy).

>>> VALIDATION STATUS: NOT BACKTESTED.  Derived from 29 live trades and  <<<
>>> deployed by explicit user decision. See "WHAT IS AND IS NOT KNOWN".  <<<

WHERE THIS CAME FROM
----------------------------------------------------------------------
An account-wide exit audit found the operator's hand-placed trades were
the only consistently profitable thing on the account:

    29 trades   WR 58%   PF 1.84   net +216.13 USD
    avg win  +31.58  held 0.4h        avg loss -23.42  held 0.2h

against every bot on the same account losing money. Asked what they look
at when entering, the answer was, verbatim: "look at the chart -- if it
is moving up at that moment I go long, if it is moving down I go short."

The exits were then measured from the filled brackets themselves
(_manual_geometry.py, run against the broker's own deal history):

    TP  n=18   median 13.98 price   = 0.51 x ATR(H1)
    SL  n=10   median  9.80 price   = 0.42 x ATR(H1)
    implied R:R 1.43 : 1     ATR(H1) at entry: median 23.06
    exits: TP 18, SL 10, manual 1
    long 16 trades won 11 (69%)   short 13 trades won 7 (54%)
    symbols: XAUAUDm x26, BTCUSDm x3
    entry hours (UTC): clustered 11-15

So this class supplies ONLY the entry: momentum direction over a short
lookback. The brackets are passed on the command line (--sl-atr /
--tp-atr) exactly as they are for every other strategy here, because
their correct values depend on the timeframe the bot is run on -- see
"CALIBRATION" below, this is the easiest thing to get wrong.

WHAT IS AND IS NOT KNOWN
----------------------------------------------------------------------
KNOWN (measured from the broker's deal history, not self-reported):
  - the exit geometry above, and that it was profitable over 29 trades
  - the entries were NOT trend-following: 12 of 26 were against the
    H4 EMA50/200 trend, and those still won 50% of the time. Whatever
    edge exists here, it is not the trend filter the other bots use.

NOT KNOWN -- and this is the part that matters:
  - whether 29 trades means anything. At R:R 1.43 break-even is 41% WR;
    observing 58% on n=29 happens by chance roughly one time in eight.
  - what "moving up" meant to a human eye. This class picks ONE
    operationalisation (net move over N bars, filtered by a minimum
    size). It is a guess at the intent, not a measurement of it.
  - whether it survives costs. The stop is 0.42xATR, which is small
    enough that spread is a large fraction of R -- the regime where a
    scalp usually dies.
  - 33% of the observed profit came from a single trade (+71.11).

_copytrade_backtest.py exists to answer the first and third of those on
years of bars. At the time this was written it could not run: the MT5
terminal had no M5 history cached for XAUAUDm and would not download it
on API request alone. THIS CLASS HAS THEREFORE NEVER BEEN TESTED ON
ANYTHING EXCEPT THE 29 TRADES THAT INSPIRED IT.

CALIBRATION -- read before setting --sl-atr / --tp-atr
----------------------------------------------------------------------
The 0.51 / 0.42 multipliers above are expressed against ATR(H1), because
that is the unit the operator's trades were measured in. The live bot
computes ATR on ITS OWN entry timeframe. Run this on M5 and pass
--sl-atr 0.42 and you get a stop 3-4x tighter than the operator's, since
ATR(M5) is roughly ATR(H1)/sqrt(12).

So the multipliers must be rescaled for whatever --timeframe is used:

    on H1 : --sl-atr 0.42  --tp-atr 0.51      (as measured)
    on M5 : multiply both by ATR(H1)/ATR(M5), measured on the real
            symbol -- do NOT assume the sqrt(12) approximation

The operator's median hold was 12-24 minutes, which argues for M5 or M15
entries rather than H1. That makes the rescaling mandatory, not optional.

POSITION SIZE
----------------------------------------------------------------------
The operator's own trades were 0.01-0.05 lot and they chose 0.03-0.05
for this bot. On the current account (equity ~54 USD, XAUAUDm at 0.72
USD per 1.0 price move per 0.01 lot) a 0.42xATR stop at 0.03 lot risks
roughly 20 USD -- about 39% of the account per trade. That is the
operator's explicit, informed decision, recorded here so the number is
never a surprise: two consecutive stops is most of the account.
"""
from __future__ import annotations

import numpy as np

from forex_indicators import Signal


class MomentumScalp:
    """Enter in the direction price is currently moving. Nothing else.

    Deliberately has no trend filter, no oscillator and no session gate:
    the trades this copies were 46% against the H4 trend and still won,
    so adding a trend filter would be adding a rule the source data
    actively argues against. Any extra condition should be earned by a
    measurement, not assumed.
    """

    name = "Momentum Scalp (copies the operator's manual entries)"
    short_name = "MomScalp"

    # --- entry ---
    LOOKBACK = 3          # bars of net movement that define "it is moving"
    MIN_MOVE_ATR = 0.15   # ignore drift below this: without it, every bar
                          # is a signal and the bot trades pure noise into
                          # the spread. 0.15 is a starting guess, NOT a
                          # measured value -- it is the first thing to
                          # sweep once a backtest can run.

    # --- exits: overridden from the CLI (--sl-atr / --tp-atr) ---
    # Defaults are the operator's measured H1 multipliers, so an
    # un-calibrated run on H1 behaves as observed rather than wildly off.
    sl_atr = 0.42
    tp_atr = 0.51
    trail_atr_mult = 999.0        # off: the source trades used flat brackets
    trail_activation_atr = 999.0
    max_spread_atr_ratio = 1.0

    MIN_BARS = 50

    _built_len = None

    def precompute(self, d: dict):
        # No indicators to build -- direction is read straight off closes.
        # Kept so the class matches the interface every other strategy
        # here implements (the live bot calls it on buffer growth).
        self._built_len = len(d["c"])

    def _ensure(self, d: dict):
        if self._built_len != len(d["c"]):
            self.precompute(d)

    def signal(self, d: dict, i: int) -> Signal:
        if i < self.MIN_BARS or i < self.LOOKBACK:
            return Signal()
        self._ensure(d)

        atr = d["atr"][i]
        if atr is None or np.isnan(atr) or atr <= 0:
            return Signal()

        c = d["c"]
        # net displacement over the lookback, measured on CLOSED bars only
        # (i is the last closed bar -- the live bot never calls this on a
        # forming one, same contract as every other strategy in this repo)
        move = float(c[i]) - float(c[i - self.LOOKBACK])
        thresh = self.MIN_MOVE_ATR * atr
        if abs(move) < thresh:
            return Signal()

        n_atr = move / atr
        if move > 0:
            return Signal("BUY", f"mom +{n_atr:.2f}xATR over {self.LOOKBACK} bars")
        return Signal("SELL", f"mom {n_atr:.2f}xATR over {self.LOOKBACK} bars")
