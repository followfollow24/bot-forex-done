"""Tests for scalp_core -- pure logic, synthetic ticks, no MT5."""
import random, sys
from datetime import datetime, timezone
sys.path.insert(0, ".")
import scalp_core as S

fails = []
def ok(c, m):
    print(f"  {'OK  ' if c else 'FAIL'} {m}")
    if not c: fails.append(m)

def utc(h, m, s=0, day=14):
    return datetime(2026, 9, day, h, m, s, tzinfo=timezone.utc).timestamp()

print("\nscalp_core tests")
# signal
ok(S.candle_signal(100, 108, "fade", 6) == -1, "fade: +8 candle -> sell")
ok(S.candle_signal(100, 92, "fade", 6) == 1, "fade: -8 candle -> buy")
ok(S.candle_signal(100, 108, "follow", 6) == 1, "follow: +8 candle -> buy")
ok(S.candle_signal(100, 105, "fade", 6) == 0, "below min_move -> nothing")

# exits pay the spread
r, px = S.exit_reason(1, 100.2, 101.2, 101.4, 1.0, 5.0, 10, 600)
ok(r == "TP" and px == 101.2, "long TP measured at the BID")
r, px = S.exit_reason(-1, 100.0, 104.8, 105.0, 1.0, 5.0, 10, 600)
ok(r == "SL" and px == 105.0, "short SL measured at the ASK")
r, _ = S.exit_reason(1, 100.2, 100.3, 100.5, 1.0, 5.0, 600, 600)
ok(r == "TIME", "time stop at max hold")

# session in Thai time
ok(S.in_session(utc(12, 30), "19:30-21:30"), "12:30 UTC = 19:30 Thai is inside")
ok(not S.in_session(utc(14, 30), "19:30-21:30"), "14:30 UTC = 21:30 Thai is outside")
ok(not S.in_session(utc(12, 29, 59), "19:30-21:30"), "19:29:59 Thai is outside")

# day guard
g = S.DayGuard(50, 30, 3)
g.roll("d1"); g.opened(); g.closed(20); g.opened(); g.closed(20)
ok(g.check(5) == "" and g.can_open(), "+45 total: not stopped yet")
ok(g.check(10) == "PROFIT" and not g.can_open(), "+50 with open P&L -> PROFIT stop, no more entries")
g.roll("d2")
ok(g.can_open() and g.realized == 0 and g.trades == 0, "new Thai day resets the guard")
g.opened(); g.closed(-31)
ok(g.check() == "LOSS", "-31 realized -> LOSS stop")
g.roll("d3"); g.opened(); g.opened(); g.opened()
ok(not g.can_open() and g.check() == "", "max_trades caps entries without being a stop")

# ---- replay on a synthetic evening ----------------------------------------
def evening(spread_at_2nd=0.2):
    """1 tick/sec, 12:25-13:10 UTC. Candle 12:30-12:35 rises +8 (-> fade sell),
    then price falls 2 points in 60s (short TP). Candle 12:45-12:50 rises +8
    again but the spread at its close is wide."""
    T, B, A = [], [], []
    t, px = utc(12, 25), 4000.0
    while t < utc(13, 10):
        if utc(12, 30) <= t < utc(12, 35):
            px += 8.0 / 300
        elif utc(12, 35) <= t < utc(12, 36):
            px -= 2.0 / 60
        elif utc(12, 45) <= t < utc(12, 50):
            px += 8.0 / 300
        sp = 0.2
        if utc(12, 50) <= t < utc(12, 50, 10):
            sp = spread_at_2nd
        T.append(t); B.append(round(px, 3)); A.append(round(px + sp, 3))
        t += 1.0
    return T, B, A

p = S.Params(mode="fade", min_move=6, tp=1.0, sl=5.0, max_hold_min=10,
             session="19:30-21:30", max_spread=0.35, profit_stop=50,
             loss_stop=30, max_trades=10, usd_per_point=1.0)
T, B, A = evening(spread_at_2nd=0.2)
sig = S.m5_signals(T, B, p)
ok([d for _, d in sig] == [-1, -1], f"two fade-sell signals found: {sig and [(datetime.fromtimestamp(t, timezone.utc).strftime('%H:%M'), d) for t, d in sig]}")
tr = S.replay(T, B, A, p)
ok(len(tr) == 2, f"two trades taken (got {len(tr)})")
ok(tr and tr[0]["side"] == -1 and tr[0]["reason"] == "TP" and 1.0 <= tr[0]["points"] < 1.1,
   f"first: short, TP, ~+1 point (got {tr and (tr[0]['reason'], round(tr[0]['points'], 3))})")
ok(tr and tr[0]["entry"] == B[T.index(utc(12, 35))], "entered at the bid of the first tick after the candle closed")

T2, B2, A2 = evening(spread_at_2nd=0.6)
tr2 = S.replay(T2, B2, A2, p)
ok(len(tr2) == 1, "wide spread at the second signal -> skipped")

# no signal uses a candle that is still forming: truncating right at 12:35:00
cut = T.index(utc(12, 35))
ok(S.m5_signals(T[:cut], B[:cut], p) == [], "a candle is only judged once a later tick proves it closed")

# day guard inside replay: $10/pt. Trade 1 = short TP +1pt (+$10); trade 2 = the
# second fade sells into a flat market and times out at -0.2pt (the spread, -$2).
ok(tr and tr[1]["reason"] == "TIME" and abs(tr[1]["points"] + 0.2) < 1e-6,
   "second trade: flat market -> time stop, loses exactly the spread (-0.2)")
p2 = S.Params(**{**p.__dict__, "usd_per_point": 10.0, "profit_stop": 15.0})
tr3 = S.replay(T, B, A, p2)
ok(len(tr3) == 2 and abs(sum(x["usd"] for x in tr3) - 8.0) < 1e-6,
   "profit stop +15 never reached (+10, -2) -> both trades, day +8")
p3 = S.Params(**{**p.__dict__, "usd_per_point": 10.0, "profit_stop": 5.0})
tr4 = S.replay(T, B, A, p3)
ok(len(tr4) == 1 and tr4[0]["reason"] == "DAY_PROFIT" and abs(tr4[0]["usd"] - 5.0) < 1e-6,
   "profit stop +5 counts OPEN profit: closes at +$5 before TP, no second entry")
p4 = S.Params(**{**p.__dict__, "usd_per_point": 10.0, "loss_stop": 1.5, "profit_stop": 0})
tr5 = S.replay(T, B, A, p4)
ok(len(tr5) == 1 and tr5[0]["reason"] == "DAY_LOSS" and -2.0 <= tr5[0]["usd"] <= -1.5,
   "the spread alone (-$2 at $10/pt) trips a -$1.50 loss stop on the next tick")

# random-side control keeps timing
trr = S.replay(T, B, A, p, random_side=True, rng=random.Random(1))
ok(len(trr) == 2 and [x["open_t"] for x in trr] == [x["open_t"] for x in tr], "coin-flip control uses identical entry times")

print("\nFAILED:" if fails else "\nALL CORE TESTS PASSED")
for f in fails: print("  -", f)
sys.exit(1 if fails else 0)
