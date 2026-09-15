"""Fake-MT5 run of _scalp_replay.py: must execute end to end, find the
planted trades, and label the coin-flip control."""
import io, contextlib, sys, time, types
from datetime import timedelta, timezone
import numpy as np
sys.path.insert(0, ".")
import _scalp_replay as R

def ticks(sym, d0, d1, flags):
    t0, t1 = d0.timestamp(), d1.timestamp()
    T = np.arange(t0, t1, 1.0)
    day = (T // 86400) * 86400
    rel = T - (day + 12 * 3600 + 30 * 60)           # seconds from 12:30 UTC
    px = 4000.0 + np.where((rel >= 0) & (rel < 300), rel / 300 * 8, 0.0)
    px = px + np.where(rel >= 300, 8.0, 0.0)
    px = px - np.where((rel >= 300) & (rel < 360), (rel - 300) / 60 * 2, 0.0)
    px = px - np.where(rel >= 360, 2.0, 0.0)
    arr = np.zeros(len(T), dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8")])
    arr["time_msc"] = (T * 1000).astype(np.int64)
    arr["bid"] = np.round(px, 3); arr["ask"] = np.round(px + 0.2, 3)
    return arr

R.mt5 = types.SimpleNamespace(
    initialize=lambda: True, shutdown=lambda: None, last_error=lambda: (0, "ok"),
    symbol_select=lambda s, b: True, COPY_TICKS_ALL=-1, ORDER_TYPE_BUY=0,
    symbol_info_tick=lambda s: types.SimpleNamespace(time=int(time.time()), bid=4000.0, ask=4000.2),
    order_calc_profit=lambda ty, s, v, p0, p1: (p1 - p0) * v * 100.0,
    copy_ticks_range=ticks)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = R.main(["--days", "10"])
out = buf.getvalue()
print(out)
fails = []
def ok(c, m):
    print(f"  {'OK  ' if c else 'FAIL'} {m}")
    if not c: fails.append(m)
ok(rc == 0, "runs end to end")
ok("$1.00 per point" in out, "0.01 lot priced at $1/point via the broker calc")
ok("COIN FLIP" in out and "beats" in out, "coin-flip control printed")
ok("exploration only" in out and "<- running" in out, "grid labelled exploration and marks the running settings")
n_ev = int(out.split("evenings with ticks: ")[1].split()[0])
ok(n_ev >= 6, f"weekday evenings found ({n_ev})")
ok(f"trades {n_ev}" in out and "won 100%" in out, "one planted fade-short TP per evening, all won")
print("\nFAILED" if fails else "\nREPLAY TEST PASSED")
sys.exit(1 if fails else 0)
