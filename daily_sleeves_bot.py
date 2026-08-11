#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_sleeves_bot.py — the two DAILY portfolio sleeves (2026-08-08).

    --sleeve funding : Funding-Contrarian Daily on BTCUSDc + ETHUSDc.
                       Signals from BINANCE (realized funding + 1d klines,
                       UTC); MT5 only fills orders and holds the broker-side
                       hard stop. Verified combo monthly Sharpe 0.94
                       (IS 1.23 / OOS>=2023 1.13).
    --sleeve combo   : BTC Combo LongBias (EMA25/120 + TSMOM40 + Donchian
                       20/10 stop-and-reverse, net-short clipped to 0).
                       Daily bars built from MT5 H1 aggregated on UTC
                       midnights — NEVER broker D1 (broker midnight != UTC).
                       Daily-clock sanity: Sharpe 1.06 / CAGR 44.5% / MAR 0.94.

FROZEN SPECS: newbot_refs/FROZEN_SPECS.md. The signal engines below are pure
functions; _preflight_daily_sleeves.py replays them over the full reference
history and must match newbot_refs/ref_*_signals.csv before any deploy.
DO NOT RE-TUNE any constant here without a new validation round.

FAIL-SAFE POLICY (house rule): a data failure must NEVER create exposure.
Missing/incomplete Binance data or MT5 hiccups => no NEW entries this day;
existing positions are held (funding sleeve: protected by its broker-side
stop). Exits on available data are still honored. Every skipped decision is
alerted, never silent.

Why one file for two sleeves: both are "once per UTC day at 00:0x" bots with
identical infrastructure (heartbeat, kill-switch, state json, Telegram,
MT5 timeout wrapper). The sleeves differ only in signal engine + execution
mapping, kept in two small classes. Watchdog runs two separate processes:
  python daily_sleeves_bot.py --sleeve funding --variant-tag funding_contrarian --risk 0.3 --allow-real
  python daily_sleeves_bot.py --sleeve combo   --variant-tag btc_combo_lb --alloc 0.10 --allow-real
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Magic numbers: 668xxx block is unused by every existing bot
# (555xxx gold / 666xxx btc / 667xxx eth families).
MAGIC = {"funding": 668001, "combo": 668002}

BINANCE_SPOT = "https://api.binance.com/api/v3/klines"
BINANCE_FUND = "https://fapi.binance.com/fapi/v1/fundingRate"

# ═════════════════════════════════════════════════════════════════════════════
#  Pure signal engines — shared verbatim by the live loop AND the preflight.
# ═════════════════════════════════════════════════════════════════════════════

def funding_daily_frame(daily: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    """FROZEN funding-contrarian engine.

    daily   : columns date (datetime64, UTC midnight), open, high, low, close —
              CLOSED UTC days only, ascending.
    funding : columns ts (datetime64), rate — realized 8h prints.

    Returns daily plus f_mean, fema3, fema30, pema200, atr14, bias where bias
    is the GATED target (-1/0/+1) decided at that day's close. Truncation to
    the funding era + 15-row warm-up trim happen HERE so live and preflight
    can't drift apart.
    """
    f = funding.copy()
    f["date"] = f["ts"].dt.floor("D")                    # 00:00 print belongs to its own day
    fm = f.groupby("date")["rate"].mean().rename("f_mean")

    d = daily.copy()
    d = d[d["date"] >= fm.index.min()].reset_index(drop=True)   # funding era only
    d = d.merge(fm, left_on="date", right_index=True, how="left")
    d["f_mean"] = d["f_mean"].ffill()                    # zero-print day inherits previous

    # NOTE adjust=True on all three EMAs (spec) — a recursive EMA won't match.
    d["fema3"]   = d["f_mean"].ewm(span=3,   adjust=True).mean()
    d["fema30"]  = d["f_mean"].ewm(span=30,  adjust=True).mean()
    d["pema200"] = d["close"].ewm(span=200, adjust=True).mean()

    prev_c = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"],
                    (d["high"] - prev_c).abs(),
                    (d["low"] - prev_c).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan                                  # first TR undefined
    d["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    raw = np.where(d["fema3"] < d["fema30"], 1,
          np.where(d["fema3"] > d["fema30"], -1, 0))
    gate_long  = d["close"] > d["pema200"]
    gate_short = d["close"] < d["pema200"]
    d["bias"] = np.where((raw == 1) & gate_long, 1,
                np.where((raw == -1) & gate_short, -1, 0))

    d = d.iloc[15:].reset_index(drop=True)               # warm-up trim (spec)
    return d


def combo_daily_frame(daily_close: pd.Series) -> pd.DataFrame:
    """FROZEN Combo LongBias engine. daily_close indexed by UTC date, ascending.

    Returns DataFrame date, close, sleeve_trend, sleeve_tsmom, sleeve_donch,
    target_frac (exactly 0, 1/3, 2/3 or 1). Donchian sleeve is a path-
    dependent state machine — always REPLAYED over the full series, never
    persisted (restart self-heals by replaying).
    """
    c = daily_close.reset_index(drop=True).astype(float)
    n = len(c)

    ema25  = c.ewm(span=25,  adjust=False).mean()        # recursive, seeded 1st close
    ema120 = c.ewm(span=120, adjust=False).mean()
    a = (ema25 > ema120).astype(float)
    a.iloc[:min(120, n)] = 0.0                           # warm-up: force flat

    b = pd.Series(0.0, index=c.index)
    if n > 40:
        ratio = c / c.shift(40) - 1.0
        b = np.sign(ratio).fillna(0.0)

    hi20 = c.rolling(20).max().shift(1)                  # PRIOR 20 days, excl. today
    lo20 = c.rolling(20).min().shift(1)
    hi10 = c.rolling(10).max().shift(1)
    lo10 = c.rolling(10).min().shift(1)
    p = 0.0
    donch = np.zeros(n)
    for i in range(n):
        if not np.isnan(hi20.iloc[i]):
            ci = c.iloc[i]
            if p <= 0 and ci > hi20.iloc[i]:
                p = 1.0
            elif p >= 0 and ci < lo20.iloc[i]:
                p = -1.0
            elif p == 1.0 and ci < lo10.iloc[i]:
                p = 0.0
            elif p == -1.0 and ci > hi10.iloc[i]:
                p = 0.0
        donch[i] = p

    raw = (a.to_numpy() + b.to_numpy() + donch) / 3.0
    tf = np.maximum(raw, 0.0)
    tf = np.round(tf * 3.0) / 3.0                        # quantize to {0,1/3,2/3,1}

    return pd.DataFrame({
        "date": daily_close.index,
        "close": c.to_numpy(),
        "sleeve_trend": a.to_numpy(),
        "sleeve_tsmom": b.to_numpy(),
        "sleeve_donch": donch,
        "target_frac": tf,
    })


# ═════════════════════════════════════════════════════════════════════════════
#  Binance REST (funding sleeve only) — stdlib urllib, no new dependency
# ═════════════════════════════════════════════════════════════════════════════

def _http_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "daily-sleeves-bot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_binance_daily(symbol: str, days: int = 1300) -> pd.DataFrame:
    """CLOSED UTC daily bars, ascending. Current (forming) day is dropped."""
    rows: list = []
    end = None
    while len(rows) < days:
        q = {"symbol": symbol, "interval": "1d", "limit": 1000}
        if end is not None:
            q["endTime"] = end
        batch = _http_json(f"{BINANCE_SPOT}?{urllib.parse.urlencode(q)}")
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
        if len(batch) < 1000:
            break
    df = pd.DataFrame([{
        "date": pd.Timestamp(r[0], unit="ms"),
        "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]),
    } for r in rows]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    today = pd.Timestamp(datetime.now(timezone.utc).date())
    return df[df["date"] < today].reset_index(drop=True)


def fetch_binance_funding(symbol: str, days: int = 1300) -> pd.DataFrame:
    """Realized funding prints, ascending. ~3/day => days*3 prints, paginated."""
    want = days * 3 + 10
    rows: list = []
    end = None
    while len(rows) < want:
        q = {"symbol": symbol, "limit": 1000}
        if end is not None:
            q["endTime"] = end
        batch = _http_json(f"{BINANCE_FUND}?{urllib.parse.urlencode(q)}")
        if not batch:
            break
        rows = batch + rows
        end = batch[0]["fundingTime"] - 1
        if len(batch) < 1000:
            break
    df = pd.DataFrame([{
        "ts": pd.Timestamp(r["fundingTime"], unit="ms"),
        "rate": float(r["fundingRate"]),
    } for r in rows]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df


def funding_day_complete(funding: pd.DataFrame, day: pd.Timestamp) -> bool:
    """True iff all three 8h prints (00/08/16 UTC) of `day` are present.
    fundingTime stamps carry ms offsets (e.g. 08:00:00.004) — floor, never ==."""
    prints = funding[(funding["ts"] >= day) & (funding["ts"] < day + timedelta(days=1))]
    hours = set(prints["ts"].dt.floor("h").dt.hour)
    return {0, 8, 16}.issubset(hours)


# ═════════════════════════════════════════════════════════════════════════════
#  Bot
# ═════════════════════════════════════════════════════════════════════════════

class DailySleevesBot:
    def __init__(self, cfg: ForexConfig, sleeve: str, variant_tag: str,
                 risk_pct: float, alloc_frac: float, protective_stop_pct: float):
        self.cfg = cfg
        self.sleeve = sleeve
        self.variant_tag = variant_tag
        self.risk_pct = risk_pct              # funding: % equity risked per stop
        self.alloc_frac = alloc_frac          # combo: fraction of equity allocated
        self.protective_stop_pct = protective_stop_pct

        slug = "crypto" if sleeve == "funding" else "btcusdc"
        up = f"{slug.upper()}_{variant_tag.upper()}"
        self.stop_file      = os.path.join(_BASE_DIR, f"STOP_{up}")
        self.heartbeat_file = os.path.join(_BASE_DIR, f"HEARTBEAT_{up}")
        self.state_file     = os.path.join(_BASE_DIR, f"{slug}_{variant_tag}_state.json")
        self.log_file       = os.path.join(_BASE_DIR, f"forex_bot_{slug}_{variant_tag}.log")

        self.log = self._setup_logging()
        self.connector = MT5Connector(cfg, self.log)
        self.executor  = ForexOrderExecutor(self.connector, cfg, self.log)

        # MT5 IPC timeout wrapper — same defense as the H1 bot (hang history)
        self._io = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix=f"mt5-io-{variant_tag}")
        self._mt5_timeout = 90.0

        self.state = self._load_state()
        self.symbols: dict = {}               # canonical -> broker name

    # ── infra ────────────────────────────────────────────────────────────
    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger(f"DailySleeves-{self.variant_tag}")

    def _mt5(self, func, *args, **kw):
        fut = self._io.submit(func, *args, **kw)
        return fut.result(timeout=self._mt5_timeout)

    def _heartbeat(self):
        try:
            with open(self.heartbeat_file, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _telegram(self, msg: str):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat  = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:
            self.log.warning(f"telegram failed: {e}")

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception:
            return {"last_decision_day": "", "positions": {}}

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def _own_positions(self, bsym: Optional[str] = None) -> list:
        magic = MAGIC[self.sleeve]
        pos = [p for p in (self._mt5(self.connector.get_open_positions) or [])
               if int(p.get("magic", 0)) == magic]
        if bsym:
            pos = [p for p in pos if p.get("symbol") == bsym]
        return pos

    # ── main loop ────────────────────────────────────────────────────────
    def run(self):
        if not self._mt5(self.connector.connect):
            self.log.error("MT5 connect failed")
            sys.exit(1)

        # ── Same demo/real safety gate as every other bot in this repo
        #    (forex_live_bot_gold_cwider.py _print_banner_and_verify_demo):
        #    refuse to place real orders on a non-demo account unless
        #    --allow-real was passed explicitly. --dry-run always exempt.
        info = self.connector.get_account_info()
        is_demo = self.connector.is_demo()
        if not self.cfg.dry_run and not is_demo and not self.cfg.allow_real:
            self.log.error(
                "REFUSING TO START: account is NOT confirmed DEMO "
                f"(type={info.get('type', 'UNKNOWN')}) and --allow-real was "
                "not passed. Use --dry-run to test, or --allow-real to "
                "confirm you intend to trade this real account.")
            sys.exit(1)
        acct_tag = "DEMO" if is_demo else ("LIVE (--allow-real)" if self.cfg.allow_real else "UNKNOWN")

        wanted = ["BTCUSDC", "ETHUSDC"] if self.sleeve == "funding" else ["BTCUSDC"]
        for s in wanted:
            self.symbols[s] = self.connector.resolve_symbol(s)
            # [2026-08-11 FIX] cfg.pip_size/pip_value_usd_approx were only
            # ever populated under the canonical key ("BTCUSDC") in main(),
            # but every runtime lookup uses the broker-resolved key (e.g.
            # Exness's actual "BTCUSDc", lowercase c) -- a case-sensitive
            # dict miss that silently fell back to ForexConfig's generic
            # non-crypto defaults. LIVE sizing was unaffected (pip_size
            # cancels out algebraically against get_pip_value_live()'s own
            # pip_size/trade_tick_size term, as long as it's read
            # consistently -- it was), but --dry-run sizing (no
            # get_pip_value_live fallback) would have been ~10,000x off.
            # Populate both keys so lookups are correct under either.
            bsym = self.symbols[s]
            if bsym != s:
                self.cfg.pip_size[bsym] = self.cfg.pip_size.get(s, 1.0)
                self.cfg.pip_value_usd_approx[bsym] = self.cfg.pip_value_usd_approx.get(s, 0.01)
        eq = self._mt5(self.connector.get_equity)
        self.log.info("=" * 70)
        self.log.info(f"  ACCOUNT: {acct_tag}  login={info.get('login','?')}  "
                      f"server={info.get('server','?')}  "
                      f"balance={info.get('balance', eq):.2f} {info.get('currency','USD')}")
        self.log.info(f"  Order mode: {'DRY-RUN (paper, NO real orders)' if self.cfg.dry_run else 'LIVE — places real orders'}")
        self.log.info(f"  DAILY SLEEVE '{self.sleeve}'  variant={self.variant_tag}  "
                      f"magic={MAGIC[self.sleeve]}")
        self.log.info(f"  symbols={self.symbols}  equity={eq:.2f}  "
                      f"risk={self.risk_pct}%  alloc={self.alloc_frac}")
        self.log.info(f"  kill-switch: {os.path.basename(self.stop_file)}")
        self.log.info("=" * 70)
        self._recover()
        self._telegram(f"\U0001F680 START {self.variant_tag} ({self.sleeve}) "
                       f"equity={eq:.2f}")

        while True:
            try:
                self._heartbeat()
                self._watch_positions()
                now = datetime.now(timezone.utc)
                today = now.strftime("%Y-%m-%d")
                if (self.state.get("last_decision_day") != today
                        and now.hour == 0 and 1 <= now.minute <= 50):
                    if os.path.exists(self.stop_file):
                        self.log.info("[KILL-SWITCH] present — skipping decision "
                                      "(existing positions still watched)")
                        # kill-switch is a deliberate operator stop, not a
                        # transient glitch -- do NOT mark last_decision_day,
                        # but also don't spin: the minute-window naturally
                        # caps retries to once/min for at most 50 min/day.
                    else:
                        # [2026-08-11 FIX] this used to set last_decision_day
                        # unconditionally, so a single Binance/MT5 blip inside
                        # _decide() (fetch failure, unmeasured broker offset,
                        # invalid pip_value, etc -- all previously logged as
                        # "fail-safe skip" but silently treated as "day done")
                        # cost the WHOLE day with no retry, even though the
                        # 1-50 minute window was clearly designed to allow
                        # one. Now only a genuinely completed decision (trade
                        # or deliberate no-trade) consumes the day.
                        completed = self._decide(today)
                        if completed:
                            self.state["last_decision_day"] = today
                        else:
                            self.log.warning("[RETRY] decision cycle incomplete "
                                            "(fail-safe skip) -- will retry "
                                            "within today's window")
                        self._save_state()
            except Exception as e:
                self.log.error(f"loop error: {e}")
            time.sleep(60)

    # ── recovery: broker is the source of truth ──────────────────────────
    def _recover(self):
        pos = self._own_positions()
        known = self.state.get("positions", {})
        for p in pos:
            key = p["symbol"]
            if key not in known:
                self.log.warning(f"[RECOVER] untracked position {key} "
                                 f"{p['type']} lot={p['volume']} — adopting")
                self._telegram(f"⚠️ RECOVER {self.variant_tag}: adopted "
                               f"untracked {key} {p['type']} lot={p['volume']}")
        gone = [k for k in known if k not in {p["symbol"] for p in pos}]
        for k in gone:
            self.log.warning(f"[RECOVER] tracked {k} no longer at broker "
                             f"(closed while down) — clearing + alert")
            self._telegram(f"⚠️ RECOVER {self.variant_tag}: {k} closed "
                           f"while bot was down (stop or manual)")
            known.pop(k, None)
        self._save_state()

    def _watch_positions(self):
        """Poll-time detection of broker-side closes (stop hits)."""
        known = self.state.get("positions", {})
        if not known:
            return
        live = {p["symbol"] for p in self._own_positions()}
        for k in list(known):
            if k not in live:
                info = known.pop(k)
                self.log.info(f"[BROKER-CLOSE] {k} closed (stop/manual) — {info}")
                self._telegram(f"\U0001F534 STOP/CLOSE {self.variant_tag}: {k} "
                               f"position closed at broker (entry {info.get('entry')})")
        self._save_state()

    # ── decision dispatch ────────────────────────────────────────────────
    def _decide(self, today: str) -> bool:
        """Returns True iff the day's decision genuinely completed (data was
        available, the logic ran to a real conclusion -- trade or no-trade).
        False means a fail-safe/transient skip that should be RETRIED within
        the same day's decision window, not treated as done."""
        self.log.info(f"── decision cycle {today} ──")
        if self.sleeve == "funding":
            return self._decide_funding()
        else:
            return self._decide_combo()

    # ── funding sleeve ───────────────────────────────────────────────────
    def _decide_funding(self) -> bool:
        all_ok = True
        for canon, binance in (("BTCUSDC", "BTCUSDT"), ("ETHUSDC", "ETHUSDT")):
            bsym = self.symbols[canon]
            try:
                daily = fetch_binance_daily(binance)
                fund  = fetch_binance_funding(binance)
                frame = funding_daily_frame(daily, fund)
            except Exception as e:
                self.log.error(f"[{binance}] data fetch failed ({e}) — "
                               f"NO new entries; existing position kept (has stop)")
                self._telegram(f"⚠️ {self.variant_tag}: {binance} data "
                               f"fetch failed — decision skipped (fail-safe)")
                all_ok = False
                continue
            row = frame.iloc[-1]
            day = row["date"]
            complete = funding_day_complete(fund, day)
            bias = int(row["bias"]) if complete else 0
            self.log.info(f"[{binance}] D={day.date()} f_mean={row['f_mean']:.6f} "
                          f"fema3={row['fema3']:.6f} fema30={row['fema30']:.6f} "
                          f"close={row['close']:.2f} pema200={row['pema200']:.2f} "
                          f"atr14={row['atr14']:.2f} complete={complete} bias={bias:+d}")

            pos = self._own_positions(bsym)
            cur = 0
            if pos:
                cur = 1 if pos[0]["type"] == "BUY" else -1

            if cur != 0 and bias != cur:
                p = pos[0]
                side = "long" if cur == 1 else "short"
                r = self._mt5(self.executor.close_position_market, bsym, side,
                              float(p["volume"]), str(p["id"]),
                              f"{self.variant_tag[:8]}-exit")
                # [2026-08-11 FIX] close_position_market() returns {} (falsy)
                # on a rejected/failed order specifically so the caller can
                # detect it (see forex_executor.py's own comment on this).
                # This call site used to ignore that and unconditionally
                # treat the exit as successful -- a requote/IPC hiccup would
                # leave the OLD position live at the broker while the bot
                # believed itself flat, then fall straight into the entry
                # block below and open a NEW, possibly opposite-direction
                # position on the same symbol: two live positions, only one
                # tracked, Telegram falsely reporting a clean exit. Same risk
                # class as the 2026-08-10 sizing incident, different
                # mechanism. Fix: only untrack/flip on a confirmed close;
                # otherwise skip this symbol's entry decision entirely this
                # cycle and retry next poll (the combo sleeve's own close
                # loop already did this correctly -- see the `if r:` pattern
                # a few dozen lines below).
                if not r:
                    self.log.error(f"[{binance}] EXIT FAILED {side} lot={p['volume']} "
                                   f"-- position still open at broker, NOT untracking. "
                                   f"Will retry within today's decision window.")
                    self._telegram(f"⚠️ {self.variant_tag}: {binance} exit order "
                                   f"FAILED — position still open, will retry "
                                   f"(fail-safe, no new entry this cycle)")
                    all_ok = False
                    continue
                self.log.info(f"[{binance}] EXIT {side} lot={p['volume']} -> {r}")
                self._telegram(f"\U0001F7E1 EXIT {side.upper()} {bsym} "
                               f"({self.variant_tag}) lot={p['volume']}")
                self.state["positions"].pop(canon, None)
                cur = 0

            if cur == 0 and bias != 0 and complete:
                atr = float(row["atr14"])
                if not np.isfinite(atr) or atr <= 0:
                    self.log.warning(f"[{binance}] invalid ATR — skip entry")
                    all_ok = False   # data glitch, retry-worthy
                    continue
                # incomplete-funding entries already excluded via bias=0
                eq = self._mt5(self.connector.get_equity)
                bid, ask = self._mt5(self.connector.get_current_price, bsym)
                px = ask if bias == 1 else bid
                if px <= 0:
                    self.log.warning(f"[{binance}] no price — skip entry")
                    all_ok = False   # MT5/connectivity glitch, retry-worthy
                    continue
                sd = 2.5 * atr
                # [2026-08-10 SIZING BUG FIX] the previous formula
                # ("coins = risk_cash/sd; lot = coins/0.01") assumed a fixed
                # 0.01-coin-per-lot contract with NO real tick-value lookup,
                # so on this CENT account it sized positions ~100x too big
                # (confirmed live: a $174 equity, 0.3%-risk BTC short came
                # out at 1.46 lot / ~$50 max stop-loss instead of ~0.01 lot /
                # ~$0.50 — both funding_contrarian positions were emergency-
                # closed the same day, real loss $13.20). Fixed by using the
                # EXACT same pip_size/pip_value_live pattern the validated H1
                # bots use (_open_position() in forex_live_bot_gold_cwider.py)
                # — get_pip_value_live() reads MT5's real trade_tick_value,
                # which is account-currency-correct for cent accounts
                # automatically; no manual USC/USD conversion needed or safe
                # to hand-roll again.
                pip_size  = self.cfg.get_pip_size(bsym)
                pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
                sd_pips = sd / pip_size
                if sd_pips <= 0 or pip_value <= 0:
                    self.log.warning(f"[{binance}] invalid pip_value — skip entry")
                    all_ok = False   # MT5/connectivity glitch, retry-worthy
                    continue
                risk_cash = eq * self.risk_pct / 100.0
                lot = round(risk_cash / (sd_pips * pip_value), 2)
                if lot < 0.01:
                    self.log.warning(f"[{binance}] lot rounds to 0 — skip entry")
                    continue
                # hard circuit-breaker: independently verify the ACTUAL risk
                # this lot represents is within 1.5x the intended risk before
                # ever sending the order — catches any future unit-mismatch
                # the same way, instead of trusting the formula blindly.
                actual_risk_pct = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")
                if actual_risk_pct > self.risk_pct * 1.5:
                    self.log.error(
                        f"[{binance}] SIZING SANITY CHECK FAILED: lot={lot} implies "
                        f"actual_risk={actual_risk_pct:.2f}% > 1.5x intended "
                        f"{self.risk_pct}% — REFUSING to open (possible bug)")
                    self._telegram(f"⛔ {self.variant_tag}: {binance} sizing sanity "
                                   f"check failed (implied risk {actual_risk_pct:.1f}% "
                                   f"vs intended {self.risk_pct}%) — entry refused")
                    continue
                side = "long" if bias == 1 else "short"
                sl = px - sd if bias == 1 else px + sd
                r = self._mt5(self.executor.open_position, bsym, side, lot,
                              sl, 0.0, f"{self.variant_tag[:8]}-{side}")
                if not r:
                    self.log.error(f"[{binance}] open failed")
                    all_ok = False   # order-send glitch, retry-worthy
                    continue
                fill = float(r.get("fill_price", px) or px)
                # re-anchor the stop to the ACTUAL fill (spec: divergence moves
                # levels, never distances). Failure tolerated: original stop
                # (computed off the requested price) stays.
                sl2 = fill - sd if bias == 1 else fill + sd
                try:
                    self._mt5(self.executor.modify_sl, bsym,
                              str(r.get("trade_id", "")), sl2)
                except Exception as e:
                    self.log.warning(f"[{binance}] stop re-anchor failed ({e}) — "
                                     f"keeping requested-price stop {sl:.2f}")
                    sl2 = sl
                self.state["positions"][canon] = {
                    "entry": fill, "stop": sl2, "lot": lot, "side": side,
                    "entry_day": str(day.date()), "atr": atr}
                self.log.info(f"[{binance}] ENTER {side} lot={lot} fill={fill:.2f} "
                              f"stop={sl2:.2f}")
                self._telegram(f"\U0001F7E2 ENTER {side.upper()} {bsym} "
                               f"({self.variant_tag})\nlot={lot} fill={fill:.2f} "
                               f"stop={sl2:.2f} (2.5xATR14={sd:.2f})")
        self._save_state()
        return all_ok

    # ── combo sleeve ─────────────────────────────────────────────────────
    def _fetch_h1_utc_daily_closes(self, bsym: str) -> Optional[pd.Series]:
        """MT5 H1 -> UTC daily closes. Broker offset measured from the newest
        (forming) bar: H1 bars open on broker-clock whole hours, so
        offset = newest_open_sec - floor(now_utc to hour). Cached hourly;
        measurement is unambiguous away from the exact boundary, so we sample
        only at minute >= 2."""
        candles = self._mt5(self.connector.fetch_ohlcv_paginated,
                            bsym, "1h", 17600)
        if not candles or len(candles) < 3000:
            return None
        now = time.time()
        newest_open = candles[-1][0] / 1000.0
        off = self.state.get("broker_offset_sec")
        if datetime.now(timezone.utc).minute >= 2:
            measured = round((newest_open - (now // 3600) * 3600) / 1800.0) * 1800
            if off is not None and measured != off:
                self.log.warning(f"[offset] broker offset changed {off} -> "
                                 f"{measured} (DST?) — using new value")
                self._telegram(f"⚠️ {self.variant_tag}: broker UTC "
                               f"offset changed {off}->{measured}s")
            off = measured
            self.state["broker_offset_sec"] = off
        if off is None:
            self.log.warning("[offset] not yet measured — skip")
            return None
        rows = [(pd.Timestamp((int(c[0] / 1000) - off), unit="s"), float(c[4]))
                for c in candles
                if c[0] / 1000 - off + 3600 <= now]      # closed (UTC) bars only
        df = pd.DataFrame(rows, columns=["ts_utc", "close"])
        df["date"] = df["ts_utc"].dt.floor("D")
        # daily close = close of the LAST H1 bar of that UTC day (23:00 bar
        # normally; earlier bar if maintenance ate the 23:00 one — spec fallback)
        daily = df.groupby("date")["close"].last()
        today = pd.Timestamp(datetime.now(timezone.utc).date())
        daily = daily[daily.index < today]
        # guard: if >2h of the newest day is missing, treat as data failure
        newest_day_bars = df[df["date"] == daily.index[-1]].shape[0]
        if newest_day_bars < 22:
            self.log.warning(f"[combo] newest day has only {newest_day_bars} H1 "
                             f"bars — skip rebalance (fail-safe)")
            return None
        return daily

    def _decide_combo(self) -> bool:
        bsym = self.symbols["BTCUSDC"]
        daily = self._fetch_h1_utc_daily_closes(bsym)
        if daily is None or len(daily) < 200:
            self._telegram(f"⚠️ {self.variant_tag}: no usable daily "
                           f"series — rebalance skipped (fail-safe)")
            return False   # data glitch, retry-worthy within today's window
        frame = combo_daily_frame(daily)
        row = frame.iloc[-1]
        tf = float(row["target_frac"])
        self.log.info(f"[combo] D={row['date'].date()} close={row['close']:.2f} "
                      f"sleeves=({row['sleeve_trend']:.0f},{row['sleeve_tsmom']:+.0f},"
                      f"{row['sleeve_donch']:+.0f}) target_frac={tf:.4f}")

        eq = self._mt5(self.connector.get_equity)
        bid, ask = self._mt5(self.connector.get_current_price, bsym)
        px = (bid + ask) / 2 if bid > 0 else float(row["close"])
        alloc = eq * self.alloc_frac

        # [2026-08-10 SIZING BUG FIX] same class of bug as the funding sleeve
        # (see the long comment in _decide_funding): "px * 0.01" assumed a
        # fixed 0.01-coin-per-lot contract with no real tick-value lookup.
        # Fixed the same way: notional value of 1.0 lot = px * pip_value
        # (pip_value = account-currency profit per $1 move per lot, read
        # from MT5's real trade_tick_value via get_pip_value_live() — this
        # is exactly what forex_live_bot_gold_cwider.py's proven-safe
        # _open_position() uses, and it self-corrects for cent accounts).
        pip_size  = self.cfg.get_pip_size(bsym)
        pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
        if pip_value <= 0:
            self.log.error("[combo] invalid pip_value — REFUSING to trade")
            self._telegram(f"⛔ {self.variant_tag}: invalid pip_value — not trading")
            return False   # MT5/connectivity glitch, retry-worthy
        lot_notional = (px / pip_size) * pip_value        # account-currency value of 1.0 lot

        # min-lot guard (spec): refuse if a 1/3 step can't be represented
        step_notional = 0.01 * lot_notional               # volume_step 0.01 lot
        if alloc / 3.0 < 2 * step_notional:
            self.log.error(f"[combo] alloc {alloc:.2f} too small for 1/3 steps — "
                           f"REFUSING to trade")
            self._telegram(f"⛔ {self.variant_tag}: alloc too small for 1/3 "
                           f"position steps — not trading")
            return True   # persistent config state, not transient -- retrying
                          # within the hour won't change today's equity/alloc

        target_lots = round(tf * alloc / lot_notional, 2)
        # hard circuit-breaker: independently verify the notional this lot
        # represents is within 1.5x the intended allocation before ever
        # sending an order — catches any future unit-mismatch the same way,
        # instead of trusting the formula blindly.
        actual_notional = target_lots * lot_notional
        if actual_notional > alloc * 1.5:
            self.log.error(
                f"[combo] SIZING SANITY CHECK FAILED: target_lots={target_lots} implies "
                f"notional={actual_notional:.2f} > 1.5x intended alloc={alloc:.2f} "
                f"— REFUSING to trade (possible bug)")
            self._telegram(f"⛔ {self.variant_tag}: sizing sanity check failed "
                           f"(notional {actual_notional:.2f} vs alloc {alloc:.2f}) "
                           f"— rebalance refused")
            return True   # a real bug, not a transient glitch -- retrying the
                          # identical computation within the hour won't help

        own = self._own_positions(bsym)
        cur_lots = round(sum(float(p["volume"]) for p in own
                             if p["type"] == "BUY"), 2)
        delta = round(target_lots - cur_lots, 2)
        self.log.info(f"[combo] equity={eq:.2f} alloc={alloc:.2f} px={px:.2f} "
                      f"target={target_lots} current={cur_lots} delta={delta:+.2f}")
        if abs(delta) < 0.01:
            self.log.info("[combo] delta below volume step — hold")
            return True

        if delta > 0:
            sl = px * (1 - self.protective_stop_pct / 100.0)   # catastrophe stop
            r = self._mt5(self.executor.open_position, bsym, "long", delta,
                          sl, 0.0, f"{self.variant_tag[:8]}-add")
            if r:
                fill = float(r.get("fill_price", px) or px)
                self.log.info(f"[combo] BUY {delta} fill={fill:.2f} "
                              f"protective-stop={sl:.2f}")
                self._telegram(f"\U0001F7E2 REBALANCE BUY {delta} {bsym} "
                               f"({self.variant_tag}) -> {tf:.2f}x alloc, "
                               f"fill={fill:.2f}")
            else:
                self.log.error("[combo] buy failed — will retry next day")
        else:
            need = -delta
            for p in sorted(own, key=lambda q: q["id"]):
                if need <= 0:
                    break
                lot = min(float(p["volume"]), need)
                r = self._mt5(self.executor.close_position_market, bsym, "long",
                              lot, str(p["id"]), f"{self.variant_tag[:8]}-red")
                if r:
                    need = round(need - lot, 2)
                    self.log.info(f"[combo] SELL {lot} (ticket {p['id']})")
                else:
                    self.log.error(f"[combo] partial close failed on "
                                   f"{p['id']} — retry next day")
                    break
            self._telegram(f"\U0001F7E1 REBALANCE SELL {-delta:.2f} {bsym} "
                           f"({self.variant_tag}) -> {tf:.2f}x alloc")
        # [2026-08-11 FIX] track REALITY, not intent. This used to key off
        # target_lots (what we WANTED), not what actually got filled -- a
        # partial-fill or a failed buy/sell leg (both logged above, "retry
        # next day") left state silently claiming the intended end-state
        # while the broker held something else. _watch_positions() returns
        # early when state["positions"] is empty, so a stray post-failure
        # position would never be alerted on. Re-query the broker so state
        # always reflects what's actually there.
        actual_lots = round(sum(float(p["volume"])
                                for p in self._own_positions(bsym)
                                if p["type"] == "BUY"), 2)
        if actual_lots > 0:
            self.state["positions"]["BTCUSDC"] = {
                "entry": px, "lot": actual_lots, "side": "long",
                "target_frac": tf}
        else:
            self.state["positions"].pop("BTCUSDC", None)
        self._save_state()
        return True

# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleeve", required=True, choices=["funding", "combo"])
    ap.add_argument("--variant-tag", type=str, required=True)
    ap.add_argument("--risk", type=float, default=1.0,
                    help="funding sleeve: %% equity risked to the 2.5xATR stop")
    ap.add_argument("--alloc", type=float, default=0.35,
                    help="combo sleeve: fraction of account equity allocated")
    ap.add_argument("--protective-stop-pct", type=float, default=30.0,
                    help="combo: catastrophe broker-side stop this %% below "
                         "each entry (insurance only — the strategy has no SL)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cfg = ForexConfig()
    cfg.symbols = ["BTCUSDC", "ETHUSDC"] if args.sleeve == "funding" else ["BTCUSDC"]
    cfg.magic_number = MAGIC[args.sleeve]
    cfg.dry_run = args.dry_run
    cfg.allow_real = args.allow_real
    for s in ("BTCUSDC", "ETHUSDC"):
        cfg.pip_size[s] = 1.0
        cfg.pip_value_usd_approx[s] = 0.01

    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] MT5 unavailable — use --dry-run off-Windows")
        sys.exit(1)

    DailySleevesBot(cfg, args.sleeve, args.variant_tag,
                    args.risk, args.alloc, args.protective_stop_pct).run()


if __name__ == "__main__":
    main()
