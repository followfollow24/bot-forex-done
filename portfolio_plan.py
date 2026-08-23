#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
How many UNCORRELATED edges to reach 0.3%/day -- the honest, Sharpe-driven math.

Achievable daily return at a sane risk budget is set by Sharpe, not leverage.
- Combining n uncorrelated strategies each Sharpe s -> portfolio Sharpe = s*sqrt(n).
- Growth-optimal (Kelly) leverage: full-Kelly CAGR_cont = S^2/2 (vol=S, brutal DD);
  half-Kelly CAGR_cont = 3*S^2/8 (vol=S/2, saner) -- we use HALF-Kelly as realistic.
- daily% ~= CAGR_cont / 365.
"""
import numpy as np

s = 0.78                       # Sharpe of ONE validated edge (our BTC combo, OOS)
TARGET_DAILY = 0.003           # 0.3%/day

print(f"single-edge Sharpe (our BTC combo, OOS) = {s}")
print(f"target = {TARGET_DAILY*100:.1f}%/day = +{((1+TARGET_DAILY)**365-1)*100:.0f}%/yr compounded\n")
print(f"{'n edges':>7}{'portSharpe':>11}{'halfKelly CAGR':>16}{'~daily%':>10}{'~annual vol(DD-ish)':>21}")
print("-" * 66)
for n in range(1, 8):
    S = s * np.sqrt(n)
    g = 3 * S**2 / 8            # half-Kelly continuous growth
    cagr = np.exp(g) - 1
    daily = np.exp(g/365) - 1
    vol = S / 2                 # half-Kelly annual vol -> DD roughly this order
    hit = "  <= reaches 0.3%/day" if daily >= TARGET_DAILY else ""
    print(f"{n:>7}{S:>11.2f}{cagr*100:>15.0f}%{daily*100:>9.2f}%{vol*100:>18.0f}%{hit}")

print("\nRead: you need ~5 GENUINELY uncorrelated edges (each ~Sharpe 0.78) to reach")
print("0.3%/day at half-Kelly -- and even then annual vol ~85% => 40-60% drawdowns")
print("are routine. 'Accept risk' is real here. Fewer/‑correlated edges => can't get there.")
print("\nCaveats: (1) 'uncorrelated' is hard; correlations spike toward 1 in crises.")
print("(2) each new candidate may fail OOS (like BTC mean-reversion did).")
print("(3) small bear-market sample => every edge here is provisional.")
