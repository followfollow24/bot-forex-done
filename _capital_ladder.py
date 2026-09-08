#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_capital_ladder.py -- run the LIVE configuration on history, at four
starting balances, and say plainly whether it made money.

Not a parameter search. Every rule here is the one the bot is running
right now, taken from start_gold_live.ps1 and not adjusted to flatter the
result:

    gate 14 points, fixed        entry when |price - 19:30 reference| >= 14
    earliest entry +1s           watching from the bell, give up at 15 min
    0.01 lot                     ordinary sessions
    0.05 lot                     when the gate falls inside 10s AND
                                 equity is at least 180 at that moment
    hold 30 minutes              no judgement about the move ending
    stop 3xATR(H1)               attached to the order

Two things make this an HONEST answer rather than a flattering one.

First, the account can end. Equity is carried forward trade by trade, and
before each trade's profit is booked the run checks whether the position
went further against the entry than the account could hold. If it did,
the broker closed it and there is no account left -- the simulation stops
there instead of continuing to compound a balance that does not exist.

Second, the period is split. The 14-point gate was chosen by sweeping
June-September, so results on those months are partly a memory of the
choice. Anything before June was never involved in picking it, and that
column is the one to believe. They are reported separately and never
added together.

Usage:  python _capital_ladder.py [symbol] [days]
"""
import sys
from datetime import date, datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 365
STARTS = [50.0, 100.0, 150.0, 200.0]
GATE, FAST_SEC, SMALL, BIG, FLOOR = 14.0, 10.0, 0.01, 0.05, 180.0
HOLD_MIN, SL_ATR, MIN_WAIT, MAXW, THAI = 30.0, 3.0, 1.0, 900, 7
FITTED_FROM = date(2026, 6, 1)      # the gate was swept on June onwards


def atr_at(sym, when, n=14):
    r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1,
                             when - timedelta(hours=40), when)
    if r is None or len(r) < n + 1:
        return None
    trs = [max(float(r[i]["high"]) - float(r[i]["low"]),
               abs(float(r[i]["high"]) - float(r[i - 1]["close"])),
               abs(float(r[i]["low"]) - float(r[i - 1]["close"])))
           for i in range(1, len(r))]
    return sum(trs[-n:]) / n


def load(sym):
    """One entry per session that cleared the gate, in date order."""
    out, shut = [], 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(sym, s_utc,
                                 s_utc + timedelta(seconds=MAXW + 60),
                                 mt5.COPY_TICKS_ALL)
        if t is None or len(t) < 20:
            continue
        bars = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=120))
        atr = atr_at(sym, s_utc)
        if bars is None or len(bars) < 6 or not atr:
            shut += 1; continue
        si = mt5.symbol_info(sym)
        spread = si.spread * si.point
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= GATE))[0]
        if len(w) == 0:
            out.append({"date": d, "traded": False})
            continue
        i = int(w[0])
        s = 1 if mid[i] > ref else -1
        entry = float(ask[i]) if s > 0 else float(bid[i])
        sl = entry - s * SL_ATR * atr
        srv = t0 + float(sec[i])
        end = srv + HOLD_MIN * 60
        exit_px, worst = None, 0.0
        for b in bars:
            bt = int(b["time"])
            if bt + 300 <= srv:
                continue
            h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
            worst = min(worst, ((l if s > 0 else h) - entry) * s)
            if ((l <= sl) if s > 0 else (h >= sl)):
                exit_px = sl; break
            if bt + 300 >= end:
                exit_px = c; break
        if exit_px is None:
            exit_px = float(bars[-1]["close"])
        out.append({"date": d, "traded": True, "t": float(sec[i]),
                    "pts": (exit_px - entry) * s - spread, "worst": worst})
    return out, shut


def run(rows, start, pp):
    """Walk the sessions in order. The account can end; if it does, so
    does the run -- there are no trades after a broker liquidation."""
    eq, peak, dd = start, start, 0.0
    n = w = fast = 0
    worst_trade = 0.0
    dead = None
    for r in rows:
        if not r["traded"]:
            continue
        lot = BIG if (r["t"] <= FAST_SEC and eq >= FLOOR) else SMALL
        if -r["worst"] * pp[lot] >= eq:
            dead = r["date"]
            eq = 0.0
            break
        n += 1
        fast += lot == BIG
        pl = r["pts"] * pp[lot]
        worst_trade = min(worst_trade, pl)
        eq += pl
        w += pl > 0
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dict(n=n, w=w, fast=fast, eq=eq, dd=dd, worst=worst_trade,
                dead=dead, peak=peak)


def table(title, rows, pp, note):
    print(f"\n{title}")
    print(f"  {len(rows)} sessions, "
          f"{sum(1 for r in rows if r['traded'])} of them cleared the gate"
          f"  ({rows[0]['date']} to {rows[-1]['date']})")
    print(f"  {note}\n")
    print(f"{'start':>8}{'trades':>8}{'wins':>6}{'win %':>7}{'big':>5}"
          f"{'final':>10}{'return':>9}{'max DD':>9}{'worst':>8}   outcome")
    print("-" * 78)
    for s in STARTS:
        r = run(rows, s, pp)
        ret = (r["eq"] / s - 1.0) * 100.0
        out = (f"ACCOUNT ENDED {r['dead']:%d %b}" if r["dead"]
               else "survived")
        wr = (100.0 * r["w"] / r["n"]) if r["n"] else 0.0
        print(f"{s:>7.0f}{r['n']:>8}{r['w']:>6}{wr:>6.0f}%{r['fast']:>5}"
              f"{r['eq']:>10.2f}{ret:>+8.0f}%{r['dd']:>9.2f}"
              f"{r['worst']:>8.2f}   {out}")
    print("-" * 78)


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    if mt5.symbol_info(SYMBOL) is None:
        print(f"[ERROR] {SYMBOL} not found"); mt5.shutdown(); return 2
    mt5.symbol_select(SYMBOL, True)
    tk = mt5.symbol_info_tick(SYMBOL)
    pp = {}
    for lot in (SMALL, BIG):
        v = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, lot,
                                  tk.ask, tk.ask + 1.0)
        if not v:
            print("[ERROR] the broker would not price this symbol -- MT5 may "
                  "have lost its connection or the market is shut")
            mt5.shutdown(); return 2
        pp[lot] = float(v)
    ac = mt5.account_info()
    ccy = ac.currency if ac else "?"

    print(f"loading up to {DAYS} days of ticks ...", flush=True)
    rows, shut = load(SYMBOL)
    if not rows:
        print("[ERROR] no sessions with tick data"); mt5.shutdown(); return 2

    print("=" * 78)
    print(f" THE LIVE CONFIGURATION ON HISTORY -- {SYMBOL}")
    print(f" gate {GATE:g} pts | {SMALL} lot, {BIG} when the gate falls "
          f"inside {FAST_SEC:g}s and equity >= {FLOOR:g}")
    print(f" hold {HOLD_MIN:.0f} min | stop {SL_ATR}xATR | 1 pt = "
          f"{pp[SMALL]:.3f} {ccy} at {SMALL}, {pp[BIG]:.3f} at {BIG}")
    print("=" * 78)

    clean = [r for r in rows if r["date"] < FITTED_FROM]
    fitted = [r for r in rows if r["date"] >= FITTED_FROM]

    if clean:
        table("BELIEVE THIS ONE -- months that never chose the gate",
              clean, pp,
              "The 14-point gate was picked by sweeping June onwards. These "
              "months had no part in that,\n  so this is the closest thing "
              "here to an honest forecast.")
    if fitted:
        table("TREAT THIS ONE AS OPTIMISTIC -- the months the gate was "
              "chosen on", fitted, pp,
              "The gate was selected because it did well here. Quoting it "
              "back is partly a memory of\n  that choice, not evidence.")
    table("BOTH TOGETHER, for completeness", rows, pp,
          "Half fitted, half not. The number to plan with is the first "
          "table, not this one.")

    print("\nWHAT THE COLUMNS MEAN")
    print("  big      how many sessions were sized up to 0.05. It needs a")
    print(f"           gate inside {FAST_SEC:g}s AND equity {FLOOR:g}+, so a small")
    print("           account never reaches it and the row stays all 0.01.")
    print("  max DD   the deepest fall from a running peak, in account")
    print("           currency -- what you would have had to sit through.")
    print("  outcome  ACCOUNT ENDED means a position went further against")
    print("           the entry than the balance could hold. The broker")
    print("           closes it, and no later trade in that row happened.")
    print("\n  Slippage is not modelled, exits are read off M5 bar")
    print("  high/low so the real path could be worse, and today's spread")
    print("  is used throughout. All three push the true result DOWN.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
