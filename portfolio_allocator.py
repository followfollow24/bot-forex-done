#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_allocator.py -- recommends a per-bot risk multiplier from a
credibility-weighted blend of (a) each bot's own live realized track record
and (b) a backtest/walk-forward Sharpe prior, using the same inverse-vol
risk-parity idea already validated in portfolio_2edge.py (gold-trend +
BTC-combo -> Sharpe 1.40), extended with a Sharpe tilt (reward per unit of
risk, not just risk-neutral parity) and a credibility ramp so a brand-new
bot with 3 days of live data doesn't get sized off noise.

[2026-08-11] Built at the user's explicit request as a LIVE-running
recommender (not a one-off backtest). Scope decided deliberately narrow:
this script COMPUTES AND ALERTS a recommended risk% per bot -- it does NOT
restart any live bot itself. Applying a new weight is a separate,
deliberate step (see APPLYING at the bottom of this docstring) because:
  1. the 12 live bots are NOT uniformly parameterized -- most take
     --risk, btc_combo_lb takes --alloc (a different sizing paradigm,
     daily_sleeves_bot.py's combo sleeve targets a portfolio fraction, not
     a per-trade risk%), and news_gemini has no --risk CLI flag at all
     (DEFAULT_RISK_PCT is a hardcoded module constant) -- a single
     templated "restart with --risk X" step cannot honestly cover all 12.
  2. every other change to a live bot this session went through a
     verify-before-deploy step with a human in the loop; auto-restarting
     12 real-money bots on an unreviewed formula's output breaks that
     discipline for something with real drawdown consequences if the
     formula is ever wrong.

BACKTEST PRIOR HONESTY: only 3 of the 12 live bots have a Sharpe number
this session can actually cite from verified research:
  - btc_h1_manual : OOS Sharpe 1.00 (2026-08-05 adx10 retune, walk-forward)
  - gold_h1_manual: OOS Sharpe 0.57 (2026-08-05 adx10 retune, walk-forward)
  - btc_combo_lb  : OOS Sharpe 0.78 (MAR 0.59 walk-forward, portfolio_2edge.py)
The other 9 (gold_daily_breakout, gold_momentum_rsi, btc_h1_breakout,
btc_amd, btc_lqsweep, btc_tpo, eth_h1_manual, funding_contrarian,
news_gemini) have NO verified backtest Sharpe in this session's research
-- BACKTEST_PRIOR intentionally omits them rather than inventing a number.
Bots without a prior default to the fleet-neutral prior (see
FLEET_NEUTRAL_SHARPE/VOL) until their own live track record accumulates
enough credibility to matter -- meaning for now they're allocated close to
their CURRENT risk (multiplier near 1.0) until real data comes in. Add a
verified prior to BACKTEST_PRIOR as research produces one; don't guess.

Usage:
  python portfolio_allocator.py                  dry-run: compute + log +
                                                   telegram the recommendation,
                                                   change nothing
  python portfolio_allocator.py --since 2026-07-29   override the live-data
                                                   lookback start date

APPLYING a recommendation once reviewed: hand-restart the specific bot(s)
with the new --risk/--alloc value using the SAME pattern every other deploy
this session used -- capture the bot's real running command line, edit only
the risk/alloc number, stop, restart, verify. Do not template this step
until all 12 bots' sizing params are made uniform (a separate refactor).
"""
import argparse
import math
import os
import sys
import time
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---- fleet definition (magic -> variant_tag), taken verbatim from
# trade_summary.py's MAGIC_LABEL (already the source of truth for magic
# resolution across the fleet) plus news_gemini which uses a distinct
# per-symbol magic scheme (not in that table). ----
MAGIC_LABEL = {
    555143: "gold_h1_manual",
    555153: "gold_daily_breakout",
    555073: "gold_momentum_rsi",
    666120: "btc_h1_manual",
    666020: "btc_h1_breakout",
    666040: "btc_amd",
    666050: "btc_lqsweep",
    666060: "btc_tpo",
    667130: "eth_h1_manual",
    668001: "funding_contrarian",
    668002: "btc_combo_lb",
}

# current --risk (or --alloc for btc_combo_lb) per variant, captured
# verbatim from watchdog_h1.ps1's $bots array (the authoritative record of
# each bot's real running args). news_gemini omitted: no --risk CLI flag,
# excluded from allocation entirely for now (see module docstring).
CURRENT_RISK_PCT = {
    "gold_h1_manual": 0.30,
    "gold_daily_breakout": 0.50,
    "gold_momentum_rsi": 0.30,
    "btc_h1_manual": 1.00,
    "btc_h1_breakout": 1.00,
    "btc_amd": 0.50,
    "btc_lqsweep": 0.50,
    "btc_tpo": 0.30,
    "eth_h1_manual": 1.90,
    "funding_contrarian": 0.30,
    "btc_combo_lb": 0.10,   # --alloc, not --risk -- different unit, see docstring
}

# verified walk-forward/OOS Sharpe, honestly incomplete -- see docstring.
BACKTEST_PRIOR = {
    "btc_h1_manual": {"sharpe": 1.00, "vol": None},
    "gold_h1_manual": {"sharpe": 0.57, "vol": None},
    "btc_combo_lb": {"sharpe": 0.78, "vol": None},
}

# Bots with no prior AND not enough live data yet get this -- deliberately
# unremarkable (middle-of-the-pack) so an unproven bot neither vanishes nor
# dominates the allocation before it has earned either outcome.
FLEET_NEUTRAL_SHARPE = 0.5
FLEET_NEUTRAL_VOL = 0.01   # 1% daily P&L-as-fraction-of-equity, a rough
                          # order-of-magnitude placeholder for a bot with
                          # zero data of its own -- gets overridden the
                          # moment either live or backtest vol is known.

MIN_LIVE_DAYS_FOR_STATS = 14   # below this, live estimate isn't trusted at all
FULL_CREDIBILITY_DAYS = 60     # live data fully replaces the prior by here

SHARPE_FLOOR = 0.1   # score uses max(sharpe, floor) -- a strategy with a
                     # negative or near-zero blended Sharpe still gets a
                     # small nonzero weight rather than 0 or a nonsensical
                     # negative one; operator judgment (not this script)
                     # decides whether to actually kill a bad bot.
VOL_FLOOR = 0.001
MIN_MULTIPLIER = 0.4   # bounds on the recommended change vs current risk --
MAX_MULTIPLIER = 2.0   # a formula bug should not zero out or 5x any bot.


def _daily_pnl_pct_series(magic: str, tag: str, since: datetime) -> list:
    """Live daily P&L as a fraction of equity, resolved via position_id the
    same way trade_summary.py does (closing deals can carry magic=0 for a
    manually-closed position; the entry deal's magic is authoritative)."""
    if mt5 is None:
        raise RuntimeError("MetaTrader5 package unavailable -- run on the VPS")
    lookback_start = since - timedelta(days=60)
    wide_deals = mt5.history_deals_get(lookback_start, datetime.now()) or []
    entries = [d for d in wide_deals if d.entry == 0]
    pos_magic = {}
    for d in entries:
        if d.magic:
            pos_magic[d.position_id] = d.magic
    closes = [d for d in wide_deals if d.entry == 1 and d.time >= since.timestamp()]

    acc = mt5.account_info()
    equity_now = acc.equity if acc else None
    if not equity_now or equity_now <= 0:
        raise RuntimeError("could not read account equity from MT5")

    by_day = {}
    for d in closes:
        resolved_magic = d.magic if d.magic else pos_magic.get(d.position_id, 0)
        if resolved_magic != magic:
            continue
        day = datetime.fromtimestamp(d.time).strftime("%Y-%m-%d")
        net = d.profit + d.swap + d.commission
        by_day[day] = by_day.get(day, 0.0) + net

    if not by_day:
        return []
    # approximate: today's equity as the normalizing denominator for every
    # day's P&L. Imprecise vs a true daily-equity-curve reconstruction, but
    # adequate for a vol/Sharpe ESTIMATE feeding a bounded multiplier, not
    # a precision-critical figure -- and avoids needing per-day equity
    # snapshots this fleet doesn't currently record.
    return [pnl / equity_now for pnl in by_day.values()]


def _stats(returns: list):
    """(sharpe, vol) from a list of daily P&L-as-fraction-of-equity
    values, annualized (sqrt(365) -- these bots trade 24/7 including
    crypto weekends, so use calendar days not trading days).

    Rejects near-constant return series outright (vol below VOL_FLOOR)
    instead of just guarding against exactly zero: floating-point noise on
    a near-constant series can leave vol at e.g. 1e-17 rather than bit-
    exact 0, and dividing by that turns an unremarkable Sharpe into a
    nonsense ~1e18 that would otherwise swamp the credibility blend
    downstream. A real daily P&L series this flat isn't credible data
    anyway -- treat it the same as "not enough data"."""
    n = len(returns)
    if n < 2:
        return None, None
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    vol = math.sqrt(var)
    if vol < VOL_FLOOR:
        return None, None
    sharpe = (mean / vol) * math.sqrt(365)
    return sharpe, vol


def compute_recommendation(since: datetime, live_data_fn=_daily_pnl_pct_series,
                           bot_start: dict = None) -> dict:
    """Pure function (live_data_fn injectable) so the blending/weighting
    math is unit-testable without a live MT5 connection. Returns
    {tag: {sharpe, vol, credibility, source, multiplier, current_risk,
           recommended_risk}}."""
    bot_start = bot_start or {}
    now = datetime.now()
    estimates = {}

    for magic, tag in MAGIC_LABEL.items():
        if tag not in CURRENT_RISK_PCT:
            continue  # e.g. news_gemini: no --risk to size, skip entirely
        start = bot_start.get(tag, since)
        live_days = max((now - start).total_seconds() / 86400.0, 0.0)

        live_sharpe = live_vol = None
        if live_days >= MIN_LIVE_DAYS_FOR_STATS:
            returns = live_data_fn(magic, tag, since)
            live_sharpe, live_vol = _stats(returns)

        prior = BACKTEST_PRIOR.get(tag, {})
        prior_sharpe = prior.get("sharpe")
        prior_vol = prior.get("vol")

        credibility = 0.0
        if live_sharpe is not None:
            credibility = max(0.0, min(1.0, live_days / FULL_CREDIBILITY_DAYS))

        base_sharpe = prior_sharpe if prior_sharpe is not None else FLEET_NEUTRAL_SHARPE
        base_vol = prior_vol if prior_vol is not None else FLEET_NEUTRAL_VOL

        blended_sharpe = (credibility * live_sharpe + (1 - credibility) * base_sharpe
                          if live_sharpe is not None else base_sharpe)
        blended_vol = (credibility * live_vol + (1 - credibility) * base_vol
                       if live_vol is not None else base_vol)

        source = ("live+prior" if (live_sharpe is not None and prior_sharpe is not None) else
                  "live-only" if live_sharpe is not None else
                  "prior-only" if prior_sharpe is not None else
                  "fleet-neutral")

        estimates[tag] = {
            "sharpe": blended_sharpe, "vol": blended_vol,
            "credibility": credibility, "source": source,
            "live_days": live_days,
        }

    scores = {tag: max(e["sharpe"], SHARPE_FLOOR) / max(e["vol"], VOL_FLOOR)
             for tag, e in estimates.items()}
    total = sum(scores.values())
    n = len(scores)
    if total <= 0 or n == 0:
        return estimates

    for tag, e in estimates.items():
        raw_mult = (scores[tag] / total) * n
        mult = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, raw_mult))
        e["multiplier"] = mult
        e["current_risk"] = CURRENT_RISK_PCT[tag]
        e["recommended_risk"] = round(CURRENT_RISK_PCT[tag] * mult, 3)

    return estimates


def _telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": msg}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
    except Exception as e:
        print(f"[WARN] telegram send failed: {e}")


def run_once(since: datetime, prev: dict = None) -> dict:
    """One recommendation cycle: connect, compute, log, telegram, disconnect.
    Returns the estimates dict (for the daemon loop to diff against next
    time -- only alerts on a MEANINGFUL change, not every unchanged repeat)."""
    if mt5 is None or not mt5.initialize():
        print("[ERROR] MT5 unavailable -- this script computes live-blended "
              "recommendations and must run on the VPS with a connected "
              "MT5 terminal.")
        return prev or {}

    est = compute_recommendation(since)
    mt5.shutdown()

    lines = ["=" * 78, " PORTFOLIO ALLOCATOR -- recommended risk (DRY-RUN, nothing changed)",
             "=" * 78,
             f"{'bot':<22}{'source':<14}{'cred':>6}{'sharpe':>8}{'vol':>7}"
             f"{'mult':>7}{'cur%':>7}{'rec%':>7}"]
    for tag, e in sorted(est.items(), key=lambda kv: -kv[1].get("multiplier", 0)):
        lines.append(f"{tag:<22}{e['source']:<14}{e['credibility']:>6.2f}"
                     f"{e['sharpe']:>8.2f}{e['vol']:>7.4f}{e['multiplier']:>7.2f}"
                     f"{e['current_risk']:>7.2f}{e['recommended_risk']:>7.2f}")
    lines.append("-" * 78)
    lines.append("Recommendation only -- see module docstring 'APPLYING' section "
                "for why this script never restarts a live bot itself.")
    report = "\n".join(lines)
    print(report)

    # only telegram when something actually moved meaningfully -- a daily
    # daemon repeating an unchanged table every 24h is noise, not signal.
    changed = prev is None or any(
        tag not in prev or abs(e["multiplier"] - prev[tag]["multiplier"]) >= 0.10
        for tag, e in est.items())
    if changed:
        tg_lines = ["\U0001F4CA Portfolio allocator recommendation" +
                   (" (dry-run):" if prev is None else " (updated):")]
        for tag, e in sorted(est.items(), key=lambda kv: -kv[1].get("multiplier", 0)):
            tg_lines.append(f"{tag}: {e['current_risk']:.2f}% -> {e['recommended_risk']:.2f}% "
                            f"(x{e['multiplier']:.2f}, {e['source']})")
        _telegram("\n".join(tg_lines))
    else:
        print("(no bot's multiplier moved >=0.10 since last cycle -- skipped Telegram)")

    return est


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-07-29",
                    help="live-data lookback start date (YYYY-MM-DD)")
    ap.add_argument("--daemon", action="store_true",
                    help="keep running, recomputing every --interval-hours "
                         "(default: single dry-run and exit)")
    ap.add_argument("--interval-hours", type=float, default=24.0,
                    help="daemon recompute interval (default 24h -- daily "
                         "is plenty since credibility only shifts over "
                         "weeks; nothing here needs minute-level freshness)")
    args = ap.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d")

    if not args.daemon:
        run_once(since)
        return

    print(f"[DAEMON] portfolio_allocator running, recompute every "
         f"{args.interval_hours}h (recommendation-only, never restarts a bot)")
    prev = None
    while True:
        prev = run_once(since, prev=prev)
        time.sleep(args.interval_hours * 3600)


if __name__ == "__main__":
    main()
