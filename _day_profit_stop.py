"""Your own trades, replayed tick by tick: what if you had STOPPED at +$X?

READ-ONLY. Reads the MT5 terminal that is already logged in. Places,
closes and modifies nothing, and never touches a password.

For every day in the look-back it rebuilds your running day P&L second by
second -- realized trades plus whatever was still open, priced from real
ticks -- and then asks one question per target (e.g. +20 / +30 / +50):

    the first moment the day was up by the target, close everything and
    stop for the day. What would the day have ended at instead?

Days that never reached the target are left exactly as they happened. Those
days decide whether the rule helps, so they are reported separately.

Usage on the VPS:
    python _day_profit_stop.py                 # last 30 days, targets 20,30,50
    python _day_profit_stop.py --days 60 --targets 20,50,100
    python _day_profit_stop.py --all           # include bot trades (magic != 0)
"""
from __future__ import annotations
import argparse, sys, time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except Exception:                                    # replaced by a fake in tests
    mt5 = None

THAI = timedelta(hours=7)
OUT_FILE = "day_profit_stop.txt"
LINES: list[str] = []


QUIET = {"on": False}


def say(s=""):
    if not QUIET["on"]:
        print(s)
    LINES.append(s)


def broker_offset_hours(symbols):
    """MT5 stamps deals and ticks in SERVER time. Measure the offset once."""
    now = time.time()
    for s in symbols:
        tk = mt5.symbol_info_tick(s)
        if tk is not None and abs(tk.time - now) < 12 * 3600:
            return round((tk.time - now) / 3600.0)
    return 0


def per_unit(symbol, price):
    """account-currency profit for 1.0 lot and a +1.0 price move (broker calc)"""
    p = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, price, price + 1.0)
    return float(p) if p else None


def positions_from_deals(deals, include_bots):
    """group deals into positions: list of dict with ordered events"""
    by = {}
    for d in deals:
        if d.symbol == "" or d.type not in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
            continue                                  # balance / credit rows
        if not include_bots and getattr(d, "magic", 0) not in (0, None):
            continue
        by.setdefault(d.position_id, []).append(d)
    out = []
    for pid, ds in by.items():
        ds.sort(key=lambda z: (z.time_msc, z.ticket))
        first_in = next((z for z in ds if z.entry == mt5.DEAL_ENTRY_IN), None)
        if first_in is None:
            continue                                  # opened before the window
        side = 1 if first_in.type == mt5.DEAL_TYPE_BUY else -1
        out.append(dict(pid=pid, symbol=first_in.symbol, side=side,
                        magic=getattr(first_in, "magic", 0), deals=ds))
    return out


def position_path(pos, now_srv, grid_sec=1):
    """Series (index: server epoch seconds) of this position's contribution
    to day P&L: unrealized while open, realized after, 0 before."""
    sym, side, ds = pos["symbol"], pos["side"], pos["deals"]
    t0 = ds[0].time_msc / 1000.0
    closed = [z for z in ds if z.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    vol_in = sum(z.volume for z in ds if z.entry == mt5.DEAL_ENTRY_IN)
    vol_out = sum(z.volume for z in closed)
    still_open = vol_out + 1e-9 < vol_in
    t1 = (closed[-1].time_msc / 1000.0) if (closed and not still_open) else now_srv

    k = per_unit(sym, ds[0].price)
    ticks = mt5.copy_ticks_range(sym, datetime.fromtimestamp(t0, tz=timezone.utc),
                                 datetime.fromtimestamp(t1 + 1, tz=timezone.utc),
                                 mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0 or k is None:
        return None, dict(sym=sym, why="no ticks or no broker price calc")

    tt = ticks["time_msc"].astype(np.int64) / 1000.0
    px = ticks["bid"].astype(float) if side > 0 else ticks["ask"].astype(float)
    ok = px > 0
    tt, px = tt[ok], px[ok]

    # walk events: running volume, average entry, realized P&L
    ev = sorted(ds, key=lambda z: z.time_msc)
    vol, avg, real = 0.0, 0.0, 0.0
    ev_t = np.array([z.time_msc / 1000.0 for z in ev])
    state_vol, state_avg, state_real = [], [], []
    for z in ev:
        if z.entry == mt5.DEAL_ENTRY_IN:
            avg = (avg * vol + z.price * z.volume) / (vol + z.volume)
            vol += z.volume
        else:
            vol = max(0.0, vol - z.volume)
            real += z.profit + z.commission + z.swap + getattr(z, "fee", 0.0)
        state_vol.append(vol); state_avg.append(avg); state_real.append(real)
    state_vol, state_avg, state_real = map(np.array, (state_vol, state_avg, state_real))

    j = np.searchsorted(ev_t, tt, side="right") - 1
    j = np.clip(j, 0, len(ev) - 1)
    unreal = (px - state_avg[j]) * side * state_vol[j] * k
    pnl = state_real[j] + unreal

    s = pd.Series(pnl, index=np.floor(tt / grid_sec) * grid_sec)
    s = s[~s.index.duplicated(keep="last")]
    s.loc[np.floor(t0 - 1)] = 0.0                      # flat before it opened
    if not still_open:
        s.loc[np.floor(t1) + 1] = state_real[-1]       # banked after close
    s = s.sort_index()
    info = dict(sym=sym, side="BUY" if side > 0 else "SELL",
                lot=float(vol_in), open_t=t0, close_t=None if still_open else t1,
                open_px=float(ds[0].price),
                close_px=float(closed[-1].price) if closed else float("nan"),
                result=float(state_real[-1] if not still_open else pnl[-1]),
                still_open=still_open, magic=pos["magic"])
    return s, info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--targets", default="20,30,50")
    ap.add_argument("--all", action="store_true", help="include bot trades")
    ap.add_argument("--loss-stops", default="0,20,30,50",
                    help="daily loss limits to pair with each target; 0 = none")
    ap.add_argument("--after", type=float, default=20.0,
                    help="a trade counts as 'opened while up' when the day was already >= +this")
    ap.add_argument("--full", action="store_true",
                    help="print the long per-trade report too (always saved to file)")
    a = ap.parse_args(argv)
    targets = [float(x) for x in a.targets.split(",") if x.strip()]
    losses = [float(x) for x in a.loss_stops.split(",") if x.strip()]

    if mt5 is None:
        print("[ERROR] needs MetaTrader5 -- run on the VPS"); return 2
    if not mt5.initialize():
        print(f"[ERROR] MT5 init failed: {mt5.last_error()}"); return 2
    acct = mt5.account_info()
    if acct is None:
        print("[ERROR] no account logged in to the terminal"); return 2
    ccy = acct.currency

    now_utc = time.time()
    frm = datetime.now(timezone.utc) - timedelta(days=a.days + 1)
    to = datetime.now(timezone.utc) + timedelta(days=1)
    deals = mt5.history_deals_get(frm, to)
    if deals is None:                                  # MT5 signals errors by None
        print(f"[ERROR] history_deals_get returned None ({mt5.last_error()})"
              " -- CANNOT TELL, not 'no trades'"); return 2

    poss = positions_from_deals(list(deals), a.all)
    syms = sorted({p["symbol"] for p in poss})
    off_h = broker_offset_hours(syms or ["XAUUSDm", "XAUUSD"])
    now_srv = now_utc + off_h * 3600

    say("=" * 78)
    say(f" YOUR TRADES, REPLAYED -- account {acct.login} ({acct.server})"
        f"  equity {acct.equity:,.2f} {ccy}")
    say(f" last {a.days} days   {'all trades' if a.all else 'manual trades only (magic 0)'}"
        f"   broker clock UTC{off_h:+d}   targets {', '.join(f'+{t:g}' for t in targets)} {ccy}")
    say("=" * 78)
    if not poss:
        say("\n  no closed or open positions found in the window"); _save(); return 0

    paths, infos, skipped = [], [], []
    for p in poss:
        s, info = position_path(p, now_srv)
        if s is None:
            skipped.append(info); continue
        paths.append(s); infos.append(info)

    def thai_day(srv_t):
        return (datetime.fromtimestamp(srv_t - off_h * 3600, tz=timezone.utc) + THAI).date()

    days = sorted({thai_day(i["open_t"]) for i in infos})
    day_rows = []
    for dday in days:
        idx = [n for n, i in enumerate(infos) if thai_day(i["open_t"]) == dday]
        cols = {n: paths[n] for n in idx}
        grid = sorted(set().union(*[set(c.index) for c in cols.values()]))
        df = pd.DataFrame({n: c.reindex(grid).ffill().fillna(0.0) for n, c in cols.items()},
                          index=grid)
        tot = df.sum(axis=1)
        end = float(tot.iloc[-1])
        pk_t = float(tot.idxmax()); pk = float(tot.max())
        lo = float(tot.min())
        rule = {}
        for T in targets:
            hit = tot[tot >= T]
            if len(hit):
                rule[T] = (float(hit.iloc[0]), float(hit.index[0]))
            else:
                rule[T] = (end, None)
        day_rows.append(dict(day=dday, n=len(idx), end=end, peak=pk, peak_t=pk_t,
                             low=lo, rule=rule, idx=idx, path=tot))

    def hhmm(srv_t):
        return (datetime.fromtimestamp(srv_t - off_h * 3600, tz=timezone.utc) + THAI).strftime("%H:%M:%S")

    QUIET["on"] = not a.full         # long sections -> file only, unless --full
    # ---- the most recent day in detail ------------------------------------
    last = day_rows[-1]
    say(f"\n{'-'*78}\n LATEST DAY {last['day']}  ({last['n']} trades)\n{'-'*78}")
    say(f"{'open':>9}{'close':>10}{'symbol':>10}{'side':>6}{'lot':>6}"
        f"{'open px':>11}{'close px':>11}{'result':>10}")
    for n in last["idx"]:
        i = infos[n]
        say(f"{hhmm(i['open_t']):>9}{(hhmm(i['close_t']) if i['close_t'] else 'OPEN'):>10}"
            f"{i['sym']:>10}{i['side']:>6}{i['lot']:>6.2f}{i['open_px']:>11.3f}"
            f"{i['close_px']:>11.3f}{i['result']:>+10.2f}")
    say(f"\n  best moment of the day : {last['peak']:+.2f} {ccy} at {hhmm(last['peak_t'])}")
    say(f"  worst moment of the day: {last['low']:+.2f} {ccy}")
    say(f"  how the day ended      : {last['end']:+.2f} {ccy}"
        f"   (gave back {last['peak'] - last['end']:.2f} from the best moment)")
    for T in targets:
        v, t = last["rule"][T]
        if t is None:
            say(f"  stop at +{T:g}: never reached -> day unchanged {v:+.2f}")
        else:
            say(f"  stop at +{T:g}: reached at {hhmm(t)} -> day would end {v:+.2f}"
                f"  instead of {last['end']:+.2f}")

    # ---- every day -------------------------------------------------------
    say(f"\n{'-'*78}\n EVERY DAY\n{'-'*78}")
    say(f"{'day':>11}{'trades':>7}{'best':>9}{'worst':>9}{'actual':>9}"
        + "".join(f"{f'stop+{T:g}':>10}" for T in targets))
    for r in day_rows:
        cells = ""
        for T in targets:
            v, t = r["rule"][T]
            cells += f"{v:>+9.2f}{'*' if t else ' '}"
        say(f"{str(r['day']):>11}{r['n']:>7}{r['peak']:>+9.2f}{r['low']:>+9.2f}"
            f"{r['end']:>+9.2f}{cells}")
    say("  * = the target was reached that day, so the rule closed and stopped")

    # ---- the verdict -------------------------------------------------------
    say(f"\n{'='*92}\n TOTALS\n{'='*92}")
    actual = sum(r["end"] for r in day_rows)
    say(f"\n  actual, as you traded           : {actual:+10.2f} {ccy}"
        f"   over {len(day_rows)} days")
    for T in targets:
        tot_r = sum(r["rule"][T][0] for r in day_rows)
        hit = [r for r in day_rows if r["rule"][T][1] is not None]
        miss = [r for r in day_rows if r["rule"][T][1] is None]
        saved = sum(r["rule"][T][0] - r["end"] for r in hit)
        say(f"\n  stop at +{T:g}                    : {tot_r:+10.2f} {ccy}"
            f"   ({tot_r - actual:+.2f} vs actual)")
        say(f"     days the target was reached : {len(hit):>3}"
            f"   -> rule banked {sum(r['rule'][T][0] for r in hit):+.2f}"
            f", those days actually ended {sum(r['end'] for r in hit):+.2f}"
            f"  (difference {saved:+.2f})")
        say(f"     days it was NEVER reached   : {len(miss):>3}"
            f"   -> {sum(r['end'] for r in miss):+.2f}  (the rule cannot help these)")
    QUIET["on"] = False
    # ---- SUMMARY THAT FITS ONE SCREEN -------------------------------------
    def first_touch(path, T, L):
        """day result if you stop at +T or -L, whichever is touched first.
        T or L of 0 means that side is off. Same second -> the loss."""
        v = path.to_numpy()
        up = np.flatnonzero(v >= T) if T > 0 else np.array([], int)
        dn = np.flatnonzero(v <= -L) if L > 0 else np.array([], int)
        iu = up[0] if len(up) else None
        idn = dn[0] if len(dn) else None
        if iu is None and idn is None:
            return float(v[-1]), ""
        if idn is not None and (iu is None or idn <= iu):
            return float(v[idn]), "L"
        return float(v[iu]), "P"

    say("\n" + "#" * 78)
    say(f"#  SUMMARY  acct {acct.login}  {len(day_rows)} days"
        f"  {sum(r['n'] for r in day_rows)} trades  (full detail: {OUT_FILE})")
    say("#" * 78)
    say(f"\n  {'day':>10}{'trades':>7}{'best':>9}{'worst':>9}{'ended':>9}")
    for r in day_rows:
        say(f"  {str(r['day']):>10}{r['n']:>7}{r['peak']:>+9.2f}"
            f"{r['low']:>+9.2f}{r['end']:>+9.2f}")
    say(f"  {'TOTAL':>10}{'':>25}{sum(r['end'] for r in day_rows):>+9.2f}")

    say(f"\n  STOP-AT-PROFIT x STOP-AT-LOSS  ({len(day_rows)}-day total, {ccy})")
    head = "  profit\\loss" + "".join(
        f"{('none' if L == 0 else f'-{L:g}'):>10}" for L in losses)
    say(head)
    grid = {}
    for T in [0.0] + targets:
        row = f"  {('none' if T == 0 else f'+{T:g}'):>11}"
        for L in losses:
            tot_g = 0.0; nP = nL = 0
            for r in day_rows:
                v, why = first_touch(r["path"], T, L)
                tot_g += v; nP += why == "P"; nL += why == "L"
            grid[(T, L)] = (tot_g, nP, nL)
            row += f"{tot_g:>+10.2f}"
        say(row)
    best = max(grid.items(), key=lambda kv: kv[1][0])
    (bT, bL), (bv, bp, bl) = best
    say(f"\n  best cell: profit {'none' if bT == 0 else f'+{bT:g}'},"
        f" loss {'none' if bL == 0 else f'-{bL:g}'} -> {bv:+.2f}"
        f"  (profit stop fired {bp}x, loss stop {bl}x)")
    say(f"  as traded: {grid[(0.0, 0.0)][0]:+.2f}")

    # ---- does the day's P&L change how you trade? ----------------------
    say(f"\n  TRADES GROUPED BY HOW THE DAY STOOD WHEN YOU OPENED THEM")
    say(f"  {'opened while':>22}{'trades':>7}{'won':>6}{'total':>10}"
        f"{'per trade':>11}{'avg lot':>9}")
    groups = {"UP (>= +%g)" % a.after: [], "flat": [], "DOWN (<= -%g)" % a.after: []}
    keys = list(groups)
    for r in day_rows:
        path = r["path"]
        for n in r["idx"]:
            t0 = infos[n]["open_t"]
            before = path[path.index < t0 - 0.5]
            stand = float(before.iloc[-1]) if len(before) else 0.0
            k = keys[0] if stand >= a.after else keys[2] if stand <= -a.after else keys[1]
            groups[k].append(infos[n])
    for k in keys:
        g = groups[k]
        if not g:
            say(f"  {k:>22}{0:>7}"); continue
        res = np.array([i["result"] for i in g])
        say(f"  {k:>22}{len(g):>7}{(res > 0).mean()*100:>5.0f}%{res.sum():>+10.2f}"
            f"{res.mean():>+11.2f}{np.mean([i['lot'] for i in g]):>9.2f}")
    say("  If UP trades lose more per trade or use bigger lots than flat ones,")
    say("  the leak is what happens after you are already winning.")

    if skipped:
        say(f"\n  skipped {len(skipped)} position(s) with no tick history:"
            f" {', '.join(sorted({s['sym'] for s in skipped}))}")
    say("\n  Closing takes a few seconds in real life; fills here are the exact")
    say("  tick where the target was first touched, so real results are a little worse.")
    say("=" * 78)
    _save()
    mt5.shutdown()
    return 0


def _save():
    try:
        with open(OUT_FILE, "w", encoding="utf-8") as fh:
            fh.write("\n".join(LINES) + "\n")
        print(f"\n(saved to {OUT_FILE})")
    except Exception as exc:
        print(f"(could not save {OUT_FILE}: {exc!r})")


if __name__ == "__main__":
    sys.exit(main())
