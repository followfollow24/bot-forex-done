import sys
sys.path.insert(0, '.')
from forex_config import ForexConfig
from backtest_forex import DataLoader, prepare_data, BacktestEngine
from gold_regime_filter_real_engine import RegimeFilteredHybrid, gold_cfg, GOLD_CSV, SPREAD, COMM, START

loader = DataLoader(log_fn=lambda *a, **k: None)
cfg0 = ForexConfig(); cfg0.total_capital_usd = START
df_full, _ = loader.load("XAUUSD", 99.0, cfg0, csv_path=GOLD_CSV, allow_synthetic=True)
d = prepare_data(df_full)
strat = RegimeFilteredHybrid()
strat.sl_atr, strat.tp_atr = 3.0, 7.0
strat.trail_atr_mult, strat.trail_activation_atr = 999.0, 999.0
strat.precompute(d)
eng = BacktestEngine(d, gold_cfg(), strat, spread_price=SPREAD, commission_per_lot=COMM, symbol="XAUUSD")
eng.run(quiet=True, do_precompute=False)
trades = eng.trades

RISK_PCT_BACKTEST = 0.30
risk_levels = [0.30, 0.50, 1.0, 2.0, 3.0, 5.0]

header = "risk%".rjust(7) + "final_equity_multiplier".rjust(26) + "max_drawdown%".rjust(16) + "blown_up".rjust(12)
print(header)

for risk in risk_levels:
    equity = 1.0
    peak = 1.0
    maxdd = 0.0
    ruined = False
    for t in trades:
        entry_equity = t["equity_after"] - t["net_pnl"]
        risk_amount_backtest = entry_equity * (RISK_PCT_BACKTEST / 100.0)
        if risk_amount_backtest == 0:
            continue
        r_multiple = t["net_pnl"] / risk_amount_backtest
        equity *= (1 + r_multiple * (risk / 100.0))
        if equity <= 0:
            ruined = True
            equity = 0.0001
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        maxdd = max(maxdd, dd)
        if ruined:
            break
    blown = "YES" if (ruined or equity < 0.1) else "no"
    line = f"{risk:>6.2f}%" + f"{equity:>25.4f}x" + f"{maxdd:>15.1f}%" + f"{blown:>12}"
    print(line)
