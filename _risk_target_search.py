import sys
sys.path.insert(0, '.')
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine, FastHybridTrendPullback
from gold_regime_filter_real_engine import gold_cfg, GOLD_CSV, SPREAD, COMM, START

loader = DataLoader(log_fn=lambda *a, **k: None)
cfg0 = ForexConfig(); cfg0.total_capital_usd = START
df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
d = prepare_data(df_full)

CONFIGS = [("adx20tp7", 3.0, 7.0, 20), ("adx18tp7", 3.0, 7.0, 18)]
RISK_PCT_BACKTEST = 0.30
risk_levels = [0.30, 0.50, 0.75, 1.00, 1.25, 1.50, 2.00]

for label, sl, tp, adx in CONFIGS:
    strat = FastHybridTrendPullback()
    strat.ADX_MIN = adx
    strat.precompute(d)
    strat.sl_atr, strat.tp_atr = sl, tp
    strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
    eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD, commission_per_lot=COMM, symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    trades = eng.trades
    years = (trades[-1]["equity_after"], len(trades))  # placeholder
    n_years = 13.0

    print(f"\n{label}:")
    header = "risk%".rjust(7) + "CAGR".rjust(10) + "final_x".rjust(14) + "max_drawdown%".rjust(16)
    print(header)
    for risk in risk_levels:
        equity = 1.0
        peak = 1.0
        maxdd = 0.0
        for t in trades:
            entry_equity = t["equity_after"] - t["net_pnl"]
            risk_amount_backtest = entry_equity * (RISK_PCT_BACKTEST / 100.0)
            if risk_amount_backtest == 0:
                continue
            r_multiple = t["net_pnl"] / risk_amount_backtest
            equity *= (1 + r_multiple * (risk / 100.0))
            if equity <= 0:
                equity = 0.0001
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            maxdd = max(maxdd, dd)
        cagr = (equity ** (1/n_years) - 1) * 100
        line = f"{risk:>6.2f}%" + f"{cagr:>9.1f}%" + f"{equity:>13.2f}x" + f"{maxdd:>15.1f}%"
        print(line)
