# FROZEN SPECS — the 2 new portfolio sleeves (2026-08-08)

Both rules are adversarially verified (independent re-implementation matched)
and FROZEN. Do not re-tune parameters; a live bot must reproduce its
reference CSV bit-for-bit in the preflight self-test before any deploy.

Acceptance tests (rerunnable):
- `ref_funding_contrarian.py` — exits 0 iff the funding rule reproduces
  combo monthly Sharpe full=0.94 / IS=1.23 / OOS(>=2023)=1.13 (tol 0.02)
  and every row of `ref_funding_signals.csv` (4,864 rows) is consistent.
- `ref_combo_longbias.py` — regenerates `ref_combo_signals.csv` (3,240 rows);
  daily-clock sanity: Sharpe 1.06, CAGR 44.5%, MaxDD 47.2%, MAR 0.94.
  (Honest note: daily-clock OOS half Sharpe 0.60 vs 0.68-0.75 on the original
  hourly clock — positions agree 92.8% of days, corr 0.973.)

## 1. Funding-Contrarian Daily (BTC + ETH, 50/50)

- Daily bars = Binance 15m → calendar UTC days. Funding f_mean(D) = mean of
  day-D 8h prints (00/08/16 UTC; the 00:00 print belongs to the day it stamps).
- Indicators (pandas, **adjust=True** on all EMAs — recursive EMA will NOT
  match): fEMA3/fEMA30 of f_mean, pEMA200 of funding-era close,
  Wilder ATR14 (`ewm(alpha=1/14, adjust=False)` on TR).
- Signal at day-D close: bias +1 if fEMA3<fEMA30, -1 if >, 0 if equal;
  gated by close vs pEMA200 (long only above, short only below).
- Execute at D+1 UTC open. Stop = entry ∓ 2.5×ATR14(signal day D), fixed for
  life. Size = min(1% equity / stop-distance, 3× equity / price).
- Within-day order: open-execution → stop check (entry day: touch-branch
  only) → mark. Flip closes and reopens at the same open.
- Costs: spread once per round trip ($10 BTC / $1 ETH per unit size);
  swap −6.9%/yr on LONG holds only.
- LIVE: signals from Binance REST (realized funding + 1d klines, UTC);
  MT5 (BTCUSDc/ETHUSDc) only for fills + broker-side stop anchored to the
  actual fill. Data failure ⇒ NO new entries; hold existing with its stop.

## 2. Combo LongBias (BTC daily, 3 sleeves)

- Daily close = 23:00-UTC H1 bar close aggregated from MT5 H1 — NEVER broker
  D1 (broker midnight ≠ UTC midnight; measure server offset at runtime and
  alert if it shifts).
- Sleeve A: EMA25 > EMA120 (adjust=False recursive, seeded first close,
  A=0 for first 120 bars) → 1 else 0.
- Sleeve B: sign(close_t / close_{t-40} − 1), 0 if unavailable/exactly 1.
- Sleeve C: Donchian stop-and-reverse on CLOSE, entry 20d / exit 10d channels
  of the PREVIOUS days (rolling then shift(1)), strict inequalities, evaluated
  in order: (p≤0 & c>hi20)→+1; elif (p≥0 & c<lo20)→−1; elif (p=+1 & c<lo10)→0;
  elif (p=−1 & c>hi10)→0. Path-dependent: on restart REPLAY the state machine
  over full history, never persist p.
- target_frac = max((A+B+C)/3, 0) ∈ {0, ⅓, ⅔, 1}; rebalance the delta as one
  market order in the 00:00–00:05 UTC window; skip if |delta| < volume_step.
  No strategy-level SL/TP (equity kill-switch is a separate safety layer).
- Min-lot guard at startup: refuse if equity/3 < 2× min-step notional.
- Restart: recompute target from replay, read net lots from MT5 by magic,
  trade the delta with an alert. Preflight: diff own (date, sleeves,
  target_frac) vs `ref_combo_signals.csv` — must match 100%.

Portfolio context: these are sleeves 3+4 of the 4-stream plan toward
~0.3%/day at P(DD>50%)≈38% (see memory `project_edge_hunt_2026_08_07`).
Deploy path: dry-run/demo ≥1 month before real money.
