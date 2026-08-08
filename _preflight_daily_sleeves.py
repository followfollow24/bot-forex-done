#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Preflight for daily_sleeves_bot.py — the deploy gate.

Replays the bot's OWN pure signal engines (imported from the bot file, not
re-implemented) over the full reference history and diffs against the frozen
ground truth newbot_refs/ref_*_signals.csv:

  funding : bias per symbol-day must match the reference `bias` column.
            Run twice — (a) full history, (b) TRAILING-1300-day windows on the
            last 3 years (exactly what the live bot sees after its paginated
            Binance fetch) — to prove the truncated-window EMA values do not
            flip any decision.
  combo   : (sleeve_trend, sleeve_tsmom, sleeve_donch, target_frac) per day
            must match the reference columns exactly.

Exit 0 = deploy allowed. Any mismatch prints the rows and exits 1.
MetaTrader5 is stubbed so this runs on the Mac.
"""
import sys, os
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.modules.setdefault("MetaTrader5", MagicMock())

import numpy as np
import pandas as pd

from daily_sleeves_bot import funding_daily_frame, combo_daily_frame

REFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "newbot_refs")
SCRATCH = ("/private/tmp/claude-501/-Users-follow-Desktop-outputs/"
           "5c1c9348-7d96-4348-98bf-858abc3aadf3/scratchpad")


def ref_csv(name):
    for base in (REFS, SCRATCH):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return pd.read_csv(p)
    raise FileNotFoundError(name)


def daily_from_15m(path):
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
    d = (df.set_index("timestamp").resample("1D")
           .agg(open=("open", "first"), high=("high", "max"),
                low=("low", "min"), close=("close", "last"))
           .dropna(subset=["open"]).reset_index()
           .rename(columns={"timestamp": "date"}))
    return d


def check_funding() -> int:
    ref = ref_csv("ref_funding_signals.csv")
    ref["date"] = pd.to_datetime(ref["date"])
    bad = 0
    for sym, price_csv, fund_csv in (
            ("BTC", "download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv",
             "download/btc_funding.csv"),
            ("ETH", "download/ethusdt-15m-binance-2017-08-17-2026-06-30.csv",
             "download/eth_funding.csv")):
        daily = daily_from_15m(price_csv)
        fund = pd.read_csv(fund_csv)
        fund["ts"] = pd.to_datetime(fund["timestamp"])
        fund = fund.rename(columns={"funding_rate": "rate"})[["ts", "rate"]]

        # (a) full history — engine vs reference, every day
        frame = funding_daily_frame(daily, fund)
        r = ref[ref["symbol"] == sym].set_index("date")
        f = frame.set_index("date")
        common = r.index.intersection(f.index)
        mism = (r.loc[common, "bias"].astype(int)
                != f.loc[common, "bias"].astype(int))
        n_bad = int(mism.sum())
        print(f"[funding/{sym}] full-history: {len(common)} days, "
              f"bias mismatches={n_bad}")
        if n_bad:
            print(r.loc[common][mism].head(10).to_string())
            bad += n_bad

        # (b) trailing-window mode: last 3y, one decision per ~15 days
        days = sorted(common)[-1095::15]
        n_bad_w = 0
        for D in days:
            dcut = daily[daily["date"] <= D].tail(1300).reset_index(drop=True)
            fcut = fund[fund["ts"] < D + pd.Timedelta(days=1)]
            fr = funding_daily_frame(dcut, fcut)
            if fr.empty or fr.iloc[-1]["date"] != D:
                continue
            if int(fr.iloc[-1]["bias"]) != int(r.loc[D, "bias"]):
                n_bad_w += 1
                print(f"  window mismatch {sym} {D.date()}: "
                      f"win={int(fr.iloc[-1]['bias'])} ref={int(r.loc[D, 'bias'])}")
        print(f"[funding/{sym}] trailing-1300d windows: {len(days)} sampled, "
              f"mismatches={n_bad_w}")
        bad += n_bad_w
    return bad


def check_combo() -> int:
    ref = ref_csv("ref_combo_signals.csv")
    ref["date"] = pd.to_datetime(ref["date"])
    daily = daily_from_15m("download/btcusdt-15m-binance-2017-08-17-2026-06-30.csv")
    closes = daily.set_index("date")["close"]
    frame = combo_daily_frame(closes).set_index("date")
    r = ref.set_index("date")
    common = r.index.intersection(frame.index)
    bad = 0
    for col in ("sleeve_trend", "sleeve_tsmom", "sleeve_donch", "target_frac"):
        diff = (r.loc[common, col].astype(float)
                - frame.loc[common, col].astype(float)).abs() > 1e-6
        n = int(diff.sum())
        print(f"[combo] {col}: {len(common)} days, mismatches={n}")
        if n:
            show = pd.DataFrame({
                "ref": r.loc[common, col][diff],
                "bot": frame.loc[common, col][diff]})
            print(show.head(10).to_string())
        bad += n
    return bad


def main():
    total = check_funding() + check_combo()
    print("PREFLIGHT:", "PASS — deploy allowed" if total == 0
          else f"FAIL — {total} mismatches, DO NOT DEPLOY")
    sys.exit(0 if total == 0 else 1)


if __name__ == "__main__":
    main()
