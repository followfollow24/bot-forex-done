"""Replay the scalp bot's exact rule over your broker's real tick history.

READ-ONLY. Uses scalp_core -- the same module scalp_bot.py trades with --
so this is what the bot would have done, evening by evening, with the real
bid/ask spread paid on every trade.

Two honesty checks are printed next to the result:
  * COIN FLIP: same entry times, same exits, same day guard, direction by a
    coin. If the rule does not beat that, the signal adds nothing.
  * A small grid of other settings, clearly marked as exploration: picking
    the best cell afterwards is exactly how every gold search this project
    ran fooled itself at first.

    python _scalp_replay.py                      # defaults, last 120 days
    python _scalp_replay.py --mode follow --min-move 3 --tp 2
"""
from __future__ import annotations
import argparse, random, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np

try:
    import MetaTrader5 as mt5
except Exception:
    mt5 = None

import scalp_core as S

OUT = "scalp_replay.txt"
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def broker_offset(sym):
    tk = mt5.symbol_info_tick(sym)
    if tk is not None and abs(tk.time - time.time()) < 12 * 3600:
        return round((tk.time - time.time()) / 3600.0)
    return 0


def evenings(sym, days, p, off_h):
    """[(thai_date, T_utc, bid, ask)] per evening"""
    a, b = (S._hm(x) for x in p.session.split("-"))
    today = (datetime.now(timezone.utc) + S.THAI).date()
    out = []
    for back in range(days, -1, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        start_thai = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(minutes=a)
        start_utc = start_thai - S.THAI - timedelta(minutes=30)   # room for a gate candle before the session
        end_utc = start_thai - S.THAI + timedelta(minutes=(b - a) + p.max_hold_min + 10)
        srv0 = start_utc + timedelta(hours=off_h)
        srv1 = end_utc + timedelta(hours=off_h)
        tk = mt5.copy_ticks_range(sym, srv0, srv1, mt5.COPY_TICKS_ALL)
        if tk is None or len(tk) < 200:
            continue
        T = tk["time_msc"].astype(np.int64) / 1000.0 - off_h * 3600
        bid = tk["bid"].astype(float); ask = tk["ask"].astype(float)
        ok = (bid > 0) & (ask > 0) & (ask >= bid)
        if ok.sum() < 200:
            continue
        out.append((d, T[ok], bid[ok], ask[ok]))
    return out


def run(evs, p, random_side=False, seed=0):
    rng = random.Random(seed)
    trades = []
    for d, T, B, A in evs:
        trades += S.replay(T, B, A, p, random_side=random_side, rng=rng)
    return trades


def summary(trades):
    if not trades:
        return 0, 0.0, 0.0, 0.0
    pts = np.array([t["points"] for t in trades])
    usd = np.array([t["usd"] for t in trades])
    return len(trades), float((pts > 0).mean()), float(pts.mean()), float(usd.sum())


def main(argv=None):
    base = S.Params()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSDm")
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--days", type=int, default=120)
    for k, v in base.__dict__.items():
        if k == "usd_per_point":
            continue
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), default=v)
    a = ap.parse_args(argv)

    if mt5 is None:
        print("[ERROR] needs MetaTrader5 -- run on the VPS"); return 2
    if not mt5.initialize():
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}"); return 2
    if not mt5.symbol_select(a.symbol, True):
        print(f"[ERROR] symbol {a.symbol} not available"); return 2
    tk = mt5.symbol_info_tick(a.symbol)
    upp = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, a.symbol, a.lot, tk.ask, tk.ask + 1.0) if tk else None
    if not upp:
        print("[ERROR] broker would not price a point for this lot"); return 2
    p = S.Params(**{k: getattr(a, k) for k in base.__dict__ if k != "usd_per_point"},
                 usd_per_point=float(upp))
    off_h = broker_offset(a.symbol)

    evs = evenings(a.symbol, a.days, p, off_h)
    say("=" * 78)
    say(f" SCALP REPLAY  {a.symbol}  lot {a.lot}  (${p.usd_per_point:.2f} per point)"
        f"  broker UTC{off_h:+d}")
    say(f" {p.mode} candles >= {p.min_move:g} pts | TP {p.tp:g} SL {p.sl:g}"
        f" | hold <= {p.max_hold_min:g}m | {p.session} Thai"
        f" | day +{p.profit_stop:g}/-{p.loss_stop:g} max {p.max_trades}")
    if not evs:
        say("\n  no tick history for these evenings"); _save(); return 0
    say(f" evenings with ticks: {len(evs)}  ({evs[0][0]} .. {evs[-1][0]})")
    say("=" * 78)

    tr = run(evs, p)
    n, wr, avg_pts, tot = summary(tr)
    reasons = {}
    for t in tr:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    by_day = {}
    for t in tr:
        by_day.setdefault(t["day"], []).append(t["usd"])
    days_pos = sum(1 for v in by_day.values() if sum(v) > 0)
    say(f"\n  trades {n}   won {wr*100:.0f}%   avg {avg_pts:+.3f} pts/trade"
        f"   TOTAL ${tot:+.2f}")
    say(f"  exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    say(f"  evenings traded {len(by_day)}: {days_pos} up, {len(by_day) - days_pos} down"
        f"   best ${max((sum(v) for v in by_day.values()), default=0):+.2f}"
        f"   worst ${min((sum(v) for v in by_day.values()), default=0):+.2f}")

    # coin flip control
    flips = [summary(run(evs, p, random_side=True, seed=s))[3] for s in range(1, 201)]
    flips = np.array(flips)
    beat = float((flips < tot).mean())
    say(f"\n  COIN FLIP (same times, exits, guard; 200 runs):"
        f"  mean ${flips.mean():+.2f}  range ${np.percentile(flips, 5):+.2f}..${np.percentile(flips, 95):+.2f}")
    say(f"  the rule beats {beat*100:.0f}% of coin flips"
        + ("  -> signal adds nothing detectable" if beat < 0.95 else "  -> beyond chance"))

    # exploration grid -- label it as such
    say(f"\n  OTHER SETTINGS (exploration only -- best cell here is NOT evidence)")
    say(f"  {'mode':>7}{'move':>6}{'TP':>5}{'SL':>5}{'trades':>8}{'won':>6}{'pts/tr':>9}{'total $':>10}")
    for mode in ("fade", "follow"):
        for mm in (3.0, 6.0, 10.0):
            for tpv, slv in ((1.0, 5.0), (2.0, 4.0)):
                q = S.Params(**{**p.__dict__, "mode": mode, "min_move": mm, "tp": tpv, "sl": slv})
                nn, ww, aa, tt = summary(run(evs, q))
                mark = " <- running" if (mode, mm, tpv, slv) == (p.mode, p.min_move, p.tp, p.sl) else ""
                say(f"  {mode:>7}{mm:>6g}{tpv:>5g}{slv:>5g}{nn:>8}{ww*100:>5.0f}%{aa:>+9.3f}{tt:>+10.2f}{mark}")

    # selective days: the day gate, halves, coin flips
    say(f"\n  NOT EVERY DAY -- trade {p.session} only when the {p.gate_at or p.session.split('-')[0]} candle moved >= gate pts")
    say(f"  (exploration; halves = first/second {len(evs)//2} evenings; coin = % of 50 flips beaten)")
    say(f"  {'gate':>5}{'mode':>7}{'TP/SL':>6}{'days':>5}{'trades':>7}{'won':>5}"
        f"{'pts/tr':>8}{'total$':>8}{'half1$':>8}{'half2$':>8}{'coin':>6}")
    h = len(evs) // 2
    for gate in (0.0, 5.0, 10.0, 15.0, 20.0):
        for mode in ("fade", "follow"):
            for tpv, slv in ((1.0, 5.0), (3.0, 3.0)):
                q = S.Params(**{**p.__dict__, "mode": mode, "tp": tpv, "sl": slv,
                                "day_gate": gate})
                trq = run(evs, q)
                nn, ww, aa, tt = summary(trq)
                if nn == 0:
                    say(f"  {gate:>5g}{mode:>7}{f'{tpv:g}/{slv:g}':>6}{0:>5}{0:>7}")
                    continue
                h1 = summary(run(evs[:h], q))[3]
                h2 = summary(run(evs[h:], q))[3]
                fl = np.array([summary(run(evs, q, random_side=True, seed=s))[3]
                               for s in range(1, 51)])
                days_n = len({x["day"] for x in trq})
                say(f"  {gate:>5g}{mode:>7}{f'{tpv:g}/{slv:g}':>6}{days_n:>5}{nn:>7}"
                    f"{ww*100:>4.0f}%{aa:>+8.3f}{tt:>+8.2f}{h1:>+8.2f}{h2:>+8.2f}"
                    f"{(fl < tt).mean()*100:>5.0f}%")

    say(f"\n  Real fills can be worse than ticks (slippage at 19:30). The spread is")
    say(f"  already paid: longs exit at the bid, shorts at the ask.  saved -> {OUT}")
    say("=" * 78)
    _save()
    mt5.shutdown()
    return 0


def _save():
    try:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(LINES) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
