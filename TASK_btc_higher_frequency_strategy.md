# Task: BTC Higher-Frequency Strategy Design & Validation (Research Only)

## Objective
Design and validate a **higher-frequency BTC trading strategy** (M15/H1 entry timeframe, ~360+ trades/yr) using the REAL HybridTrendPullback architecture and REAL BacktestEngine SL/TP execution. This is **RESEARCH ONLY** — no deployment to real account until walk-forward validation + demo forward-test pass.

## Why This Task
- **Gold bots (adx20tp7/adx18tp7)** hold 16h max → demo accumulates 30 trades in ~1-2 years. Too slow for validation.
- **BTC Combo LongBias** holds weeks → same problem, plus only 2-3 bears in 8.87yr sample for OOS testing.
- **M15 higher-freq variant** ~360 trades/yr → demo validates in 1-3 months, better stat power for tail-risk edge cases.

## Governing Methodology (Mandatory Guardrails)

### The Partial-TP Bug Lesson
Before running ANY backtest claiming "this is what the live bot does," **diff EVERY parameter** against the actual live bot's argparse defaults and startup banner. 
- Live gold bots: `--no-partial-tp` (partial_tp_frac=0.0)
- Backtest must match exactly; diff config vs source every time, don't trust memory.

### The Flip-Flop Pattern Guardrail
Never lead with full-sample ("in-sample") backtest results to claim an edge is "real" or "broken." Always:
1. Run **true walk-forward FIRST** (train picks config on data < test-date only, locked, applied to unseen test window)
2. Print TRAIN and TEST metrics **in the same row**, always, never lead with only the flattering one.
3. Report full-sample LAST, clearly flagged "IN-SAMPLE — context only," as a sanity check, not proof.

### Bear Regime Caveat
BTC has only ~2 distinct bears in 8.87yr (2018, 2022). OOS validation on 2022 bear is thin (n=1 bear). Flag conclusions as provisional; demo forward-test is the real gate.

## STEPS (Sequential)

### Step 0: Weekend & Costs (Fact-Check from MT5)
**Status:** ✅ **DONE** — `check_btc_weekend.py` ran on VPS 2026-07-09.
- **Weekend trading:** YES, real range (Sat/Sun = 0.66× weekday, 0% flat bars) → keep Binance 24/7 as-is.
- **Costs (verified from BTCUSDc symbol_info):**
  - Spread: **$10 absolute** (trade_tick_value 0.01, trade_tick_size 0.01)
  - Commission: **$0** (Cent crypto is spread-only)
  - Swap: **long −$0.1248/lot/night (~6.9%/yr notional), Friday ×3; short = free**
  - Contract: 0.01 BTC/lot

### Step 1: Strategy Grid Design & Candidate Generation

**Reuse:** HybridTrendPullback real class (H1 EMA50/200 + ADX + M15 EMA20 pullback + ATR SL/TP)

**Grid (parametric sweep):**
- ADX: **12, 15, 20, 25** (lower ADX = more trades, higher Sharpe if edge tight)
- SL: **2.0, 2.5, 3.0, 4.0** (× ATR)
- TP: derived as **SL × RR** where RR ∈ {2.0, 2.5, 3.0} (R:R ratios, ensures TP > SL)
- **Total: ~48 configs per entry-family (M15 and H1)**
- Partial-TP: **OFF (frac=0.0, matching live gold bots)**
- Trailing SL: **OFF (activation_atr=999.0)**
- Risk/trade: **0.30%** (same as live gold)

**Cost assumptions:**
- Spread: **$10**
- Commission: **$0**
- Swap: **asymmetric**, computed per night held (Friday ×3)
- Real costs in EVERY number; no placeholders.

### Step 2: Walk-Forward Validation (THE DECISIVE TEST)

**Data:** Binance 15m BTC (shape only), Exness costs, 2017-08-17 to 2026-06-30 (~8.87yr).

**Two WF schemes:**

#### WF-A: Yearly OOS (8 years: 2019–2026)
- For each year `Y`: TRAIN = all data before Y-01-01, TEST = year Y only.
- Train picks **best config by selected OBJECTIVE** (pf, sharpe, or return).
- Config locked, applied to unseen year.
- **Report:** PF>1 count (e.g., "7/8 years"), config stability (how many distinct configs picked).

#### WF-B: Single Split (train 2017-2021, test 2022-2026)
- TRAIN picks best config before 2022-01-01.
- Config locked, apply to 2022-2026 unseen.
- Show top-6 train-ranked configs; only row 1 (TRAIN-PICKED) is honest OOS.
- Reveal config fragility: does ranking shuffle OOS?

**Key metrics per window/split:**
- PF (profit factor) — sum_wins / sum_losses
- Sharpe (annualized) — mean daily return / std dev, √365
- MaxDD% (calendar-daily, filled for flat days)
- Return% (total P&L as % of $10K start)
- Win rate%
- Trade count
- All costs included (spread + swap).

### Step 3: Objective Selection & Iteration

**Why this matters:** SL4/TP12 config (Sharpe 1.71) was chosen by PF. But grid shows **ADX12/SL2.5/TP7.5** (Sharpe 1.52 yearly, **2.21 OOS split**) beats it on return (+140% vs +82% yearly). Different objective → different config → different outcomes.

- **Option A:** Select by PF (conservative, proven profitable in edge)
- **Option B:** Select by Sharpe (maximize risk-adjusted return)
- **Option C:** Select by return% (maximize P&L, accept variance)

Run WF-A and WF-B for each objective; report side-by-side.

### Step 4: Stress & Robustness (After Winner Locked)

For the **train-picked locked config** from Step 2 (WF-B), run:

#### Spread Stress
Re-run OOS at $10, $20, $30 spread. Does edge survive 3× spread blowout?

#### Long vs Short Decomposition
OOS by side: longs only, shorts only, combined. Is it bull-beta or genuinely 2-sided? 2022 bear specifically.

#### Correlation to Gold + BTC-Combo Sleeves
OOS daily-return correlation. Is it additive (low corr) or substitute (high corr)?

### Step 5: Report & Gate Decisions

**Always print TRAIN + TEST together:**

```
                TRAIN              |           TEST OOS
cfg             PF   Sh  ret%  n  |  PF   ret%   DD%  Sh    n
ADX12/SL2.5/TP7.5  1.19 1.70  +71 2525 | 1.24  +112%  7.8% 2.21  2842  <== PICKED
```

**Full-sample table printed LAST, flagged "IN-SAMPLE ONLY":**

```
cfg              PF    ret%   MaxDD%  Sharpe  trades   win%
ADX12/SL3/TP7.5  1.22  +147    9.7    1.86    4638     41   <-- in-sample context
```

### Step 6: Compare & Package (Only After 0-5)

Once a config **passes walk-forward + stress**, compare it to:
- Gold-trend sleeve: Sharpe 0.79, correlation gold↔BTC −0.01
- BTC-Combo LongBias: Sharpe 0.78 (WF-OOS), correlation +0.26 to new HF

Portfolio outcomes (inverse-vol, OOS):
- GOLD + Combo: Sharpe ~1.44
- **GOLD + HF:** Sharpe ~ ?  (should be ~1.5–2.1 if HF is standalone Sharpe 1.5+)
- All three: Sharpe ~ ?

**Only then** discuss packaging: demo bot (allow_real=OFF, heartbeat, equity-stop, kill-switch) + demo timeline (30 trades ≈ 1 month at ~360 trades/yr).

## Deliverables

1. **btc_walkforward.py** (updated if needed): runs grid, WF-A yearly, WF-B split, prints TRAIN+TEST side-by-side, flags full-sample as in-sample.
2. **btc_hf_stress.py** (updated if needed): spread stress, long/short decomposition, correlation to sleeves.
3. **Memory update:** project_btc_hf_edge.md with walk-forward results, honest caveats (n=1 bear, Sharpe on small DD, etc.), final config locked.

## Critical DON'Ts

- ❌ Never claim an edge "confirmed" based on full-sample alone.
- ❌ Never run backtest mismatching partial-TP without verifying (the bug).
- ❌ Never frame 16-loss gold streak as "just variance" when it's new OOS high → flag as edge case.
- ❌ Never deploy to real money until walk-forward + demo validate.
- ❌ Never use same H1 trend + M15 pullback signal on BTC expecting "diversification" from gold (same regime-lag risk).

## Success Criteria

- ✅ Walk-forward yields PF>1 in 7+/8 years (yearly) AND stable config (≤3 distinct configs picked).
- ✅ OOS split test config shows Sharpe ≥ 0.9, decay from train ≤ 30%.
- ✅ Spread 2–3× doesn't collapse edge (PF>1, Sharpe >0.7 still).
- ✅ Correlation to gold/combo clear (<0.5 to combo if additive).
- ✅ All numbers include real costs; no placeholders.

---

## Prior Findings (Context)

- **BTC weekend:** real range, 24/7 OK.
- **BTC Combo LongBias:** WF-OOS Sharpe 0.78, full-sample 0.86, only 30 trades/yr.
- **Gold edge (ADX20_TP7):** WF yearly Sharpe 1.52, split OOS Sharpe 1.71, survivor config very stable. **BUT** 16-loss streak now observed 2026-07-10, new OOS high → structural H1-lag weakness confirmed; edge is real but has tail-risk cluster at reversals.

---

## Timeline & Resource

- **Step 0:** Done (2026-07-09).
- **Steps 1-2:** Generate grid, run 2 × 48 configs × WF-A/WF-B → ~2-4 hours computation.
- **Steps 3-5:** Analyze, select objective, stress-test → 1-2 hours analysis.
- **Step 6:** Compare, report → 1 hour.
- **Total:** 4-8 hours elapsed time.

**Do NOT deploy until all steps complete + memory updated + demo plans written.**
