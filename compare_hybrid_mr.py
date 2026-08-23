#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_hybrid_mr.py — HybridMR_A / HybridMR_B vs ADX20_TP7 + ATRRankRSI
27-window walk-forward + window-level complementarity analysis
Usage:
  python3 compare_hybrid_mr.py --csv download/xauusd-m15-bid-2013-01-01-2026-06-10.csv
"""
from __future__ import annotations
import argparse, math, os, sys, time
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from backtest_forex import (DataLoader, prepare_data, BacktestEngine,
                             FastHybridTrendPullback, compute_metrics)
from strategy_sideways import ATRRankRSI, HybridMR
from walk_forward_regime import build_windows, gold_return_pct, regime_tag, window_metrics

SPREAD=0.10; COMM=3.50; START=10_000.0; RISK_PCT=0.30; WINDOW_MONTHS=6
MAX_HOLD_TREND=64; MAX_HOLD_MR=32
PF_THRESHOLD=1.05; WIN_THRESHOLD=15


def _cfg(mh):
    c=ForexConfig(); c.total_capital_usd=START; c.risk_per_trade_pct=RISK_PCT
    c.partial_tp_atr=999.0; c.partial_tp_frac=0.0; c.move_sl_to_breakeven=False
    c.max_hold_bars=mh; return c


def run_strategy(d, strat, mh):
    eng=BacktestEngine(d,_cfg(mh),strat,spread_price=SPREAD,commission_per_lot=COMM,symbol="XAUUSD")
    eng.run(quiet=True, do_precompute=False)
    ov=compute_metrics(eng.trades,eng.equity_curve,START)
    avg=(sum(t.get("bars_held",0) for t in eng.trades)/len(eng.trades) if eng.trades else 0)
    return eng.trades, ov, avg


def windows_stats(trades, win_info):
    eq=START
    for t in trades:
        t["_norm_pnl"]=t["net_pnl"]*(START/eq) if eq>0 else t["net_pnl"]
        t["_entry_dt"]=pd.Timestamp(t["entry_ts"]); eq=t["equity_after"]
    pf_all=pf_ds=n_all=n_ds=0
    for wi in win_info:
        wt=[t for t in trades if wi["start"]<=t["_entry_dt"]<wi["end"]]
        m=window_metrics(wt,START)
        if m["trades"]==0: continue
        n_all+=1; is_ds=wi["regime"] in ("DOWN","SIDEWAYS")
        if is_ds: n_ds+=1
        pf=m["pf"]
        if pf is not None and (math.isinf(pf) or pf>1.0):
            pf_all+=1
            if is_ds: pf_ds+=1
    return pf_all, n_all, pf_ds, n_ds


def period_stats(trades, pfrom, pto):
    ts=[t for t in trades if pfrom<=t["entry_ts"][:10]<pto]
    if not ts: return None
    eq=[START]
    for t in ts: eq.append(eq[-1]+t["net_pnl"])
    ret=(eq[-1]-START)/START*100; peak=START; dd=0
    for e in eq:
        if e>peak: peak=e
        d=(peak-e)/peak*100
        if d>dd: dd=d
    dd=dd or 0.01; cal=ret/dd
    gw=sum(t["net_pnl"] for t in ts if t["net_pnl"]>0)
    gl=abs(sum(t["net_pnl"] for t in ts if t["net_pnl"]<=0))
    pf=gw/gl if gl>0 else float("inf")
    return dict(n=len(ts), pf=pf, ret=ret, dd=dd, calmar=cal)


def wnd_pf(trades, wi):
    wt=[t for t in trades if wi["start"]<=pd.Timestamp(t["entry_ts"])<wi["end"]]
    gw=sum(t["net_pnl"] for t in wt if t["net_pnl"]>0)
    gl=abs(sum(t["net_pnl"] for t in wt if t["net_pnl"]<=0))
    pf=gw/gl if gl>0 else (float("inf") if gw>0 else 0.0)
    return len(wt), round(pf,3)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args=ap.parse_args(); t0=time.time()
    W=108
    print(); print("="*W)
    print(" HybridMR_A (all filters) | HybridMR_B (no H1 ADX)  vs  ADX20_TP7 + ATRRankRSI")
    print(f" Threshold: PF > {PF_THRESHOLD}  AND  Win/27 > {WIN_THRESHOLD}/27")
    print("="*W)

    print("  [load] อ่าน CSV ...", flush=True)
    loader=DataLoader(log_fn=lambda *a,**k: None)
    cfg0=ForexConfig(); cfg0.total_capital_usd=START
    df,_=loader.load("XAUUSD",99.0,cfg0,csv_path=args.csv,allow_synthetic=True)
    print(f"  [load] {len(df):,} bars  {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}  ({time.time()-t0:.1f}s)", flush=True)
    d=prepare_data(df)
    if d is None: print("[ERROR]"); sys.exit(1)
    windows=build_windows(df["timestamp"].iloc[0],df["timestamp"].iloc[-1],WINDOW_MONTHS)
    win_info=[dict(start=ws,end=we,gold_ret=gold_return_pct(df,ws,we),regime=regime_tag(gold_return_pct(df,ws,we))) for ws,we in windows]
    print(f"  [windows] {len(windows)} windows × {WINDOW_MONTHS}mo\n", flush=True)

    results=[]

    # 1. ADX20_TP7
    print("  [1/4] ADX20_TP7 ...", end="", flush=True); t1=time.time()
    tr=FastHybridTrendPullback(); tr.ADX_MIN=20; tr.sl_atr=3.0; tr.tp_atr=7.0
    tr.trail_atr_mult=999.0; tr.trail_activation_atr=999.0; tr.precompute(d)
    trd,ov,avg=run_strategy(d,tr,MAX_HOLD_TREND)
    pfa,na,pfd,nd=windows_stats(trd,win_info)
    results.append(dict(label="ADX20_TP7",trades=trd,ov=ov,avg=avg,pfa=pfa,na=na,pfd=pfd,nd=nd))
    print(f" {len(trd):,} trades  ({time.time()-t1:.1f}s)", flush=True)

    # 2. ATRRankRSI (best single sideways so far)
    print("  [2/4] ATRRankRSI (best single) ...", end="", flush=True); t2=time.time()
    arr=ATRRankRSI(); arr.precompute(d)
    trd,ov,avg=run_strategy(d,arr,MAX_HOLD_MR)
    pfa,na,pfd,nd=windows_stats(trd,win_info)
    results.append(dict(label="ATRRankRSI",trades=trd,ov=ov,avg=avg,pfa=pfa,na=na,pfd=pfd,nd=nd))
    print(f" {len(trd):,} trades  ({time.time()-t2:.1f}s)", flush=True)

    # 3. HybridMR_A — all filters (ATR rank + H1 ADX + BB + RSI + session)
    print("  [3/4] HybridMR_A (ATR-rank + H1-ADX + BB + RSI + session) ...", end="", flush=True); t3=time.time()
    hmr_a=HybridMR(); hmr_a.REQUIRE_H1_ADX=True; hmr_a.precompute(d)
    trd,ov,avg=run_strategy(d,hmr_a,MAX_HOLD_MR)
    pfa,na,pfd,nd=windows_stats(trd,win_info)
    results.append(dict(label="HybridMR_A",trades=trd,ov=ov,avg=avg,pfa=pfa,na=na,pfd=pfd,nd=nd))
    print(f" {len(trd):,} trades  ({time.time()-t3:.1f}s)", flush=True)

    # 4. HybridMR_B — no H1 ADX (ATR rank + BB + RSI + session)
    print("  [4/4] HybridMR_B (ATR-rank + BB + RSI + session, no H1-ADX) ...", end="", flush=True); t4=time.time()
    hmr_b=HybridMR(); hmr_b.REQUIRE_H1_ADX=False; hmr_b.precompute(d)
    trd,ov,avg=run_strategy(d,hmr_b,MAX_HOLD_MR)
    pfa,na,pfd,nd=windows_stats(trd,win_info)
    results.append(dict(label="HybridMR_B",trades=trd,ov=ov,avg=avg,pfa=pfa,na=na,pfd=pfd,nd=nd))
    print(f" {len(trd):,} trades  ({time.time()-t4:.1f}s)", flush=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(); print("="*W)
    print(" RESULTS — full 13yr + 27-window robustness")
    print("="*W)
    print(f"  {'Strategy':<14} {'trades':>7} {'PF':>6} {'Ret%':>8} {'MaxDD':>6} {'Calmar':>7} {'Win/27':>7} {'D+S/DS':>7} {'avg_b':>6}  Verdict")
    print("  "+"-"*W)
    for r in results:
        ov=r["ov"]; ret=(ov.get("final_equity",START)-START)/START*100
        dd=ov.get("max_dd_pct",0) or 0.01; cal=ret/dd; pf=ov.get("profit_factor",0) or 0
        if r["label"]=="ADX20_TP7": verdict="  BASELINE"
        else:
            pp=pf>PF_THRESHOLD; pw=r["pfa"]>WIN_THRESHOLD
            verdict=("  ✅ PASS" if pp and pw else ("  🟡 partial" if pp or pw else "  ❌ fail"))
        print(f"  {r['label']:<14} {len(r['trades']):>7,} {pf:>6.3f} {ret:>+7.0f}% {dd:>5.1f}% {cal:>7.1f} {r['pfa']:>3}/{r['na']:<3} {r['pfd']:>3}/{r['nd']:<3} {r['avg']:>5.1f}{verdict}")

    # ── Period breakdown ──────────────────────────────────────────────────────
    PERIODS=[("IS  2013-2020","2013-01-01","2020-01-01"),
             ("VAL 2020-2022","2020-01-01","2022-01-01"),
             ("OOS 2022-2026","2022-01-01","2026-06-10")]
    print(); print("="*W); print(" PERIOD BREAKDOWN"); print("="*W)
    for plabel,pfrom,pto in PERIODS:
        print(f"\n  [{plabel}]")
        print(f"  {'Strategy':<14} {'trades':>7} {'PF':>6} {'Ret%':>8} {'MaxDD':>6} {'Calmar':>7}")
        print("  "+"-"*55)
        for r in results:
            m=period_stats(r["trades"],pfrom,pto)
            if not m: print(f"  {r['label']:<14}  —"); continue
            flag=""
            if plabel.startswith("OOS"):
                flag="  ✅" if m["calmar"]>=20 else ("  🟡" if m["calmar"]>=10 else "  ❌")
            print(f"  {r['label']:<14} {m['n']:>7,} {m['pf']:>6.3f} {m['ret']:>+7.0f}% {m['dd']:>5.1f}% {m['calmar']:>7.1f}{flag}")

    # ── Window-level complementarity ──────────────────────────────────────────
    print(); print("="*W)
    print(" WINDOW-LEVEL — ADX20_TP7 vs HybridMR_B (best combined)  [complementarity check]")
    print("="*W)
    adx_trades  = results[0]["trades"]
    best_hybrid = results[3]["trades"]   # HybridMR_B
    arr_trades  = results[1]["trades"]
    print(f"\n  {'Window':<12} {'Regime':<7} | {'ADX PF':>7} | {'ARR':>5} {'ARR PF':>7} | {'HMR-B':>6} {'HMR-B PF':>8} | Complement?")
    print("  "+"-"*75)
    for wi in win_info:
        label=wi["start"].strftime("%Y-%m"); regime=wi["regime"][:4]
        nt,pft=wnd_pf(adx_trades,wi)
        na,pfa_=wnd_pf(arr_trades,wi)
        nh,pfh=wnd_pf(best_hybrid,wi)
        comp=""
        if pft<1.0:
            if pfh>1.0: comp="✅ HMR-B"
            elif pfa_>1.0: comp="✅ ARR"
            else: comp="❌ both lose"
        print(f"  {label:<12} {regime:<7}  {pft:>7.3f}    {na:>5}  {pfa_:>7.3f}    {nh:>6}  {pfh:>8.3f}  {comp}")

    print(); print(f"  เสร็จใน {time.time()-t0:.1f}s"); print("="*W)


if __name__=="__main__":
    main()
