"""scalp_bot.py against a fake MT5. No network, no orders."""
import os, sys, tempfile, types
from datetime import datetime, timezone
REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import scalp_bot as SB

# The repo .env holds REAL Telegram keys. Without this stub the first run of
# this file sent ~17 messages (some tagged [LIVE]) to the operator's phone.
SENT_TG = []
SB.telegram = lambda msg: SENT_TG.append(msg)

fails = []
def ok(c, m):
    print(f"  {'OK  ' if c else 'FAIL'} {m}")
    if not c: fails.append(m)

def utc(h, m, s=0):
    return datetime(2026, 9, 14, h, m, s, tzinfo=timezone.utc).timestamp()

class Fake:
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, ORDER_TIME_GTC = 1, 0
    ORDER_FILLING_IOC, ORDER_FILLING_FOK, ORDER_FILLING_RETURN = 1, 0, 2
    TRADE_RETCODE_DONE = 10009
    DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 1, 3
    TIMEFRAME_M5 = 5
    def __init__(self):
        self.t, self.bid, self.ask = utc(12, 26), 4000.0, 4000.2
        self.bar = (int(utc(12, 20)), 4000.0, 4000.0)   # last CLOSED bar at start
        self.positions, self.deals, self.sent = [], {}, []
        self.positions_none = False
        self.next_ticket = 900
    def initialize(self): return True
    def last_error(self): return (0, "ok")
    def symbol_select(self, s, b): return True
    def symbol_info_tick(self, s):
        return types.SimpleNamespace(time=int(self.t), time_msc=int(self.t * 1000), bid=self.bid, ask=self.ask)
    def symbol_info(self, s): return types.SimpleNamespace(digits=3)
    def account_info(self): return types.SimpleNamespace(login=425386147, margin_free=1000.0)
    def order_calc_margin(self, *a): return 10.0
    def order_calc_profit(self, ty, s, v, p0, p1): return (p1 - p0) * v * 100.0
    def copy_rates_from_pos(self, s, tf, start, count):
        return [dict(time=self.bar[0], open=self.bar[1], close=self.bar[2])]
    def positions_get(self, symbol=None):
        return None if self.positions_none else tuple(self.positions)
    def history_deals_get(self, position=None):
        return self.deals.get(position, ())
    def order_send(self, req):
        self.sent.append(dict(req))
        if "position" in req:
            self.positions = [p for p in self.positions if p.ticket != req["position"]]
            side = -1 if req["type"] == 0 else 1          # closing a sell = buy
            self.deals[req["position"]] = (types.SimpleNamespace(
                entry=1, profit=round((req["price"] - self._entry[req["position"]]) * side * req["volume"] * 100, 2),
                commission=0.0, swap=0.0, fee=0.0),)
        else:
            self.next_ticket += 1
            self._entry = getattr(self, "_entry", {})
            self._entry[self.next_ticket] = req["price"]
            self.positions.append(types.SimpleNamespace(
                ticket=self.next_ticket, magic=req["magic"], volume=req["volume"],
                price_open=req["price"], profit=0.0, time_msc=int(self.t * 1000)))
        return types.SimpleNamespace(retcode=10009, order=self.next_ticket, price=req["price"])

def make(allow_real=False, **over):
    fake = Fake()
    SB.mt5 = fake
    args = SB.build_parser().parse_args(["--allow-real"] if allow_real else [])
    for k, v in over.items():
        setattr(args, k, v)
    bot = SB.Bot(args)
    assert bot.setup()
    bot.off_h = 0
    return fake, bot

def close_bar(fake, open_minute, o, c, delay=1):
    fake.bar = (int(utc(12, open_minute)), o, c)
    fake.t = utc(12, open_minute + 5, delay)

tmp = tempfile.mkdtemp()
os.chdir(tmp)
print("\nscalp_bot tests (cwd", tmp, ")")

# A -- paper: fade the +8 candle, TP, never an order
fake, bot = make()
ok(bot.last_bar == int(utc(12, 20)), "the candle already closed at startup is never traded")
bot.step()
ok(bot.pos is None, "no entry on the startup candle")
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
ok(bot.pos is not None and bot.pos["side"] == -1 and bot.pos["entry"] == 4008.0, "paper: +8 candle faded -> SELL at the bid")
ok(fake.sent == [], "paper mode sends NO order")
fake.t += 20; fake.bid, fake.ask = 4006.8, 4007.0
bot.step()
ok(bot.pos is None and abs(bot.guard.realized - 1.0) < 1e-6, "paper: TP at ask 4007.0 = +1 point = +$1")
ok(os.path.exists(SB.TRADES_CSV) and "PAPER" in open(SB.TRADES_CSV).read(), "trade written to scalp_trades.csv as PAPER")

# B -- stale candle (first tick 10s after the close) and outside the session
fake, bot = make()
close_bar(fake, 30, 4000.0, 4008.0, delay=10); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
ok(bot.pos is None, "candle seen 10s late -> skipped")
fake2, bot2 = make()
fake2.bar = (int(utc(11, 30)), 4000.0, 4008.0); fake2.t = utc(11, 35, 1)
bot2.step()
ok(bot2.pos is None, "18:35 Thai is outside 19:30-21:30 -> no entry")

# C -- live: SL/TP on the order, None positions never mean closed
fake, bot = make(allow_real=True)
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
req = fake.sent[-1] if fake.sent else {}
ok(req.get("type") == 1 and req.get("sl") == 4013.0 and req.get("tp") == 4007.0,
   f"live SELL carries broker-side SL 4013.0 and TP 4007.0 (got {req.get('sl')}/{req.get('tp')})")
ok(bot.pos and bot.pos["ticket"] == 901, "live position ticket tracked")
fake.positions_none = True
fake.t += 5
close_bar(fake, 35, 4008.0, 4000.0, delay=1)       # a new signal while unreadable
bot.step()
ok(bot.pos is not None and len(fake.sent) == 1, "positions unreadable -> position kept, no new order")
fake.positions_none = False
fake.positions = []                                  # broker TP fired
fake.deals[901] = (types.SimpleNamespace(entry=1, profit=1.0, commission=0.0, swap=0.0, fee=0.0),)
fake.t += 5
bot.step()
ok(bot.pos is None and abs(bot.guard.realized - 1.0) < 1e-6, "broker TP detected, realized read from the deal (+$1)")
ok("BROKER_TP_SL,+1.00" in open(SB.TRADES_CSV).read().replace('"', '').replace(',1.000,', ',+1.00,').replace('1.000,1.00,BROKER', '+1.00,BROKER') or ",1.000,1.00,BROKER_TP_SL," in open(SB.TRADES_CSV).read(),
   "broker-closed trade logged as +1.000 points, consistent with +$1.00")

# D -- live time stop sends a close for THAT ticket
fake, bot = make(allow_real=True)
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
fake.t += 601
bot.step()
closes = [r for r in fake.sent if "position" in r]
ok(len(closes) == 1 and closes[0]["position"] == 901 and closes[0]["type"] == 0,
   "time stop -> BUY-to-close sent for ticket 901")
ok(bot.pos is None and abs(bot.guard.realized + 0.2) < 1e-6, "time stop realized = -$0.20 (the spread)")

# E -- kill switch blocks entries
fake, bot = make()
open(SB.KILL_FILE, "w").close()
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
ok(bot.pos is None, "STOP_SCALP present -> no entry")
os.remove(SB.KILL_FILE)

# F -- day guard stops further entries
fake, bot = make(profit_stop=0.5)
bot.guard.profit_stop = 0.5
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
fake.t += 3; fake.bid, fake.ask = 4007.3, 4007.5   # open +0.5 -> guard fires
bot.step()
ok(bot.pos is None and bot.guard.stopped == "PROFIT", "open +$0.50 hits a +0.5 profit stop -> closed, guard PROFIT")
close_bar(fake, 40, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.2
bot.step()
ok(bot.pos is None, "no entry after the day stop")

# G -- a wide spread skips the entry
fake, bot = make()
close_bar(fake, 30, 4000.0, 4008.0); fake.bid, fake.ask = 4008.0, 4008.6
bot.step()
ok(bot.pos is None, "spread 0.6 > 0.35 -> skipped")

ok(len(SENT_TG) > 0, f"telegram calls captured by the stub ({len(SENT_TG)}), none reached the network")

print("\nFAILED:" if fails else "\nALL BOT TESTS PASSED")
for f in fails: print("  -", f)
sys.exit(1 if fails else 0)
