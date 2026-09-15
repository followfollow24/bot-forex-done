"""scalp_core.py -- the scalp rule as pure functions.

Imported by BOTH scalp_bot.py (live / paper) and _scalp_replay.py (history),
so what the replay measures is exactly what the bot does. No MT5 in here.

THE RULE (defaults in Params):
  * only inside the session window, Thai time (default 19:30-21:30)
  * when a 5-minute candle closes having moved >= min_move points,
      mode "fade"   -> trade AGAINST it  (catch the wave back)
      mode "follow" -> trade WITH it
  * one position at a time, entered at the first tick after the candle
    closes, and only if the spread is <= max_spread
  * exit at +tp points, -sl points, or after max_hold_min minutes
  * DAY GUARD: stop for the Thai day at +profit_stop or -loss_stop dollars
    (realized + open), and never more than max_trades entries a day
    -- the guard your own 9 days of trades said you need.

A "point" is 1.0 of gold price. At 0.01 lot on XAUUSDm that is $1.
"""
from __future__ import annotations

import random

import numpy as np
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

THAI = timedelta(hours=7)          # Thailand has no DST


@dataclass
class Params:
    mode: str = "fade"             # "fade" or "follow"
    min_move: float = 6.0          # points the 5-min candle must move
    tp: float = 1.0                # take profit, points
    sl: float = 5.0                # stop loss, points
    max_hold_min: float = 10.0     # time stop, minutes (0 = off)
    session: str = "19:30-21:30"   # Thai wall clock
    max_spread: float = 0.35       # skip entries when the spread is wider
    profit_stop: float = 50.0      # day guard, account currency (0 = off)
    loss_stop: float = 30.0        # day guard, account currency (0 = off)
    max_trades: int = 10           # entries per Thai day (0 = no cap)
    usd_per_point: float = 1.0     # at the traded lot; the bot measures it


def _hm(s: str) -> int:
    h, m = s.strip().split(":")
    return int(h) * 60 + int(m)


def thai_dt(utc_ts: float) -> datetime:
    return datetime.fromtimestamp(utc_ts, tz=timezone.utc) + THAI


def thai_date(utc_ts: float):
    return thai_dt(utc_ts).date()


def in_session(utc_ts: float, session: str) -> bool:
    a, b = (_hm(x) for x in session.split("-"))
    d = thai_dt(utc_ts)
    m = d.hour * 60 + d.minute
    return a <= m < b if a <= b else (m >= a or m < b)


def candle_signal(o: float, c: float, mode: str, min_move: float) -> int:
    """+1 buy, -1 sell, 0 nothing"""
    move = c - o
    if abs(move) < min_move or move == 0:
        return 0
    d = 1 if move > 0 else -1
    return -d if mode == "fade" else d


def exit_reason(side: int, entry: float, bid: float, ask: float,
                tp: float, sl: float, age_sec: float, max_sec: float):
    """A long is closed at the bid, a short at the ask -- the spread is paid."""
    px = bid if side > 0 else ask
    gain = (px - entry) * side
    if gain >= tp:
        return "TP", px
    if gain <= -sl:
        return "SL", px
    if max_sec > 0 and age_sec >= max_sec:
        return "TIME", px
    return None, px


@dataclass
class DayGuard:
    profit_stop: float
    loss_stop: float
    max_trades: int
    day: object = None
    realized: float = 0.0
    trades: int = 0
    stopped: str = ""

    def roll(self, today) -> bool:
        """start a fresh day; True if the day changed"""
        if today != self.day:
            self.day, self.realized, self.trades, self.stopped = today, 0.0, 0, ""
            return True
        return False

    def can_open(self) -> bool:
        if self.stopped:
            return False
        return not (self.max_trades > 0 and self.trades >= self.max_trades)

    def check(self, open_pnl: float = 0.0) -> str:
        """'PROFIT' / 'LOSS' once the day's total touches a stop, else ''"""
        if not self.stopped:
            tot = self.realized + open_pnl
            if self.profit_stop > 0 and tot >= self.profit_stop:
                self.stopped = "PROFIT"
            elif self.loss_stop > 0 and tot <= -self.loss_stop:
                self.stopped = "LOSS"
        return self.stopped

    def opened(self) -> None:
        self.trades += 1

    def closed(self, pnl: float) -> None:
        self.realized += pnl


def m5_signals(times, bids, p: Params):
    """[(candle_close_utc, direction)] from bid ticks, causal: a candle's
    signal uses only ticks inside that candle, and a candle only counts once
    a later tick exists (so the last, possibly unfinished candle is ignored).
    Vectorised: the per-tick Python loop was the replay's bottleneck."""
    T = np.asarray(times, dtype=float)
    Bd = np.asarray(bids, dtype=float)
    if len(T) == 0:
        return []
    bucket = (T // 300).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:] - 1, len(T) - 1]
    out = []
    for k in range(len(starts) - 1):          # the last bucket is unfinished
        close_t = float((bucket[starts[k]] + 1) * 300)
        if not in_session(close_t - 1, p.session):
            continue
        d = candle_signal(Bd[starts[k]], Bd[ends[k]], p.mode, p.min_move)
        if d:
            out.append((close_t, d))
    return out


def replay(times, bids, asks, p: Params, random_side: bool = False,
           rng: random.Random | None = None):
    """Run the rule over a tick stream. Returns a list of trade dicts.

    random_side=True keeps every entry time, spread check, exit and day
    guard identical and only flips a coin for the direction -- the control
    that says whether the SIGNAL adds anything beyond the timing.

    While flat, nothing can happen until the next signal, so the loop jumps
    straight there; tick-by-tick stepping only runs while a position is open.
    """
    rng = rng or random.Random(0)
    T = np.asarray(times, dtype=float)
    Bd = np.asarray(bids, dtype=float)
    Ad = np.asarray(asks, dtype=float)
    n = len(T)
    sigs = m5_signals(T, Bd, p)
    dayid = ((T + THAI.total_seconds()) // 86400).astype(np.int64)
    guard = DayGuard(p.profit_stop, p.loss_stop, p.max_trades)
    trades, pos, j = [], None, 0
    max_sec = p.max_hold_min * 60.0

    def close(i, reason, px):
        side, entry, t0 = pos
        pts = (px - entry) * side
        usd = pts * p.usd_per_point
        guard.closed(usd)
        trades.append(dict(open_t=t0, close_t=float(T[i]), side=side, entry=entry,
                           exit=float(px), points=float(pts), usd=float(usd),
                           reason=reason, day=thai_date(t0)))

    i = 0
    while i < n:
        if pos is None:
            if j >= len(sigs):
                break
            k = int(np.searchsorted(T, sigs[j][0], side="left"))
            if k >= n:
                break
            i = max(i, k)
        t = T[i]
        guard.roll(int(dayid[i]))
        if pos is not None:
            side, entry, t0 = pos
            reason, px = exit_reason(side, entry, Bd[i], Ad[i],
                                     p.tp, p.sl, t - t0, max_sec)
            open_usd = (px - entry) * side * p.usd_per_point
            if not reason and guard.check(open_usd):
                reason = "DAY_" + guard.stopped
            if reason:
                close(i, reason, px)
                guard.check(0.0)
                pos = None
        while j < len(sigs) and sigs[j][0] <= t:
            close_t, d = sigs[j]
            j += 1
            if t - close_t > 5.0:                 # no tick soon after the close
                continue
            if pos is not None or not guard.can_open():
                continue
            if Ad[i] - Bd[i] > p.max_spread:
                continue
            side = rng.choice((1, -1)) if random_side else d
            entry = float(Ad[i] if side > 0 else Bd[i])
            pos = (side, entry, float(t))
            guard.opened()
        i += 1
    return trades
