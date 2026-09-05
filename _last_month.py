#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_last_month.py -- what the live configuration would have done, day by day.

Not a sweep and not a study: this replays the EXACT parameters the bot is
running right now, one session per row, so the last month can be read the
way it would have been lived rather than as an average.

    XAUAUDm  0.05 lot  gate 10 USD  min wait 1s  max wait 900s
    SL 3xATR  exit at the M15 close

The account balance is carried forward from row to row. The moment it
would have been closed out by the broker -- equity gone, which at 0.05
lot happens about 12 points against and long before the 3xATR stop -- the
run stops, because every row after that is a trade that could never have
been placed. An average over sessions that happen after the account is
gone is not a result.

Usage:  python _last_month.py [symbol] [days] [equity] [keepgoing]

keepgoing=1 carries on past the point where the account would have been
closed out, marking every such session. That is not what would really
have happened -- there is no account left to trade -- but it shows the
whole month rather than only the part before the first liquidation, which
is what you want when the question is "how do these sessions look" rather
than "what would I have".
"""
import sys
from datetime import datetime, timedelta, timezone

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)"); sys.exit(1)

import numpy as np

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "XAUAUDm"
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
EQUITY0 = float(sys.argv[3]) if len(sys.argv) > 3 else 43.38
KEEPGOING = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
LOT, GATE_USD, SL_ATR = 0.05, 10.0, 3.0
MIN_WAIT, MAX_WAIT, THAI = 1.0, 900, 7


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


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed"); return 2
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[ERROR] {SYMBOL} not found"); return 2
    mt5.symbol_select(SYMBOL, True)
    spread = info.spread * info.point
    tk = mt5.symbol_info_tick(SYMBOL)
    per_pt = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, LOT,
                                   tk.ask, tk.ask + 1.0) or 0.0
    acct = mt5.account_info()
    ccy = acct.currency if acct else "?"
    gate = GATE_USD / per_pt if per_pt else 2 * spread

    print("=" * 94)
    print(f" LAST {DAYS} DAYS AT 19:30 THAI -- exactly what the live bot is "
          f"configured to do")
    print(f" {SYMBOL}  {LOT} lot  gate {GATE_USD:.0f} {ccy} = {gate:.3f} pts  "
          f"SL {SL_ATR}xATR  exit at the M15 close")
    print(f" 1 pt = {per_pt:.3f} {ccy}   starting equity {EQUITY0:.2f}   "
          f"account ends at {EQUITY0/per_pt:.1f} pts against")
    print("=" * 94)
    print(f"\n{'date (Thai)':>16}{'fired':>7}{'side':>6}{'at':>8}"
          f"{'entry':>10}{'exit':>10}{'pts':>8}{'P/L':>9}{'worst':>9}"
          f"{'equity':>9}")
    print("-" * 94)

    eq = EQUITY0
    rows = wins = fired = blown = 0
    today = (datetime.now(timezone.utc) + timedelta(hours=THAI)).date()
    dead = None
    for back in range(DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        s_utc = datetime(d.year, d.month, d.day, 12, 30, tzinfo=timezone.utc)
        t = mt5.copy_ticks_range(SYMBOL, s_utc,
                                 s_utc + timedelta(seconds=MAX_WAIT + 60),
                                 mt5.COPY_TICKS_ALL)
        bars = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, s_utc,
                                    s_utc + timedelta(minutes=90))
        atr = atr_at(SYMBOL, s_utc)
        label = f"{d:%Y-%m-%d %a}"
        if t is None or len(t) < 20 or bars is None or len(bars) < 4 or not atr:
            print(f"{label:>16}{'-- no data (market shut) --':>40}")
            continue
        rows += 1
        t0 = int(s_utc.timestamp())
        sec = (t["time_msc"].astype(np.int64) - t0 * 1000) / 1000.0
        bid, ask = t["bid"].astype(float), t["ask"].astype(float)
        mid = np.where(ask > 0, (bid + ask) / 2.0, bid)
        ref = float(mid[0])
        w = np.where((sec >= MIN_WAIT) & (np.abs(mid - ref) >= gate))[0]
        if len(w) == 0:
            peak = float(np.max(np.abs(mid - ref)))
            print(f"{label:>16}{'no':>7}{'--':>6}{'--':>8}"
                  f"{'':>10}{'':>10}{'':>8}{'':>9}"
                  f"{'':>9}{eq:>9.2f}   peak {peak:.2f}/{gate:.2f}")
            continue
        fired += 1
        i = int(w[0])
        s = 1 if mid[i] > ref else -1
        entry = float(ask[i]) if s > 0 else float(bid[i])
        sl = entry - s * SL_ATR * atr
        srv_e = t0 + float(sec[i])
        end = (int(srv_e) // 900 + 1) * 900
        exit_px, worst = None, 0.0
        for b in bars:
            bt = int(b["time"])
            if bt + 300 <= srv_e:
                continue
            h, l, c = float(b["high"]), float(b["low"]), float(b["close"])
            worst = min(worst, ((l if s > 0 else h) - entry) * s)
            if ((l <= sl) if s > 0 else (h >= sl)):
                exit_px = sl; break
            if bt + 300 >= end:
                exit_px = c; break
        if exit_px is None:
            exit_px = float(bars[-1]["close"])
        pts = (exit_px - entry) * s - spread
        pl = pts * per_pt
        if pl > 0:
            wins += 1
        eq += pl
        flag = ""
        if -worst * per_pt >= eq - pl:
            flag = "  <-- ACCOUNT GONE"
            dead = label
        print(f"{label:>16}{'YES':>7}{'BUY' if s > 0 else 'SELL':>6}"
              f"{sec[i]:>7.0f}s{entry:>10.2f}{exit_px:>10.2f}{pts:>+8.2f}"
              f"{pl:>+9.2f}{worst*per_pt:>+9.2f}{eq:>9.2f}{flag}")
        if dead and not KEEPGOING:
            print(f"\n  STOPPED: the account was closed out on {dead}. "
                  f"Nothing after this could have been traded.")
            break
        if dead:
            blown += 1
            dead = None

    print("-" * 94)
    if rows:
        print(f"  {rows} sessions with data, gate opened on {fired} "
              f"({100.0*fired/rows:.0f}%), {wins} winners")
        if KEEPGOING:
            print(f"  {blown} of {fired} trades reached a moment that would "
                  f"have closed the account "
                  f"({100.0*blown/fired if fired else 0:.0f}%)")
        print(f"  equity {EQUITY0:.2f} -> {eq:.2f} {ccy} "
              f"({eq - EQUITY0:+.2f})")
    print("  'worst' is the deepest the trade went against you during the")
    print("  session, in money. When that reaches the equity, the broker")
    print("  closes it and the 3xATR stop never comes into it.")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
