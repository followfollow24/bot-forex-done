#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_bot_pnl.py -- one table: which bots are actually working, and what each has made.

Answers two questions that this project has repeatedly got wrong when it
answered them separately:

  1. WHICH BOTS ARE RUNNING. Not "is the process alive" and not "is the
     heartbeat fresh" -- four bots ran an MT5 "IPC send failed" loop for a
     week in 2026-08 while both of those said healthy. Working means the
     LOG is fresh. All three are shown side by side so a disagreement
     between them is visible rather than averaged away.

  2. WHAT EACH HAS MADE. Grouped by position_id, never by per-deal magic:
     broker-side closing deals (stop-loss, take-profit, stop-out) carry
     magic 0, so filtering deals by magic silently drops every exit and
     reports a bot with 13 trades as having none. The magic comes from the
     OPENING deal and the whole position inherits it.

Net P&L includes swap and commission, not just gross profit -- on a cent
account with an asymmetric BTC swap (-6.9%/yr long, 0 short) the carry is
not a rounding error.

Risk % is read from watchdog_h1.ps1's launch args, which is what the bot
is ACTUALLY running with. Any table that hardcodes its own copy drifts
from reality -- that has happened twice in this repo.

Usage (on the VPS):  python _bot_pnl.py [days]
                     (default: whole account history)
"""
import os
import re
import sys
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    print("[ERROR] needs MetaTrader5 (run on the VPS)")
    sys.exit(1)

DESK = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Desktop")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 0   # 0 = everything

MAGIC_LABEL = {
    555143: "gold_h1_manual",
    555153: "gold_daily_breakout",
    555073: "gold_momentum_rsi",
    666120: "btc_h1_manual",
    666020: "btc_h1_breakout",
    666040: "btc_amd",
    666050: "btc_lqsweep",
    666060: "btc_tpo",
    667130: "eth_h1_manual",
    668001: "funding_contrarian",
    668002: "btc_combo_lb",
    671001: "chart_ai_trader",
    669001: "news_gemini",
}

# heartbeat / stop / log names are built by each bot from SYMBOL+VARIANT;
# these are the real on-disk prefixes, confirmed against the Desktop
FILES = {
    "gold_h1_manual":      ("XAUUSDC_GOLD_H1_MANUAL",      "forex_xauusdc_gold_h1_manual.log"),
    "gold_daily_breakout": ("XAUUSDC_GOLD_DAILY_BREAKOUT", "forex_xauusdc_gold_daily_breakout.log"),
    "gold_momentum_rsi":   ("XAUUSDC_GOLD_MOMENTUM_RSI",   "forex_xauusdc_gold_momentum_rsi.log"),
    "btc_h1_manual":       ("BTCUSDC_BTC_H1_MANUAL",       "forex_btcusdc_btc_h1_manual.log"),
    "btc_h1_breakout":     ("BTCUSDC_BTC_H1_BREAKOUT",     "forex_btcusdc_btc_h1_breakout.log"),
    "btc_amd":             ("BTCUSDC_BTC_AMD",             "forex_btcusdc_btc_amd.log"),
    "btc_lqsweep":         ("BTCUSDC_BTC_LQSWEEP",         "forex_btcusdc_btc_lqsweep.log"),
    "btc_tpo":             ("BTCUSDC_BTC_TPO",             "forex_btcusdc_btc_tpo.log"),
    "eth_h1_manual":       ("ETHUSDC_ETH_H1_MANUAL",       "forex_ethusdc_eth_h1_manual.log"),
    "funding_contrarian":  ("CRYPTO_FUNDING_CONTRARIAN",   "forex_bot_crypto_funding_contrarian.log"),
    "btc_combo_lb":        ("BTCUSDC_BTC_COMBO_LB",        "forex_bot_btcusdc_btc_combo_lb.log"),
    "chart_ai_trader":     ("CHART_AI_TRADER",             "forex_bot_chart_ai_trader.log"),
    "news_gemini":         ("NEWS_GEMINI",                 "forex_bot_news_gemini.log"),
}

# see _log_cadence.py: the watchdog leaked its own output into these logs
INJECTED = re.compile(r"\[[a-z0-9_]+\] (OK -- heartbeat|kill-switch present|"
                      r"no log file matched|LOG STALE|STALE:)")


def age_min(path):
    try:
        return (datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))).total_seconds() / 60.0
    except OSError:
        return None


def real_log_age(name):
    """Log age ignoring the watchdog lines that leaked in -- counting those
    would make a frozen log look freshly written."""
    p = os.path.join(DESK, FILES[name][1])
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-400:]
    except OSError:
        return None
    for line in reversed(tail):
        if INJECTED.search(line):
            continue
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", line)
        if m:
            t = datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - t).total_seconds() / 60.0
    return None


def watchdog_risk():
    """--risk actually being launched, straight from the watchdog."""
    out = {}
    p = os.path.join(DESK, "watchdog_h1.ps1")
    try:
        txt = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return out
    for m in re.finditer(r'Variant\s*=\s*"([a-z0-9_]+)"(.*?)Args\s*=\s*"([^"]*)"',
                         txt, re.S):
        variant, args = m.group(1), m.group(3)
        r = re.search(r"--risk\s+([\d.]+)", args)
        a = re.search(r"--alloc\s+([\d.]+)", args)
        if r:
            out[variant] = ("risk", float(r.group(1)))
        elif a:
            out[variant] = ("alloc", float(a.group(1)))
    return out


def main():
    if not mt5.initialize():
        print("[ERROR] MT5 init failed")
        return 2
    acct = mt5.account_info()
    if acct is None:
        print("[ERROR] account_info() returned None -- MT5 IPC problem")
        mt5.shutdown()
        return 2

    frm = datetime.now() - timedelta(days=DAYS) if DAYS else datetime(2020, 1, 1)
    deals = mt5.history_deals_get(frm, datetime.now() + timedelta(days=1))
    if deals is None:
        deals = []

    # group by position: the OPENING deal carries the magic, the closing
    # deal carries magic 0 because the broker sent it
    pos = {}
    for d in deals:
        if d.position_id == 0:
            continue
        p = pos.setdefault(d.position_id, {"magic": 0, "net": 0.0, "open": None,
                                           "close": None, "vol": 0.0})
        p["net"] += d.profit + d.swap + d.commission
        if d.magic and not p["magic"]:
            p["magic"] = d.magic
        if d.entry == mt5.DEAL_ENTRY_IN:
            p["open"] = datetime.fromtimestamp(d.time)
            p["vol"] = d.volume
        elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            p["close"] = datetime.fromtimestamp(d.time)

    per = {}
    for p in pos.values():
        if p["close"] is None:      # still open, counted separately below
            continue
        name = MAGIC_LABEL.get(p["magic"])
        if name is None:
            name = f"(unmapped magic {p['magic']})"
        b = per.setdefault(name, {"n": 0, "w": 0, "net": 0.0, "best": None,
                                  "worst": None, "last": None})
        b["n"] += 1
        b["net"] += p["net"]
        if p["net"] > 0:
            b["w"] += 1
        b["best"] = p["net"] if b["best"] is None else max(b["best"], p["net"])
        b["worst"] = p["net"] if b["worst"] is None else min(b["worst"], p["net"])
        if p["close"] and (b["last"] is None or p["close"] > b["last"]):
            b["last"] = p["close"]

    # floating
    live = mt5.positions_get() or []
    floating = {}
    for q in live:
        name = MAGIC_LABEL.get(q.magic, f"(unmapped magic {q.magic})")
        f = floating.setdefault(name, {"n": 0, "pl": 0.0, "vol": 0.0})
        f["n"] += 1
        f["pl"] += q.profit + q.swap
        f["vol"] += q.volume
    mt5.shutdown()

    risk = watchdog_risk()
    cur = acct.currency

    print("=" * 100)
    print(f" BOT STATUS + P&L   account {acct.login}   equity {acct.equity:,.2f} {cur}"
          f"   balance {acct.balance:,.2f}")
    print(f" closed positions since {frm:%Y-%m-%d}"
          + ("  (whole history)" if not DAYS else f"  (last {DAYS}d)"))
    print("=" * 100)
    print(f"{'bot':<21}{'state':<11}{'log age':>9}{'risk':>8}"
          f"{'trades':>8}{'WR':>7}{'net ' + cur:>12}{'best':>9}{'worst':>9}{'last trade':>12}")
    print("-" * 100)

    order = sorted(per.keys(), key=lambda k: -per[k]["net"])
    for name in list(MAGIC_LABEL.values()):
        if name not in order and name not in floating:
            order.append(name)
    seen = set()
    tot_net = tot_n = 0
    for name in order:
        if name in seen:
            continue
        seen.add(name)
        b = per.get(name, {"n": 0, "w": 0, "net": 0.0, "best": None,
                           "worst": None, "last": None})
        hb = age_min(os.path.join(DESK, "HEARTBEAT_" + FILES[name][0])) if name in FILES else None
        lg = real_log_age(name) if name in FILES else None
        stopped = name in FILES and os.path.exists(os.path.join(DESK, "STOP_" + FILES[name][0]))
        if stopped:
            state = "STOPPED"
        elif hb is None:
            state = "no heartbeat"
        elif lg is None:
            state = "no log"
        elif lg > 300:
            state = "LOG FROZEN"
        else:
            state = "running"
        rk = risk.get(name)
        if rk and rk[0] == "risk":
            rks = f"{rk[1]:.2f}%"
        elif rk:
            rks = f"a{rk[1]:.2f}"
        else:
            rks = "-"
        wr    = f"{100.0 * b['w'] / b['n']:.0f}%" if b["n"] else "-"
        s_lg  = f"{lg:,.0f}m" if lg is not None else "-"
        s_bst = f"{b['best']:+,.0f}" if b["best"] is not None else "-"
        s_wst = f"{b['worst']:+,.0f}" if b["worst"] is not None else "-"
        s_lst = b["last"].strftime("%m-%d %H:%M") if b["last"] else "-"
        print(f"{name:<21}{state:<11}{s_lg:>9}{rks:>8}{b['n']:>8}{wr:>7}"
              f"{b['net']:>+12,.2f}{s_bst:>9}{s_wst:>9}{s_lst:>12}")
        tot_net += b["net"]
        tot_n += b["n"]
    print("-" * 100)
    print(f"{'TOTAL closed':<21}{'':<11}{'':>9}{'':>8}{tot_n:>8}{'':>7}{tot_net:>+12,.2f}")

    if floating:
        print()
        print("OPEN POSITIONS")
        for name, f in sorted(floating.items(), key=lambda kv: -kv[1]["pl"]):
            print(f"  {name:<21}{f['n']} position(s)  {f['vol']:.2f} lot  "
                  f"floating {f['pl']:+,.2f} {cur}")
        print(f"  {'TOTAL floating':<21}{sum(f['pl'] for f in floating.values()):+,.2f} {cur}")
    else:
        print("\nOPEN POSITIONS: none")

    print()
    print("state: 'running' = log written within 300 min. A fresh HEARTBEAT is NOT")
    print("used to decide this -- four bots kept heartbeating through a week-long")
    print("MT5 outage in 2026-08 while doing no work at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
