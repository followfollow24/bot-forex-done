#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
momentum_scalp_strategy.py -- LIVE strategy class that reproduces the
operator's own discretionary scalp (variant tag: manual_copy).

>>> VALIDATION STATUS: BACKTESTED 2026-09-04 -- NO EDGE FOUND.          <<<
>>> Do not run this on money. See "THE TEST THAT WAS PENDING" below.    <<<

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

THE TEST THAT WAS PENDING -- IT HAS NOW RUN
----------------------------------------------------------------------
[2026-09-04] The history problem above was my own error, not a broker
limitation: copy_rates_range was being asked for >100,000 bars, which
MT5 refuses with "Terminal: Invalid params" and an EMPTY array -- and I
read that empty array as "no history". Chunked, the bars were there all
along.

Two things were then measured.

_entry_fingerprint.py compared the 29 entry bars against 4,000 bars the
operator passed over. Their rule is real and specific:

    momentum at entry   1.418 xATR   vs   0.879   d=0.76  p<0.001
    extension (EMA20)   1.675 xATR   vs   1.059   d=0.74  p<0.001
    direction matched the recent move on 25/29 = 86%

which also showed MIN_MOVE_ATR = 0.15 below is roughly 10x too low --
it fires on nearly every bar, which is not what they do.

_copytrade_calibrated.py then swept the threshold from 0.15 to 2.00,
with and without the extension filter, on 101,415 M5 XAUAUDm bars
(2025-04-01..2026-09-04), train/TEST split, each cell controlled against
20 random-direction runs on the SAME filtered bars:

    ALL 32 CELLS NEGATIVE. Largest |z| was 1.94 and it was NEGATIVE
    (signal worse than the coin flip). Nothing reached 2 sigma and no
    cell held its sign across the two halves.
    At the operator's own thresholds (mom>=1.4, ext>=1.6):
        train  n=91   WR 41.8%   EV -0.235 R   z -0.48
        TEST   n=123  WR 43.1%   EV -0.161 R   z +0.27

WHY, precisely: break-even WR at 0.51/0.42 is 45.2% before costs, and
the measured WR is 42-45% -- a coin flip. Spread then takes
1.14 / (0.42 * 23.06) = 0.118 R per trade. The random control loses the
same amount, and that is the whole finding: the loss is COST, not a bad
direction call. Widening the stop to 2.5 xATR would cut the cost to
0.020 R, but there is no edge underneath for the cheaper geometry to
protect.

The +216 USD over 29 trades is consistent with luck -- 58% on n=29 at
this R:R happens about one time in eight -- and 33% of it was a single
+71.11 trade. Nothing separated their winning entries from their losing
ones (every feature p > 0.12).

THIS CLASS IS KEPT AS THE RECORD OF A TESTED AND REJECTED IDEA. It is
still registered in --strategy so the result stays reproducible, and it
must not be given an account.

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
    MIN_MOVE_ATR = 0.15   # a guess, and measured wrong: the operator's own
                          # entries sit at 1.418 xATR, ~10x higher, so this
                          # fires on nearly every bar. Left at the tested
                          # value on purpose -- raising it does not help
                          # (the sweep to 2.00 is negative at every step),
                          # and changing it now would only make the
                          # rejected result harder to reproduce.

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
