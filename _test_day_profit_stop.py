"""Fake-MT5 test for _day_profit_stop.py. Scenario built to match the
operator's day: up +60 at best, closed at -40. Values checked exactly."""
import sys, time, types, io, contextlib
from collections import namedtuple
import numpy as np

sys.path.insert(0, ".")
import _day_profit_stop as D

NOW = float(int(time.time()))
DAY = 86400.0
# Day A two days ago 12:30 UTC; Day B yesterday 13:00; Day C today-ish
def at(days_ago, hh, mm, ss=0):
    base = (NOW // DAY - days_ago) * DAY
    return base + hh * 3600 + mm * 60 + ss

Deal = namedtuple("Deal", "ticket position_id symbol type entry volume price profit "
                          "commission swap fee magic time_msc")
CONTRACT = 100.0          # standard gold: 1 lot = 100 oz, $1 move = $100/lot
SPREAD = 0.20

# piecewise mid-price timeline (absolute seconds -> mid)
knots = [
    (at(2, 12, 29), 4000.10), (at(2, 12, 30), 4000.10),   # A: buy at ask 4000.20
    (at(2, 12, 40), 4006.10),                              # bid 4006.00 -> +58
    (at(2, 13, 0), 3996.10),                               # bid 3996.00 -> -42
    (at(1, 12, 59), 4010.00), (at(1, 13, 0), 4010.00),     # B: sell at bid 4009.90
    (at(1, 13, 30), 4026.00),                              # ask 4026.10 -> -81
    (at(1, 14, 0), 4020.00), (at(1, 14, 5), 4024.00),      # C: buy 0.10 @ ask 4020.10
    (at(1, 14, 20), 4030.00),                              # partial 0.05 @ bid 4029.90
    (at(1, 14, 40), 4015.00),                              # rest 0.05 @ bid 4014.90
    (NOW, 4015.00),
]
kt = np.array([k[0] for k in knots]); kv = np.array([k[1] for k in knots])

def mid(t):
    return np.interp(t, kt, kv)

def bid(t): return mid(t) - SPREAD / 2
def ask(t): return mid(t) + SPREAD / 2

def profit(side, vol, p_open, p_close):
    return round((p_close - p_open) * side * vol * CONTRACT, 2)

a_open, a_close = at(2, 12, 30), at(2, 13, 0)
b_open, b_close = at(1, 13, 0), at(1, 13, 30)
c_open, c_p1, c_p2 = at(1, 14, 0), at(1, 14, 20), at(1, 14, 40)
DEALS = [
    Deal(1, 11, "XAUUSDm", 0, 0, 0.10, ask(a_open), 0, 0, 0, 0, 0, int(a_open * 1000)),
    Deal(2, 11, "XAUUSDm", 1, 1, 0.10, bid(a_close),
         profit(1, 0.10, ask(a_open), bid(a_close)), 0, 0, 0, 0, int(a_close * 1000)),
    Deal(3, 12, "XAUUSDm", 1, 0, 0.05, bid(b_open), 0, 0, 0, 0, 0, int(b_open * 1000)),
    Deal(4, 12, "XAUUSDm", 0, 1, 0.05, ask(b_close),
         profit(-1, 0.05, bid(b_open), ask(b_close)), 0, 0, 0, 0, int(b_close * 1000)),
    Deal(5, 13, "XAUUSDm", 0, 0, 0.10, ask(c_open), 0, 0, 0, 0, 0, int(c_open * 1000)),
    Deal(6, 13, "XAUUSDm", 1, 1, 0.05, bid(c_p1),
         profit(1, 0.05, ask(c_open), bid(c_p1)), 0, 0, 0, 0, int(c_p1 * 1000)),
    Deal(7, 13, "XAUUSDm", 1, 1, 0.05, bid(c_p2),
         profit(1, 0.05, ask(c_open), bid(c_p2)), 0, 0, 0, 0, int(c_p2 * 1000)),
    Deal(8, 14, "XAUUSDm", 0, 0, 0.01, ask(c_open), 0, 0, 0, 0, 555001, int(c_open * 1000)),  # a bot trade
    Deal(9, 0, "", 2, 0, 0, 0, 100.0, 0, 0, 0, 0, int(a_open * 1000) - 5000),                 # balance row
]

fake = types.SimpleNamespace(
    ORDER_TYPE_BUY=0, DEAL_TYPE_BUY=0, DEAL_TYPE_SELL=1,
    DEAL_ENTRY_IN=0, DEAL_ENTRY_OUT=1, DEAL_ENTRY_OUT_BY=3, COPY_TICKS_ALL=-1,
    initialize=lambda: True, shutdown=lambda: None, last_error=lambda: (0, "ok"),
    account_info=lambda: types.SimpleNamespace(login=425386147, server="Exness-MT5Real15",
                                               currency="USD", equity=123.45),
    history_deals_get=lambda f, t: list(DEALS),
    symbol_info_tick=lambda s: types.SimpleNamespace(time=int(time.time())),
    order_calc_profit=lambda ty, s, v, p0, p1: (p1 - p0) * v * CONTRACT,
)
def copy_ticks_range(sym, d0, d1, flags):
    t = np.arange(d0.timestamp(), d1.timestamp(), 1.0)
    arr = np.zeros(len(t), dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8")])
    arr["time_msc"] = (t * 1000).astype(np.int64)
    arr["bid"], arr["ask"] = bid(t), ask(t)
    return arr
fake.copy_ticks_range = copy_ticks_range
D.mt5 = fake

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = D.main(["--days", "5", "--targets", "20,50", "--loss-stops", "0,20"])
out = buf.getvalue()
full = open(D.OUT_FILE, encoding="utf-8").read()
print(out)

fails = []
def check(cond, msg):
    (print(f"  PASS  {msg}") if cond else fails.append(msg))

def grid_cell(profit_label, col):
    """read the summary grid: row by label, col 0 = no loss stop, 1 = -20"""
    for l in out.splitlines():
        parts = l.split()
        if parts and parts[0] == profit_label and len(parts) >= 3:
            try:
                return float(parts[1 + col])
            except ValueError:
                pass
    return None

check(rc == 0, "exit code 0")
check("manual trades only" in out, "bot trade (magic 555001) excluded by default")
# long report goes to the FILE only; the screen gets the one-screen summary
check("LATEST DAY" not in out and "LATEST DAY" in full, "long report -> file only by default")
check("+58.00" in out and "-42.00" in out, "summary day A best +58.00, ended -42.00")
check("+17.00" in out and "-83.00" in out and "-58.00" in out, "summary merged evening best +17, worst -83, ended -58")
check("-81.00" in full and "+23.00" in full, "file keeps per-trade detail (-81.00, partial +23.00)")
check("as traded: -100.00" in out, "as traded -100.00")

g = {k: grid_cell(*k) for k in [("none", 0), ("none", 1), ("+20", 0), ("+50", 0), ("+50", 1)]}
print("  grid read:", g)
close = lambda a, b, tol=0.15: a is not None and abs(a - b) <= tol
check(close(g[("none", 0)], -100.00), "grid none/none = -100.00 (as traded)")
check(close(g[("+20", 0)], -38.00),  "grid +20/none = -38 (A banks +20, evening never reaches +20)")
check(close(g[("+50", 0)], -8.00),   "grid +50/none = -8")
check(close(g[("none", 1)], -40.00), "grid none/-20 = -40 (both days cut at -20)")
check(close(g[("+50", 1)], +30.00),  "grid +50/-20 = +30 (A reaches +50 before -20; evening cut at -20)")

def group_row(prefix):
    for l in out.splitlines():
        if l.strip().startswith(prefix):
            return l.split()
    return None
flat, down, up = group_row("flat"), group_row("DOWN"), group_row("UP")
check(flat is not None and flat[1] == "2" and "-123.00" in flat, "flat bucket: A and B, total -123.00")
check(down is not None and "+23.00" in " ".join(down), "DOWN bucket: C opened while day was -81 -> +23.00")
check(up is not None and up[-1] == "0", "UP bucket empty in this scenario")

# MT5 returning None must be an error, not 'no trades'
fake.history_deals_get = lambda f, t: None
buf2 = io.StringIO()
with contextlib.redirect_stdout(buf2):
    rc2 = D.main(["--days", "5"])
check(rc2 == 2 and "CANNOT TELL" in buf2.getvalue(), "history_deals_get None -> error, not empty")

print("\nFAILED:" if fails else "\nALL CHECKS PASSED")
for f in fails:
    print("  FAIL ", f)
sys.exit(1 if fails else 0)
