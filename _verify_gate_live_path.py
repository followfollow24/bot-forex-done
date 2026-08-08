#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Causal/live-path verification of the 2026-08-07 entry gates.

Proves the PURE functions the live bot calls (gate_time_allow,
gate_r36s_allow_short in forex_live_bot_gold_cwider.py) reproduce the exact
masks the validation used (_uc_verify_gold_time.block_allow,
_uc_verify_crossasset.short_mask) — the same "live path == validated path"
bar every strategy change here has had to pass.

Run on the Mac: MetaTrader5 is stubbed before import (the bot file guards
mt5 usage behind the connector, but transitive imports may want it).

Checks:
  1. time gate: all 24 fill-hours x all 8 validated window variants ->
     must equal block_allow() bit-for-bit (192 combinations).
  2. r36S gate: full ETH/BTC history; at 500 random signal bars compare
     short_mask(36,168)[i] (full-history EMA, what the backtest used)
     vs gate_r36s_allow_short(closes[i-989:i+1]) (trailing 990-bar window,
     what the live bot fetches). Mismatches must be 0; if EMA warm-up
     truncation ever flips a call it would show up here.
"""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── stub MetaTrader5 so the live-bot module imports cleanly off-Windows ──
# MagicMock: forex_executor reads mt5.ORDER_FILLING_IOC etc. at import time,
# so the stub must answer ANY attribute; no MT5 call actually runs in the test.
from unittest.mock import MagicMock
sys.modules.setdefault("MetaTrader5", MagicMock())

import numpy as np
import pandas as pd

from forex_live_bot_gold_cwider import gate_time_allow, gate_r36s_allow_short


def block_allow_ref(eh, lo, hi):
    """Reference implementation, copied verbatim from _uc_verify_gold_time.py."""
    if lo < hi:
        blk = (eh >= lo) & (eh < hi)
    else:
        blk = (eh >= lo) | (eh < hi)
    return ~blk


def main():
    # ── 1. time gate ─────────────────────────────────────────────────────
    variants = [(20, 1), (19, 1), (21, 2), (20, 2), (21, 3), (19, 0), (20, 0), (22, 1)]
    hours = np.arange(24)
    bad = 0
    for lo, hi in variants:
        ref = block_allow_ref(hours, lo, hi)
        live = np.array([gate_time_allow(h, lo, hi) for h in hours])
        if not np.array_equal(ref, live):
            bad += 1
            print(f"  MISMATCH window ({lo},{hi}): ref={ref.astype(int)} live={live.astype(int)}")
    print(f"1. time gate: {len(variants) * 24} combinations, "
          f"{'ALL MATCH' if bad == 0 else f'{bad} WINDOW(S) WRONG'}")

    # ── 2. r36S gate ─────────────────────────────────────────────────────
    def load(path):
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601")
        return (df.set_index("timestamp").resample("1h")
                  .agg(close=("close", "last")).dropna().reset_index())

    btc = load("download/btcusdt-15m-vol.csv")
    eth = load("download/ethusdt-15m-vol.csv")
    m = btc.merge(eth, on="timestamp", suffixes=("_b", "_e"), how="inner")
    ratio = pd.Series(m["close_e"].to_numpy(float) / m["close_b"].to_numpy(float))
    full_mask = (ratio.ewm(span=36, adjust=False).mean()
                 > ratio.ewm(span=168, adjust=False).mean()).to_numpy()

    rng = np.random.default_rng(3)
    idx = rng.integers(1200, len(m), 500)      # skip warm-up region like live (>=600 bars aligned)
    mism = 0
    for i in idx:
        j0 = max(0, i - 989)                    # live fetches 990 bars incl. signal bar
        live = gate_r36s_allow_short(m["close_e"].iloc[j0:i + 1],
                                     m["close_b"].iloc[j0:i + 1])
        if live != bool(full_mask[i]):
            mism += 1
    print(f"2. r36S gate: 500 random signal bars, mismatches={mism} "
          f"({'PASS' if mism == 0 else 'CHECK EMA WARM-UP'})")

    ok = bad == 0 and mism == 0
    print("RESULT:", "LIVE PATH == VALIDATED PATH" if ok else "FAILED — DO NOT DEPLOY")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
