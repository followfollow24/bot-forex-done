"""Controls for the BTC shorts-side ETH/BTC-ratio gate:
1) block ALL shorts (long-only)  -- does the ratio add info beyond 'fewer shorts'?
2) random short-thinning at the same keep-rate (5 seeds) -- placebo gate.
"""
import sys, os
sys.path.insert(0, os.getcwd())
import numpy as np, pandas as pd
from _uc_crossasset2 import (do_run, line, REMA, N, MID, YRS, T0, T1)

print("window %s .. %s" % (T0, T1), flush=True)
base = do_run("BTC")
print("BASE           %s" % line(base), flush=True)

up36 = REMA[36] > REMA[168]
nan = np.isnan(REMA[168])
gate = np.where(nan, True, up36).astype(bool)
st = do_run("BTC", None, gate)
print("GATE S36_168   %s" % line(st), flush=True)
keep_rate = gate[~nan].mean()
print("gate keep-rate on valid bars: %.3f" % keep_rate, flush=True)

noshort = np.zeros(N, dtype=bool)
st = do_run("BTC", None, noshort)
print("LONG-ONLY      %s" % line(st), flush=True)

for seed in range(5):
    rng = np.random.default_rng(seed)
    rnd = rng.random(N) < keep_rate
    rnd = np.where(nan, True, rnd).astype(bool)
    st = do_run("BTC", None, rnd)
    print("RANDOM seed=%d  %s" % (seed, line(st)), flush=True)
