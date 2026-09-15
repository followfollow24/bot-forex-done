#!/usr/bin/env python3
"""scalp_bot.py -- gold scalper for the 19:30 Thai window.

PAPER MODE BY DEFAULT. Real orders only with --allow-real, which the
operator types, never an assistant.

The rule lives in scalp_core.py and is the SAME code _scalp_replay.py
measured. Replays on the broker's own XAUUSDm ticks (86 evenings,
2026-05-18 .. 2026-09-14), 0.01 lot:

    default (fade, 19:30-21:30): 468 trades, 79% won, -0.239 pts/trade,
        -$112, no better than a coin (beats 44%)
    follow, 19:40-20:00, 19:30 gate 5: 37 trades, 97% won, +$33.72 --
        but -0.70 pts/trade over 253 trades on 13 years of M5 gold

Paper mode exists so that can be watched on live prices without paying for
it. What the bot does, every quarter-second:
  1. manage the open position: TP / SL / time stop, and the day guard
     (+profit / -loss for the Thai day, counting open profit)
  2. when a new 5-minute candle has just closed inside the session and it
     moved >= min_move points: fade it (or follow it), one position at a
     time, only if the spread is tight and the guard allows
Live orders carry the stop and take-profit ON THE ORDER, so they still
work if this process dies. Kill switch: create a file named STOP_SCALP.

    python scalp_bot.py                       # paper, defaults
    python scalp_bot.py --lot 0.01 --allow-real
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import os
import sys
import time
import traceback
from datetime import datetime

import scalp_core as S

try:
    import MetaTrader5 as mt5
except Exception:                                   # a fake is injected in tests
    mt5 = None

MAGIC = 668004
KILL_FILE = "STOP_SCALP"
LOG_FILE = "scalp_bot.log"
TRADES_CSV = "scalp_trades.csv"
POLL_SEC = 0.25
_HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATHS = (
    os.path.join(_HERE, ".env"),
    os.path.join(os.path.dirname(_HERE), ".env"),
    os.path.expanduser("~/.env"),
)


def claim_single_instance() -> bool:
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW(None, False, "Global\\scalp_bot_668004")
        return k32.GetLastError() != 183
    except Exception:
        return True


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def telegram(msg: str) -> None:
    """Best effort; a notification failure never blocks trade management."""
    try:
        import urllib.parse
        import urllib.request
        token = chat = None
        for path in ENV_PATHS:
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    if "=" not in raw or raw.strip().startswith("#"):
                        continue
                    k, v = raw.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TELEGRAM_BOT_TOKEN":
                        token = v
                    elif k == "TELEGRAM_CHAT_ID":
                        chat = v
        if not token or not chat:
            return
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
    except Exception:
        pass


def try_send(req: dict):
    """order_send with a filling-mode fallback (10030 = invalid fill)."""
    res = None
    for fill in (req.get("type_filling", mt5.ORDER_FILLING_IOC),
                 mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
        req["type_filling"] = fill
        try:
            res = mt5.order_send(req)
        except Exception as exc:
            log(f"  order_send raised: {exc!r}")
            return None
        if res is None or res.retcode != 10030:
            return res
    return res


def positions_of(sym: str):
    """Our positions, or None when MT5 cannot say. None is UNKNOWN, never
    'flat' -- MT5 reports errors by returning None, not by raising."""
    try:
        res = mt5.positions_get(symbol=sym)
    except Exception as exc:
        log(f"  positions_get raised: {exc!r}")
        return None
    if res is None:
        return None
    return [p for p in res if getattr(p, "magic", None) == MAGIC]


def deal_result(ticket: int):
    """Realized money for a closed position, or None if unreadable."""
    try:
        deals = mt5.history_deals_get(position=ticket)
    except Exception:
        return None
    if deals is None:
        return None
    outs = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
    if not outs:
        return None
    return float(sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
                     for d in outs))


class Bot:
    def __init__(self, a):
        self.a = a
        self.live = bool(a.allow_real)
        self.tag = "LIVE" if self.live else "PAPER"
        self.p = S.Params(**{k: getattr(a, k) for k in S.Params().__dict__
                             if k != "usd_per_point"})
        self.guard = S.DayGuard(self.p.profit_stop, self.p.loss_stop, self.p.max_trades)
        self.sym = a.symbol
        self.off_h = 0
        self.pos = None                 # dict(side, entry, t0, ticket)
        self.last_bar = None
        self.unknown_since = None
        self.stopped_logged = ""
        self.gate = {}                  # thai date -> first session candle big enough

    # -- startup ---------------------------------------------------------
    def setup(self) -> bool:
        if mt5 is None:
            log("[ERROR] needs MetaTrader5 -- run on the VPS"); return False
        if not mt5.initialize():
            log(f"[ERROR] MT5 init failed: {mt5.last_error()}"); return False
        if not mt5.symbol_select(self.sym, True):
            log(f"[ERROR] symbol {self.sym} not available"); return False
        tk = mt5.symbol_info_tick(self.sym)
        if tk is None:
            log("[ERROR] no quote -- market closed?"); return False
        upp = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, self.sym, self.a.lot,
                                    tk.ask, tk.ask + 1.0)
        if not upp or upp <= 0:
            log("[ERROR] broker will not price a point for this lot"); return False
        self.p.usd_per_point = float(upp)
        self.off_h = round((tk.time - time.time()) / 3600.0) \
            if abs(tk.time - time.time()) < 12 * 3600 else 0
        acct = mt5.account_info()
        log("=" * 70)
        log(f" SCALP BOT  [{self.tag}]  {self.sym}  lot {self.a.lot}"
            f"  (${self.p.usd_per_point:.2f}/point)  acct {getattr(acct, 'login', '?')}")
        log(f" {self.p.mode} >= {self.p.min_move:g} pts | TP {self.p.tp:g} SL {self.p.sl:g}"
            f" | hold {self.p.max_hold_min:g}m | {self.p.session} Thai | spread <= {self.p.max_spread:g}")
        gate_at = self.p.gate_at or self.p.session.split("-")[0]
        log(f" day gate: {('%s candle >= %g pts' % (gate_at, self.p.day_gate)) if self.p.day_gate > 0 else 'off (every day)'}")
        log(f" day guard +{self.p.profit_stop:g} / -{self.p.loss_stop:g}"
            f"  max {self.p.max_trades} trades | broker UTC{self.off_h:+d} | kill: {KILL_FILE}")
        if not self.live:
            log(" PAPER MODE -- no order will be sent")
        log(" measure these exact settings first: _scalp_replay.py with the same flags,")
        log(" and treat a replay window you chose from recent charts as unproven")
        log("=" * 70)
        bars = mt5.copy_rates_from_pos(self.sym, mt5.TIMEFRAME_M5, 1, 1)
        if bars is not None and len(bars):
            self.last_bar = int(bars[0]["time"])   # never act on a candle from before start
        telegram(f"scalp_bot [{self.tag}] started {self.sym} lot {self.a.lot}")
        return True

    # -- one poll --------------------------------------------------------
    def step(self) -> None:
        tk = mt5.symbol_info_tick(self.sym)
        if tk is None:
            return
        t = tk.time_msc / 1000.0 - self.off_h * 3600
        if self.guard.roll(int((t + S.THAI.total_seconds()) // 86400)):
            self.stopped_logged = ""
        kill = os.path.exists(KILL_FILE)

        if self.pos is not None:
            self._manage(tk, t, kill)

        if self.guard.stopped and self.stopped_logged != self.guard.stopped:
            self.stopped_logged = self.guard.stopped
            msg = (f"DAY STOP {self.guard.stopped}: day ${self.guard.realized:+.2f}"
                   f" after {self.guard.trades} trades -- no more entries until Thai midnight")
            log(f"[{self.tag}] {msg}")
            telegram(f"scalp_bot [{self.tag}] {msg}")

        bars = mt5.copy_rates_from_pos(self.sym, mt5.TIMEFRAME_M5, 1, 1)
        if bars is None or not len(bars):
            return
        bar_t = int(bars[0]["time"])
        if bar_t == self.last_bar:
            return
        self.last_bar = bar_t
        close_utc = bar_t - self.off_h * 3600 + 300
        if self.p.day_gate > 0 and S.is_gate_candle(close_utc, self.p):
            move = abs(float(bars[0]["close"]) - float(bars[0]["open"]))
            today = S.thai_date(close_utc - 1)
            self.gate[today] = move >= self.p.day_gate
            verdict = "OPEN -- trading today" if self.gate[today] else "SHUT -- no trades today"
            log(f"[{self.tag}] day gate: first candle moved {move:.2f} pts"
                f" (need {self.p.day_gate:g}) -> {verdict}")
            gate_at = self.p.gate_at or self.p.session.split("-")[0]
            telegram(f"scalp_bot [{self.tag}] {gate_at} candle {move:.2f} pts -> {verdict}")
        if self.p.day_gate > 0 and not self.gate.get(S.thai_date(close_utc - 1), False):
            return
        if kill or self.pos is not None or not self.guard.can_open():
            return
        if t - close_utc > 5.0 or not S.in_session(close_utc - 1, self.p.session):
            return
        d = S.candle_signal(float(bars[0]["open"]), float(bars[0]["close"]),
                            self.p.mode, self.p.min_move)
        if not d:
            return
        if tk.ask - tk.bid > self.p.max_spread:
            log(f"  signal {d:+d} skipped: spread {tk.ask - tk.bid:.2f}")
            return
        self._open(d, tk, t)

    # -- entries ---------------------------------------------------------
    def _open(self, side, tk, t) -> None:
        price = tk.ask if side > 0 else tk.bid
        name = "BUY" if side > 0 else "SELL"
        if not self.live:
            self.pos = dict(side=side, entry=price, t0=t, ticket=None)
            self.guard.opened()
            log(f"[PAPER] OPEN {name} @ {price:.3f}")
            telegram(f"scalp_bot [PAPER] open {name} @ {price:.3f}")
            return
        info = mt5.symbol_info(self.sym)
        acct = mt5.account_info()
        otype = mt5.ORDER_TYPE_BUY if side > 0 else mt5.ORDER_TYPE_SELL
        need = mt5.order_calc_margin(otype, self.sym, self.a.lot, price)
        if info is None or acct is None or need is None or need > acct.margin_free * 0.5:
            log(f"  entry refused: margin need {need} vs free {getattr(acct, 'margin_free', '?')}")
            return
        held = positions_of(self.sym)
        if held is None or held:
            log("  entry refused: positions unreadable or one already open")
            return
        dg = info.digits
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": self.sym, "volume": self.a.lot,
            "type": otype, "price": price,
            "sl": round(price - side * self.p.sl, dg),
            "tp": round(price + side * self.p.tp, dg),
            "deviation": 20, "magic": MAGIC, "comment": "scalp",
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = try_send(req)
        if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(res, "retcode", None)
            hint = "  <- AutoTrading OFF" if code == 10027 else ""
            log(f"  ORDER REJECTED retcode={code}{hint}")
            telegram(f"scalp_bot [LIVE] order rejected retcode={code}{hint}")
            return
        held = positions_of(self.sym) or []
        mine = sorted(held, key=lambda q: getattr(q, "time_msc", 0))
        ticket = mine[-1].ticket if mine else getattr(res, "order", None)
        entry = float(mine[-1].price_open) if mine else float(getattr(res, "price", price))
        self.pos = dict(side=side, entry=entry, t0=t, ticket=ticket)
        self.guard.opened()
        log(f"[LIVE] OPEN {name} {self.a.lot} @ {entry:.3f} ticket {ticket}"
            f"  SL {req['sl']} TP {req['tp']}")
        telegram(f"scalp_bot [LIVE] open {name} {self.a.lot} @ {entry:.3f}")

    # -- exits -----------------------------------------------------------
    def _manage(self, tk, t, kill) -> None:
        side, entry, t0 = self.pos["side"], self.pos["entry"], self.pos["t0"]
        reason, px = S.exit_reason(side, entry, tk.bid, tk.ask, self.p.tp, self.p.sl,
                                   t - t0, self.p.max_hold_min * 60.0)
        if not self.live:
            open_usd = (px - entry) * side * self.p.usd_per_point
            if not reason and self.guard.check(open_usd):
                reason = "DAY_" + self.guard.stopped
            if not reason and kill:
                reason = "KILL"
            if reason:
                self._record(reason, px, (px - entry) * side * self.p.usd_per_point)
            return

        held = positions_of(self.sym)
        if held is None:
            if self.unknown_since is None:
                self.unknown_since = t
                log("  CANNOT READ POSITIONS -- holding state, NOT assuming closed")
            if t - self.unknown_since > 60:
                telegram("scalp_bot [LIVE] positions unreadable for 60s -- CHECK THE TERMINAL")
                self.unknown_since = t
            return
        self.unknown_since = None
        mine = [q for q in held if q.ticket == self.pos["ticket"]]
        if not mine:                                  # broker TP / SL fired
            money = deal_result(self.pos["ticket"])
            if money is None:
                money = (px - entry) * side * self.p.usd_per_point
                log("  closed by broker; deal unreadable -- using last price estimate")
            # the broker filled at its own TP/SL price, not at this tick --
            # derive the points from the realized money so the log agrees
            self._record("BROKER_TP_SL", px, money,
                         pts=money / self.p.usd_per_point if self.p.usd_per_point else None)
            return
        q = mine[0]
        if not reason and self.guard.check(float(q.profit)):
            reason = "DAY_" + self.guard.stopped
        if not reason and kill:
            reason = "KILL"
        if not reason:
            return
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": self.sym, "volume": q.volume,
            "type": mt5.ORDER_TYPE_SELL if side > 0 else mt5.ORDER_TYPE_BUY,
            "position": q.ticket, "price": px, "deviation": 20, "magic": MAGIC,
            "comment": "scalp_exit", "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        ok = False
        for attempt in range(3):
            res = try_send(req)
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                ok = True
                break
            log(f"  close attempt {attempt + 1}/3 failed retcode={getattr(res, 'retcode', None)}")
        if not ok:
            telegram(f"scalp_bot [LIVE] FAILED TO CLOSE ticket {q.ticket} -- close it by hand")
            return                                    # keep managing; do not forget it
        money = deal_result(q.ticket)
        if money is None:
            money = float(q.profit)
        self._record(reason, px, money)

    def _record(self, reason, px, money, pts=None) -> None:
        side, entry, t0 = self.pos["side"], self.pos["entry"], self.pos["t0"]
        if pts is None:
            pts = (px - entry) * side
        else:
            px = entry + side * pts
        self.guard.closed(money)
        self.guard.check(0.0)
        log(f"[{self.tag}] CLOSE {reason} {pts:+.2f} pts  ${money:+.2f}"
            f"  | day ${self.guard.realized:+.2f} ({self.guard.trades} trades)")
        telegram(f"scalp_bot [{self.tag}] {reason} {pts:+.2f} pts ${money:+.2f}"
                 f" | day ${self.guard.realized:+.2f}")
        try:
            new = not os.path.exists(TRADES_CSV)
            with open(TRADES_CSV, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["opened_utc", "mode", "side", "entry", "exit",
                                "points", "money", "reason", "day_total"])
                w.writerow([f"{t0:.3f}", self.tag, side, f"{entry:.3f}", f"{px:.3f}",
                            f"{pts:.3f}", f"{money:.2f}", reason, f"{self.guard.realized:.2f}"])
        except OSError:
            pass
        self.pos = None


def build_parser():
    base = S.Params()
    ap = argparse.ArgumentParser(description="gold scalper, paper by default")
    ap.add_argument("--symbol", default="XAUUSDm")
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--allow-real", action="store_true",
                    help="send REAL orders (default: paper)")
    for k, v in base.__dict__.items():
        if k == "usd_per_point":
            continue
        ap.add_argument("--" + k.replace("_", "-"), type=type(v), default=v)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.lot <= 0 or a.sl <= 0 or a.tp <= 0:
        print("[ERROR] lot, sl and tp must be positive"); return 2
    if not claim_single_instance():
        print("[ERROR] scalp_bot is already running"); return 2
    bot = Bot(a)
    if not bot.setup():
        return 2
    try:
        while True:
            try:
                bot.step()
            except Exception:
                log("  step error:\n" + traceback.format_exc())
            if os.path.exists(KILL_FILE) and bot.pos is None:
                log("kill switch present and flat -- exiting")
                break
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log("stopped by keyboard" + (" -- A POSITION MAY STILL BE OPEN" if bot.pos else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
