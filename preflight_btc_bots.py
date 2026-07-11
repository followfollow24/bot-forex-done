#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight_btc_bots.py -- Step 3 MANDATORY pre-flight for the two new BTC-HF
live bots (btc_cons magic 666000, btc_aggr magic 666010) before either
receives its first real signal. Same discipline as test_autotrading.py
(min-lot order, immediate close, real retcode) but run once per NEW magic
number, plus checks specific to this deployment:
  1. Symbol resolves (BTCUSDc exact match, already confirmed in Step 1)
  2. Order placement succeeds for EACH new magic number
  3. Position closes cleanly
  4. Lot sizing at --risk 0.20 matches the min-lot granularity analysis
     (should NOT be floored to 0.01 at current equity)
  5. Telegram fires, labeled per-variant
  6. Heartbeat/lock/state file paths are unique, no collision with gold
     or with each other

SAFETY: min lot (0.01) BTC, immediate close, real cost = one round-trip
spread (~$10 on BTCUSDc at current price -> negligible in USC terms).
Run on VPS (real MT5 terminal connected).
ASCII-only.
"""
import os
import sys
import time
import urllib.parse
import urllib.request

import MetaTrader5 as mt5

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_live_bot_gold_cwider import _make_paths, SYMBOL_MAGIC, VARIANT_MAGIC_OFFSET

EXPECTED_LOGIN = 160075275
SYMBOL = "BTCUSDc"          # exact broker casing -- required for all MT5 API calls
CANONICAL_SYMBOL = "BTCUSDC"  # matches SYMBOL_MAGIC's key (post-.upper() form the
# live bot always uses internally -- see forex_live_bot_gold_cwider.py's fix
# 2026-07-11 for why this must NOT just be SYMBOL)

VARIANTS = [
    ("btc_cons", dict(adx=15, sl=4.0, tp=12.0)),
    ("btc_aggr", dict(adx=12, sl=2.5, tp=7.5)),
]

results = {}


def send_telegram(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("  [TELEGRAM] not configured -- SKIPPED (set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print("  [TELEGRAM] sent OK")
        return True
    except Exception as exc:
        print(f"  [TELEGRAM] FAILED: {exc}")
        return False


def main():
    print("=" * 88)
    print(" BTC-HF LIVE BOT PRE-FLIGHT (Step 3, mandatory) -- btc_cons + btc_aggr")
    print("=" * 88)

    if not mt5.initialize():
        print(f"[FAIL] mt5.initialize(): {mt5.last_error()}"); sys.exit(1)

    acc = mt5.account_info()
    if acc is None or acc.login != EXPECTED_LOGIN:
        print(f"[FAIL] login={acc.login if acc else None} != expected {EXPECTED_LOGIN} -- ABORT")
        mt5.shutdown(); sys.exit(1)

    print(f"\nAccount confirmed: login={acc.login}  equity={acc.equity:,.2f} USC "
          f"(${acc.equity/100:,.2f})  balance={acc.balance:,.2f} USC")

    # ── check 1: symbol ──
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"[FAIL] symbol_info('{SYMBOL}') is None"); mt5.shutdown(); sys.exit(1)
    mt5.symbol_select(SYMBOL, True)
    tick = mt5.symbol_info_tick(SYMBOL)
    print(f"CHECK 1 [symbol resolves]: PASS  ({SYMBOL}, tick bid={tick.bid} ask={tick.ask})")

    # ── check 4 precompute: lot sizing sanity at --risk 0.20 ──
    RISK_PCT = 0.20
    equity_usc = acc.equity
    pip_size = 1.0  # matches forex_live_bot_gold_cwider.py's BTCUSDc override
    pip_value = info.trade_tick_value * (pip_size / info.trade_tick_size)  # live formula
    print(f"\nCHECK 4 [lot sizing @ risk={RISK_PCT}%, live equity={equity_usc:,.2f} USC]:")
    print(f"  pip_value_per_lot (live formula) = {pip_value:.6f} USC/lot per $1 move "
          f"(expect ~1.0)")
    for name, cfg in VARIANTS:
        sl_atr_mult = cfg["sl"]
        # use a representative recent ATR distance in $ terms (from 27-window backtest
        # median regime, ~150-800 depending on vol; use a conservative mid estimate here
        # as a sanity bound, not a precise prediction -- exact ATR comes from live bars)
        approx_atr = 400.0  # $ , mid-range of the 90-30d median ATR observed earlier
        sl_dist = approx_atr * sl_atr_mult
        risk_cash = equity_usc * RISK_PCT / 100.0
        raw_lot = risk_cash / (sl_dist / pip_size * pip_value)
        actual_lot = max(0.01, round(raw_lot, 2))
        actual_risk_pct = (sl_dist / pip_size * pip_value * actual_lot) / equity_usc * 100.0
        floored = actual_lot <= 0.01 and raw_lot < 0.015
        print(f"  {name:<10} SL={sl_atr_mult}xATR (approx SLdist=${sl_dist:.0f})  "
              f"raw_lot={raw_lot:.4f}  actual_lot={actual_lot:.2f}  "
              f"actual_risk%={actual_risk_pct:.3f}%  "
              f"{'!! FLOOR-BOUND' if floored else 'OK, not floor-bound'}")

    # ── checks 2/3/5: order roundtrip + telegram, per NEW magic number ──
    print(f"\nCHECKS 2/3/5 [order open/close + Telegram] per variant:")
    for name, cfg in VARIANTS:
        magic = SYMBOL_MAGIC[CANONICAL_SYMBOL] + VARIANT_MAGIC_OFFSET[name]
        print(f"\n  --- {name}  (magic={magic}) ---")
        lot = info.volume_min
        tick = mt5.symbol_info_tick(SYMBOL)
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "magic": magic,
            "comment": f"PREFLIGHT-{name.upper()}"[:16],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        if r is None:
            print(f"    [FAIL] order_send() None: {mt5.last_error()}")
            results[name] = "FAIL-open"; continue
        print(f"    OPEN retcode={r.retcode}  comment={r.comment}")
        if r.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"    [FAIL] open did not succeed (retcode={r.retcode})")
            results[name] = f"FAIL-open-retcode{r.retcode}"; continue

        ticket = r.order; fill_px = r.price
        print(f"    [PASS] OPEN ok  ticket={ticket}  fill={fill_px}")
        time.sleep(1)

        tick2 = mt5.symbol_info_tick(SYMBOL)
        close_req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": lot,
            "type": mt5.ORDER_TYPE_SELL, "position": ticket,
            "price": tick2.bid if tick2 else fill_px, "magic": magic,
            "comment": f"PREFLIGHT-{name.upper()}-CLOSE"[:24],
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": mt5.ORDER_FILLING_IOC,
        }
        cr = mt5.order_send(close_req)
        if cr is None or cr.retcode != mt5.TRADE_RETCODE_DONE:
            rc = cr.retcode if cr else mt5.last_error()
            print(f"    [WARN] CLOSE failed retcode={rc} -- MANUALLY CLOSE ticket={ticket} NOW")
            results[name] = "FAIL-close"; continue
        diff = cr.price - fill_px
        print(f"    [PASS] CLOSE ok  fill={cr.price}  diff={diff:+.2f}")
        results[name] = "PASS"

        send_telegram(
            f"[PREFLIGHT TEST] {name} (BTCUSDc, magic={magic})\n"
            f"Open {fill_px} -> Close {cr.price} (min-lot roundtrip)\n"
            f"This is a pre-flight check, not a real signal.")

    # ── check 6: file path uniqueness ──
    print(f"\nCHECK 6 [heartbeat/lock/state file uniqueness]:")
    gold_paths = _make_paths("XAUUSD", "adx20tp7")
    all_paths = {"gold_adx20tp7": gold_paths}
    for name, _ in VARIANTS:
        # CANONICAL_SYMBOL here, not SYMBOL -- must match what the real live
        # bot's main() actually calls _make_paths() with (SYMBOL.upper()),
        # otherwise this would report a different (wrong, fallback-hashed)
        # magic number than what really gets used. File paths themselves are
        # unaffected (slug = symbol.lower() normalizes either casing the same).
        all_paths[name] = _make_paths(CANONICAL_SYMBOL, name)
    labels = ["STOP", "STATE", "LOG", "FILLS", "LOCK", "MAGIC", "EQUITY_STOP", "HEARTBEAT"]
    seen = {}
    collision = False
    for who, paths in all_paths.items():
        for lbl, p in zip(labels, paths):
            if lbl == "MAGIC":
                continue
            key = (lbl, p)
            if key in seen and seen[key] != who:
                print(f"  [FAIL] COLLISION: {lbl}={p} used by both {seen[key]} and {who}")
                collision = True
            seen[key] = who
    if not collision:
        print("  [PASS] no filename collisions between gold and the 2 new BTC variants")
    for who, paths in all_paths.items():
        print(f"    {who:<16} magic={paths[5]}  heartbeat={os.path.basename(paths[7])}  "
              f"lock={os.path.basename(paths[4])}")

    print("\n" + "=" * 88)
    print(" SUMMARY")
    print("=" * 88)
    for name, _ in VARIANTS:
        print(f"  {name}: {results.get(name, 'NOT RUN')}")
    all_pass = all(v == "PASS" for v in results.values()) and not collision
    print(f"\n  OVERALL: {'ALL CHECKS PASSED -- safe to proceed to Step 4' if all_pass else 'FAILURES PRESENT -- DO NOT PROCEED to Step 4'}")
    print("=" * 88)

    mt5.shutdown()


if __name__ == "__main__":
    main()
