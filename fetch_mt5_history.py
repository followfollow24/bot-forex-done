#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
# fetch_mt5_history.py
# =============================================================================
# Pull historical OHLC bars from the running MT5 (Exness) terminal on the VPS
# and save them in the CSV format backtest_forex.py consumes:
#     timestamp,open,high,low,close
#
# *** RUN ON THE VPS ONLY *** (the machine with MetaTrader5 package + the logged
# in Exness terminal -- same box the bots run on). It cannot run on the Mac
# (no MetaTrader5 package/terminal there).
#
# Read-only (copy_rates); it never touches orders/positions, so it is safe to
# run while the live bots are trading.
#
# ASCII/English only -- all comments AND all printed output. Thai text in
# print() can raise UnicodeEncodeError on the Windows console (code page
# cp874/cp1252), which would crash the script at runtime. Keep it English.
#
# What it does:
#   1. initialize() and show login/server so you can confirm the right terminal
#   2. DISCOVER: list every symbol whose name contains "BTC" (or --match). A
#      Cent account may have no crypto at all, or name it BTCUSDc / BTCUSDm
#      instead of plain BTCUSD.
#   3. Pick a symbol (--symbol, else best guess), symbol_select() to make visible
#   4. Pull bars for the range (--start..--end) via copy_rates_range; if that is
#      shallow, fall back to copy_rates_from_pos to reveal the true max depth
#   5. Report bar count, first/last date, years, coverage %, then save the CSV
#
# Examples (PowerShell on the VPS):
#     python fetch_mt5_history.py --list                 # discover BTC symbols, then exit
#     python fetch_mt5_history.py                         # discover + pull M15 from 2018
#     python fetch_mt5_history.py --symbol BTCUSD --tf M15 --start 2018-01-01
#     python fetch_mt5_history.py --symbol BTCUSDc --tf M5 --start 2020-01-01 --end 2026-07-07
# =============================================================================
import argparse
import sys
import os
from datetime import datetime

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit("[ERROR] MetaTrader5 package not found -- run this on the VPS where the bots run.")

try:
    import pandas as pd
except ImportError:
    sys.exit("[ERROR] pandas not found -- pip install pandas")


TF_MAP = {
    "M1":  mt5.TIMEFRAME_M1,  "M5":  mt5.TIMEFRAME_M5,  "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1":  mt5.TIMEFRAME_H1,  "H4":  mt5.TIMEFRAME_H4,
    "D1":  mt5.TIMEFRAME_D1,
}

# Save next to the existing xauusd files so backtest_forex.py finds them in one place.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(_BASE_DIR, "download")


def discover_symbols(match: str):
    """Return list of (name, path, visible) whose name contains `match` (case-insensitive)."""
    all_syms = mt5.symbols_get()
    if all_syms is None:
        print(f"[WARN] symbols_get() returned None: {mt5.last_error()}")
        return []
    m = match.upper()
    hits = [(s.name, s.path, s.visible) for s in all_syms if m in s.name.upper()]
    return sorted(hits, key=lambda x: x[0])


def pick_symbol(hits, prefer: str = "BTCUSD"):
    """Best guess: exact == prefer > startswith(prefer) > first name containing USD."""
    names = [h[0] for h in hits]
    if prefer in names:
        return prefer
    for n in names:
        if n.upper().startswith(prefer.upper()):
            return n
    for n in names:
        if "USD" in n.upper():
            return n
    return names[0] if names else None


def fetch_range(symbol, tf_key, start, end):
    tf = TF_MAP[tf_key]
    rates = mt5.copy_rates_range(symbol, tf, start, end)
    if rates is None or len(rates) == 0:
        return None
    return pd.DataFrame(rates)


def fetch_maxdepth(symbol, tf_key, count=500_000):
    """Pull `count` bars back from the latest bar to reveal the terminal's true depth."""
    tf = TF_MAP[tf_key]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
    if rates is None or len(rates) == 0:
        return None
    return pd.DataFrame(rates)


def report_and_save(df, symbol, tf_key, save=True):
    df = df.copy()
    # MT5 'time' is epoch seconds in the broker's SERVER time. Keep it as-is (no tz
    # conversion) so it matches the existing xauusd files and the bot's server time.
    df["timestamp"] = pd.to_datetime(df["time"], unit="s")
    df = df[["timestamp", "open", "high", "low", "close"]].sort_values("timestamp")
    df = df.drop_duplicates(subset="timestamp")

    first, last = df["timestamp"].iloc[0], df["timestamp"].iloc[-1]
    span_days = (last - first).days
    years = span_days / 365.25

    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}[tf_key]
    expected_per_day = (24 * 60) / minutes
    got_per_day = len(df) / max(span_days, 1)
    coverage = got_per_day / expected_per_day * 100

    print("\n" + "=" * 60)
    print(f"  SYMBOL      : {symbol}   TF: {tf_key}")
    print(f"  bars        : {len(df):,}")
    print(f"  first bar   : {first}")
    print(f"  last bar    : {last}")
    print(f"  span        : {span_days:,} days  (~{years:.2f} years)")
    print(f"  coverage    : ~{coverage:.0f}% of full bars (crypto is 24/7 so this")
    print(f"                should be near 100%; much lower = terminal has not")
    print(f"                downloaded deep history -- scroll the chart back or")
    print(f"                supplement with Binance data)")
    print("=" * 60)

    if years < 3:
        print("  [!] history < 3 years -- too short for a reliable walk-forward OOS.")
        print("      Supplement with Binance public data (data.binance.vision) for depth.")

    if save:
        os.makedirs(OUT_DIR, exist_ok=True)
        fname = (f"{symbol.lower()}-{tf_key.lower()}-"
                 f"{first.strftime('%Y-%m-%d')}-{last.strftime('%Y-%m-%d')}.csv")
        fpath = os.path.join(OUT_DIR, fname)
        df.to_csv(fpath, index=False)
        print(f"\n  saved -> {fpath}")
        print(f"  ready for backtest (format: timestamp,open,high,low,close)")
    return df


def main():
    ap = argparse.ArgumentParser(description="Pull MT5 OHLC history -> backtest CSV")
    ap.add_argument("--symbol", default=None, help="exact symbol name (e.g. BTCUSD, BTCUSDc); if omitted, discover")
    ap.add_argument("--match", default="BTC", help="substring for discovery (default: BTC)")
    ap.add_argument("--tf", default="M15", choices=list(TF_MAP.keys()))
    ap.add_argument("--start", default="2018-01-01", help="YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--list", action="store_true", help="only list symbols matching --match, then exit")
    args = ap.parse_args()

    if not mt5.initialize():
        sys.exit(f"[ERROR] mt5.initialize() failed: {mt5.last_error()}\n"
                 f"        Open the Exness terminal and stay logged in first.")

    ti = mt5.terminal_info()
    ai = mt5.account_info()
    if ai:
        print(f"[OK] connected: login={ai.login}  server={ai.server}  company={ai.company}")
    if ti:
        print(f"     terminal path: {ti.path}")

    # ---- DISCOVER ----
    hits = discover_symbols(args.match)
    print(f"\nsymbols whose name contains '{args.match}' ({len(hits)} found):")
    if not hits:
        print(f"  (none) -- this account may have no crypto (Cent accounts are usually FX+metals only).")
        print(f"  Try --match USD to see what symbols exist, or use Binance data instead.")
    for name, path, visible in hits:
        print(f"  - {name:15s}  visible={visible}   [{path}]")

    if args.list or not hits:
        mt5.shutdown()
        return

    # ---- pick symbol ----
    symbol = args.symbol or pick_symbol(hits)
    if symbol is None:
        print("[ERROR] could not pick a symbol"); mt5.shutdown(); return
    print(f"\n>>> using symbol: {symbol}")

    if not mt5.symbol_select(symbol, True):
        print(f"[WARN] symbol_select({symbol}) failed: {mt5.last_error()} (pull may return nothing)")

    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now()

    # ---- pull range ----
    print(f"\npulling {symbol} {args.tf} : {start.date()} -> {end.date()} ...")
    df = fetch_range(symbol, args.tf, start, end)

    if df is None or len(df) < 100:
        got = 0 if df is None else len(df)
        print(f"[WARN] copy_rates_range returned {got} bars -- checking true max depth "
              f"via copy_rates_from_pos ...")
        df = fetch_maxdepth(symbol, args.tf)
        if df is None:
            print(f"[ERROR] pulled nothing: {mt5.last_error()}")
            print(f"        Open a {symbol} {args.tf} chart in the terminal, press Home /")
            print(f"        scroll left to load deep history, then run again.")
            mt5.shutdown(); return

    report_and_save(df, symbol, args.tf, save=True)
    mt5.shutdown()


if __name__ == "__main__":
    main()
