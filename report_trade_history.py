#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_trade_history.py — Full trade history since bot inception

Reads fills_log_xauusd_{variant}.csv for each of the 3 running variants
(adx20tp7 / m5tp7 / adx18tp7) and reports total trades, wins/losses,
profit factor, and total PnL since the very first deploy — NOT just
currently-open positions (use report_open_positions.py for that).

Each round-trip trade = one OPEN row + one matching CLOSE row.
PnL per trade is parsed from the CLOSE row's `comment` field, which the
live bot writes as "...pnl=+12.34" (see _close_timeout / _log_broker_close
in forex_live_bot_gold_cwider.py).

Run this on the VPS:
    cd C:\\Users\\Administrator\\Desktop
    python report_trade_history.py
"""
import csv
import os
import re
from datetime import datetime

DESKTOP = os.path.dirname(os.path.abspath(__file__))
SYMBOL_SLUG = "xauusd"
VARIANTS = ["adx20tp7", "m5tp7", "adx18tp7"]

PNL_RE = re.compile(r"pnl=([+-]?\d+\.?\d*)")


def load_fills(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def analyze_variant(variant: str) -> dict:
    path = os.path.join(DESKTOP, f"fills_log_{SYMBOL_SLUG}_{variant}.csv")
    rows = load_fills(path)

    opens = [r for r in rows if r.get("action") == "OPEN"]
    closes = [r for r in rows if r.get("action") == "CLOSE"]

    wins, losses, pnls = [], [], []
    spreads, slippages = [], []
    reasons = {}

    for r in closes:
        m = PNL_RE.search(r.get("comment", ""))
        pnl = float(m.group(1)) if m else 0.0
        pnls.append(pnl)
        if pnl >= 0:
            wins.append(pnl)
        else:
            losses.append(pnl)

        # classify exit reason from comment, e.g. "ADX20TP-TP (broker) pnl=..."
        c = r.get("comment", "")
        if "Timeout" in c:
            reason = "Timeout"
        elif "TP" in c:
            reason = "TP"
        elif "SL" in c:
            reason = "SL"
        else:
            reason = "Other"
        reasons[reason] = reasons.get(reason, 0) + 1

    for r in opens + closes:
        try:
            spreads.append(float(r.get("spread_at_fill", 0) or 0))
        except ValueError:
            pass
        try:
            slippages.append(float(r.get("slippage", 0) or 0))
        except ValueError:
            pass

    total_pnl = sum(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else 0.0
    expectancy = (total_pnl / len(pnls)) if pnls else 0.0

    first_ts, last_ts = None, None
    if rows:
        timestamps = [r.get("timestamp", "") for r in rows if r.get("timestamp")]
        if timestamps:
            first_ts, last_ts = min(timestamps), max(timestamps)

    return {
        "variant": variant,
        "file_found": os.path.exists(path),
        "opens": len(opens),
        "closes": len(closes),
        "still_open": len(opens) - len(closes),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "gross_win": gross_win,
        "gross_loss": gross_loss,
        "pf": pf,
        "total_pnl": total_pnl,
        "expectancy": expectancy,
        "reasons": reasons,
        "avg_spread": (sum(spreads) / len(spreads)) if spreads else 0.0,
        "avg_slippage": (sum(slippages) / len(slippages)) if slippages else 0.0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def main():
    print("=" * 100)
    print(" TRADE HISTORY SINCE INCEPTION (all fills_log CSVs on this machine)")
    print("=" * 100)

    grand_total_pnl = 0.0
    grand_total_trades = 0

    for variant in VARIANTS:
        r = analyze_variant(variant)
        print(f"\n[{variant}]")
        if not r["file_found"]:
            print(f"  fills_log_{SYMBOL_SLUG}_{variant}.csv NOT FOUND on this machine")
            continue

        print(f"  Period        : {r['first_ts']}  ->  {r['last_ts']}")
        print(f"  Opens/Closes  : {r['opens']} opened / {r['closes']} closed "
              f"({r['still_open']} still open)")
        print(f"  Win/Loss      : {r['wins']}W / {r['losses']}L  "
              f"(win_rate={r['win_rate']:.1f}%)")
        print(f"  Exit reasons  : " + ", ".join(f"{k}={v}" for k, v in r["reasons"].items()))
        if r["pf"] == float("inf"):
            print(f"  PF            : inf (no losses yet)")
        else:
            print(f"  PF            : {r['pf']:.3f}")
        print(f"  Total PnL     : {r['total_pnl']:+,.2f} USD")
        print(f"  Expectancy    : {r['expectancy']:+.2f} USD/trade")
        print(f"  Avg spread    : {r['avg_spread']:.4f}   Avg slippage: {r['avg_slippage']:+.4f}")

        grand_total_pnl += r["total_pnl"]
        grand_total_trades += r["closes"]

    print("\n" + "=" * 100)
    print(f" GRAND TOTAL (closed trades across all 3 variants): "
          f"{grand_total_trades} trades, {grand_total_pnl:+,.2f} USD realized PnL")
    print("=" * 100)


if __name__ == "__main__":
    main()
