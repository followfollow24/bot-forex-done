#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS-side preflight for daily_sleeves_bot.py.

_preflight_daily_sleeves.py (the Mac version) already proved the signal
engines match the frozen reference over FULL history (2471+2393+3240 days,
0 mismatches) -- but it needs the multi-hundred-MB local price CSVs under
download/, which have never been on the VPS (only the Mac research
environment has them; they are gitignored and were never meant to travel).

This script instead exercises the bot's REAL LIVE data paths -- more
relevant for a go-live gate than a CSV replay:
  funding : calls fetch_binance_daily/fetch_binance_funding for real (proves
            Binance is reachable from this VPS -- an open question in the
            frozen spec's live_data_needs section), builds the frame, and
            diffs `bias` against newbot_refs/ref_funding_signals.csv on every
            date that overlaps (the fetch naturally goes back ~2.7 years, so
            overlap with the reference, which ends 2026-06-30, is large).
  combo   : connects to the REAL MT5 terminal already logged into this
            machine, fetches H1 BTCUSDc bars, aggregates to UTC daily closes
            the same way the bot does live, and diffs (sleeve_trend,
            sleeve_tsmom, sleeve_donch, target_frac) against
            newbot_refs/ref_combo_signals.csv on overlapping dates.

Exit 0 = deploy allowed. Any mismatch prints the rows and exits 1.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from forex_config import ForexConfig
from forex_executor import MT5Connector
from daily_sleeves_bot import (fetch_binance_daily, fetch_binance_funding,
                               funding_daily_frame, combo_daily_frame)

REFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newbot_refs")


def ref_csv(name):
    return pd.read_csv(os.path.join(REFS, name))


def check_funding() -> int:
    ref = ref_csv("ref_funding_signals.csv")
    ref["date"] = pd.to_datetime(ref["date"])
    bad = 0
    for sym, binance in (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT")):
        daily = fetch_binance_daily(binance)
        fund = fetch_binance_funding(binance)
        print(f"[funding/{sym}] LIVE fetch: {len(daily)} daily bars "
              f"({daily['date'].iloc[0].date()}..{daily['date'].iloc[-1].date()}), "
              f"{len(fund)} funding prints")
        frame = funding_daily_frame(daily, fund)
        r = ref[ref["symbol"] == sym].set_index("date")
        f = frame.set_index("date")
        common = r.index.intersection(f.index)
        if len(common) < 30:
            print(f"[funding/{sym}] ABORT: only {len(common)} overlapping days "
                  f"with the reference -- cannot validate")
            bad += 1
            continue
        mism = (r.loc[common, "bias"].astype(int)
                != f.loc[common, "bias"].astype(int))
        n_bad = int(mism.sum())
        print(f"[funding/{sym}] {len(common)} overlapping days, "
              f"bias mismatches={n_bad}")
        if n_bad:
            print(r.loc[common][mism].head(10).to_string())
        bad += n_bad
    return bad


def check_combo() -> int:
    ref = ref_csv("ref_combo_signals.csv")
    ref["date"] = pd.to_datetime(ref["date"])

    cfg = ForexConfig()
    cfg.symbols = ["BTCUSDC"]
    log = __import__("logging").getLogger("preflight")
    __import__("logging").basicConfig(level=__import__("logging").INFO)
    conn = MT5Connector(cfg, log)
    if not conn.connect():
        print("[combo] ABORT: MT5 connect failed -- cannot validate")
        return 1
    bsym = conn.resolve_symbol("BTCUSDC")
    candles = conn.fetch_ohlcv_paginated(bsym, "1h", 17600)
    if not candles or len(candles) < 3000:
        print(f"[combo] ABORT: only {len(candles) if candles else 0} H1 bars "
              f"fetched -- cannot validate")
        return 1

    # Same UTC-offset measurement the live bot uses.
    now = __import__("time").time()
    newest_open = candles[-1][0] / 1000.0
    off = round((newest_open - (now // 3600) * 3600) / 1800.0) * 1800
    print(f"[combo] measured broker UTC offset: {off}s "
          f"({off/3600:+.1f}h)")

    rows = [(pd.Timestamp(int(c[0] / 1000) - off, unit="s"), float(c[4]))
            for c in candles
            if c[0] / 1000 - off + 3600 <= now]
    df = pd.DataFrame(rows, columns=["ts_utc", "close"])
    df["date"] = df["ts_utc"].dt.floor("D")
    daily = df.groupby("date")["close"].last()
    today = pd.Timestamp(pd.Timestamp.utcnow().date())
    daily = daily[daily.index < today]
    print(f"[combo] LIVE MT5->UTC daily closes: {len(daily)} days "
          f"({daily.index[0].date()}..{daily.index[-1].date()})")

    frame = combo_daily_frame(daily).set_index("date")
    r = ref.set_index("date")
    common = r.index.intersection(frame.index)
    if len(common) < 30:
        print(f"[combo] ABORT: only {len(common)} overlapping days -- "
              f"cannot validate")
        return 1
    bad = 0
    for col in ("sleeve_trend", "sleeve_tsmom", "sleeve_donch", "target_frac"):
        diff = (r.loc[common, col].astype(float)
                - frame.loc[common, col].astype(float)).abs() > 1e-6
        n = int(diff.sum())
        print(f"[combo] {col}: {len(common)} overlapping days, mismatches={n}")
        if n:
            show = pd.DataFrame({"ref": r.loc[common, col][diff],
                                 "bot": frame.loc[common, col][diff]})
            print(show.head(10).to_string())
        bad += n
    return bad


def main():
    total = check_funding() + check_combo()
    print("VPS PREFLIGHT:", "PASS — deploy allowed" if total == 0
          else f"FAIL — {total} mismatches, DO NOT DEPLOY")
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
