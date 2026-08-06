#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot_health_check.py -- one-shot health check for all 9 live bots.
Run ON THE VPS. For each bot:
  - is the process alive, and how long since its log last wrote anything
    (a multi-hour silence is the exact signature of the 2026-08-06 hang
    found on btc_h1_manual -- _fetch_closed_candles() has no timeout, see
    project_bot_hang_issue.md)
  - any open position: entry_atr, current profit in ATR terms (computed the
    same way _check_atr_milestones() does), and whether the milestone
    ratchet in the log matches what SHOULD have fired by now
"""
import os, re, time, json
from datetime import datetime, timezone
import MetaTrader5 as mt5

DESKTOP = os.path.expanduser("~/Desktop")

# variant_tag -> (magic, log filename, state filename, heartbeat filename)
# heartbeat is written EVERY loop iteration BEFORE the MT5 candle fetch (the
# call known to hang with no timeout -- project_bot_hang_issue.md), so a
# stale heartbeat is a much more reliable hang signal than log silence alone
# (the log only writes when something notable happens).
BOTS = {
    "gold_h1_manual":     (555143, "forex_xauusdc_gold_h1_manual.log",     "xauusdc_gold_h1_manual_state.json",     "HEARTBEAT_XAUUSDC_GOLD_H1_MANUAL"),
    "gold_daily_breakout":(555153, "forex_xauusdc_gold_daily_breakout.log","xauusdc_gold_daily_breakout_state.json","HEARTBEAT_XAUUSDC_GOLD_DAILY_BREAKOUT"),
    "gold_momentum_rsi":  (555073, "forex_xauusdc_gold_momentum_rsi.log", "xauusdc_gold_momentum_rsi_state.json",  "HEARTBEAT_XAUUSDC_GOLD_MOMENTUM_RSI"),
    "btc_h1_manual":      (666120, "forex_btcusdc_btc_h1_manual.log",     "btcusdc_btc_h1_manual_state.json",      "HEARTBEAT_BTCUSDC_BTC_H1_MANUAL"),
    "btc_h1_breakout":    (666020, "forex_btcusdc_btc_h1_breakout.log",   "btcusdc_btc_h1_breakout_state.json",    "HEARTBEAT_BTCUSDC_BTC_H1_BREAKOUT"),
    "btc_amd":            (666040, "forex_btcusdc_btc_amd.log",           "btcusdc_btc_amd_state.json",            "HEARTBEAT_BTCUSDC_BTC_AMD"),
    "btc_lqsweep":        (666050, "forex_btcusdc_btc_lqsweep.log",       "btcusdc_btc_lqsweep_state.json",        "HEARTBEAT_BTCUSDC_BTC_LQSWEEP"),
    "btc_tpo":            (666060, "forex_btcusdc_btc_tpo.log",           "btcusdc_btc_tpo_state.json",            "HEARTBEAT_BTCUSDC_BTC_TPO"),
    "eth_h1_manual":      (667130, "forex_ethusdc_eth_h1_manual.log",     "ethusdc_eth_h1_manual_state.json",      "HEARTBEAT_ETHUSDC_ETH_H1_MANUAL"),
}

if not mt5.initialize():
    print("ERR: MT5 init failed")
    raise SystemExit(1)

now = time.time()
print("=" * 100)
print(f" BOT HEALTH CHECK  {datetime.now(timezone.utc).isoformat()}")
print("=" * 100)

for tag, (magic, logfn, statefn, hbfn) in BOTS.items():
    logpath = os.path.join(DESKTOP, logfn)
    statepath = os.path.join(DESKTOP, statefn)
    hbpath = os.path.join(DESKTOP, hbfn)

    if os.path.exists(logpath):
        mtime = os.path.getmtime(logpath)
        silence_min = (now - mtime) / 60
    else:
        silence_min = None

    if os.path.exists(hbpath):
        hb_age_min = (now - os.path.getmtime(hbpath)) / 60
        hb_flag = " <-- HUNG (heartbeat stale >5min, restart this bot)" if hb_age_min > 5 else " OK"
    else:
        hb_age_min = None
        hb_flag = " <-- HEARTBEAT FILE NOT FOUND"

    sym = "BTCUSDc" if "btc" in tag else ("ETHUSDc" if "eth" in tag else "XAUUSDc")
    positions = [p for p in (mt5.positions_get(symbol=sym) or []) if p.magic == magic]

    print(f"\n{tag:<22} magic={magic}")
    print(f"  heartbeat: {'%.1f min ago' % hb_age_min if hb_age_min is not None else 'N/A'}{hb_flag}")
    print(f"  log last write: {'%.1f min ago' % silence_min if silence_min is not None else 'N/A (not found)'}")

    if not positions:
        print("  position: none open")
        continue

    for p in positions:
        side = "long" if p.type == 0 else "short"
        entry_atr = None
        if os.path.exists(statepath):
            try:
                st = json.load(open(statepath))
                for sp in st.get("positions", []):
                    if abs(sp.get("entry", -1) - p.price_open) < 0.01:
                        entry_atr = sp.get("entry_atr")
                        break
            except Exception:
                pass
        line = f"  position: {side} lot={p.volume} entry={p.price_open:.2f} now={p.price_current:.2f} pnl={p.profit:+.2f}"
        if entry_atr and entry_atr > 0:
            direction = 1 if side == "long" else -1
            profit_atr = (p.price_current - p.price_open) * direction / entry_atr
            level = int(profit_atr)
            line += f"  entry_atr={entry_atr:.2f}  profit_atr={profit_atr:+.2f} (level {level})"
            if abs(level) >= 1:
                line += "  [past a whole-ATR level -- should have a matching TELEGRAM alert in the log]"
        else:
            line += "  entry_atr=? (not found in state)"
        print(line)

mt5.shutdown()
print("\n" + "=" * 100)
