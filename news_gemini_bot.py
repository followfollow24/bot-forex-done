#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_gemini_bot.py -- 2026-08-10. Live news-driven trading signal using
Gemini + Google Search grounding, trading XAUUSDc / BTCUSDc / ETHUSDc.

[!!] UNVALIDATED STRATEGY -- unlike every other bot in this repo, this one
has NO historical backtest (an LLM's news judgement today is not the same
as its judgement on 2020 data, so there is nothing meaningful to backtest).
The user was told this explicitly and chose to go live immediately anyway,
small size, with Telegram alerts on every decision. Every safety net below
exists BECAUSE of that -- read them before loosening any of them.

Design (all deliberate, not defaults):
  - Poll cadence: once per NEWS_POLL_MIN minutes (default 45). News-driven
    setups do not need bar-close timing; a fixed wall-clock cadence keeps
    Gemini spend and API quota bounded and predictable.
  - Source gating: Gemini must return structured JSON (schema below) citing
    a source name/URL per candidate. The code -- NOT the model -- checks the
    source domain against TIER1_DOMAINS. A model that just says "trust me"
    with no checkable source is treated as unsourced and skipped.
  - Confidence gate: candidates below CONF_MIN are skipped.
  - Dedup: a story already acted on (by URL) blocks re-entry on the same
    symbol for DEDUP_HOURS, so one headline can't trigger repeated entries
    as it gets rephrased across poll cycles.
  - Sizing: EXACT same pip_size/get_pip_value_live() pattern as the proven
    H1 bots (forex_live_bot_gold_cwider.py _open_position()) and the
    2026-08-10-fixed daily sleeves -- never hand-roll lot math again, see
    feedback_cent_account_sizing memory. risk_pct defaults far below every
    other live bot (0.15%) because there is no historical edge estimate to
    size against.
  - Stop-loss: MANDATORY on every order, no exceptions. sl = 2.5xATR14(H1)
    from MT5 (real market volatility, not asked from the LLM). No take-
    profit; a TIME_STOP_HOURS flat close (default 18h) is the exit for a
    news-driven move that hasn't kept going, since these setups are not
    meant to be held like a trend position.
  - Consecutive-loss circuit breaker: MAX_CONSEC_LOSSES losses in a row
    (default 3) auto-writes a STOP file and alerts -- requires a human to
    clear it. There is no backtest to say "3 losses is normal variance",
    so the breaker is deliberately tight.
  - Fail-safe on EVERY external call (Gemini quota/error, MT5 timeout,
    malformed JSON): skip this cycle, alert, never guess. A skipped cycle
    is always safe; a guessed one is not.

CLI:
  python news_gemini_bot.py --allow-real
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import logging.handlers
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forex_config import ForexConfig
from forex_executor import MT5Connector, ForexOrderExecutor, _cfg_has_credentials

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MAGIC = 669001  # 669xxx family: unused by any existing bot (555/666/667/668)

SYMBOLS = {
    "XAUUSD": {"canon": "XAUUSD", "label": "gold", "ps": None, "pv": None},
    "BTCUSDC": {"canon": "BTCUSDC", "label": "BTC", "ps": 1.0, "pv": 0.01},
    "ETHUSDC": {"canon": "ETHUSDC", "label": "ETH", "ps": 1.0, "pv": 0.01},
}

# Domains the CODE (not the model) accepts as tier-1. Deliberately narrow --
# widen only after reviewing what the model actually cites in practice.
TIER1_DOMAINS = {
    "reuters.com", "bloomberg.com", "apnews.com", "wsj.com", "ft.com",
    "cnbc.com", "federalreserve.gov", "ecb.europa.eu", "bls.gov",
    "home.treasury.gov", "coindesk.com", "theblock.co",
}

CONF_MIN = 0.70
DEDUP_HOURS = 12
NEWS_POLL_MIN = 45
TIME_STOP_HOURS = 18
MAX_CONSEC_LOSSES = 3
DEFAULT_RISK_PCT = 0.15
SL_ATR_MULT = 2.5


def _domain(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "enum": ["XAUUSD", "BTCUSDC", "ETHUSDC"]},
                    "signal": {"type": "string", "enum": ["long", "short", "none"]},
                    "confidence": {"type": "number"},
                    "headline": {"type": "string"},
                    "source_name": {"type": "string"},
                    "source_url": {"type": "string"},
                    "published_utc": {"type": "string"},
                    "reasoning": {"type": "string"},
                },
                "required": ["symbol", "signal", "confidence", "headline",
                            "source_name", "source_url", "reasoning"],
            },
        },
    },
    "required": ["candidates"],
}

PROMPT_TEMPLATE = """You are a market-moving-news scanner for a live trading system. \
Use Google Search to find REAL, VERIFIED news published in the last {lookback_min} \
minutes (current UTC time: {now_utc}) that could move XAUUSD (gold), BTCUSDC (Bitcoin), \
or ETHUSDC (Ethereum) prices. Focus on:
  - Macro: Fed/central bank rate decisions or statements, CPI/inflation prints, \
NFP/jobs data, GDP, major fiscal policy news.
  - Crypto-specific: ETF flows/approvals, exchange incidents, regulatory actions, \
major protocol or whale events.
  - Geopolitical: war, sanctions, major diplomatic events that shift safe-haven demand.

Only report a candidate if you found a SPECIFIC, checkable news article with a real \
URL and a real publish time -- never a general market commentary or your own \
speculation dressed up as news. If you find nothing meeting this bar, return an \
empty candidates list -- that is a normal and expected result most cycles.

For each candidate, give a directional call: "long" (price should rise), "short" \
(price should fall), or "none" if the news is notable but not clearly directional. \
confidence is 0.0-1.0, your genuine calibrated confidence this specific news moves \
this specific symbol in this direction within the next {time_stop_h} hours -- do \
not inflate it, most real news should score well under 0.7.

Respond ONLY via the provided JSON schema."""


def gemini_scan(api_key: str, model: str, lookback_min: int) -> list:
    """Returns a list of raw candidate dicts, or raises on any failure --
    caller must treat ANY exception as 'skip this cycle', never partial-trust
    a malformed response."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = PROMPT_TEMPLATE.format(
        lookback_min=lookback_min,
        now_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        time_stop_h=TIME_STOP_HOURS)

    # Pass 1: grounded search (must be free-form text -- the API does not
    # allow combining a search tool with a forced JSON response schema).
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    grounded_text = resp.text or ""

    # Pass 2: force the grounded findings into the strict schema. No new
    # search here -- this pass only structures what pass 1 already found,
    # so it can't invent sources pass 1 didn't cite.
    resp2 = client.models.generate_content(
        model=model,
        contents=(
            "Convert the following news findings into the required JSON "
            "schema. Do not add any candidate not already present in the "
            "findings text; if the findings text has no concrete news "
            "meeting the bar, return an empty candidates list.\n\n"
            f"FINDINGS:\n{grounded_text}"
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        ),
    )
    data = json.loads(resp2.text)
    return data.get("candidates", [])


class NewsGeminiBot:
    def __init__(self, cfg: ForexConfig, risk_pct: float, poll_min: int):
        self.cfg = cfg
        self.risk_pct = risk_pct
        self.poll_min = poll_min

        self.stop_file = os.path.join(_BASE_DIR, "STOP_NEWS_GEMINI")
        self.breaker_file = os.path.join(_BASE_DIR, "BREAKER_NEWS_GEMINI")
        self.heartbeat_file = os.path.join(_BASE_DIR, "HEARTBEAT_NEWS_GEMINI")
        self.state_file = os.path.join(_BASE_DIR, "news_gemini_state.json")
        self.log_file = os.path.join(_BASE_DIR, "forex_bot_news_gemini.log")

        self.log = self._setup_logging()
        self.connector = MT5Connector(cfg, self.log)
        self.executor = ForexOrderExecutor(self.connector, cfg, self.log)
        self._io = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mt5-io-news")
        self._mt5_timeout = 90.0

        self.gemini_key = os.environ.get("GEMINI_API_KEY", "")
        self.gemini_model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

        self.state = self._load_state()
        self.symbols: dict = {}

    # ── infra (same pattern as daily_sleeves_bot.py) ────────────────────
    def _setup_logging(self) -> logging.Logger:
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handlers: list = [logging.StreamHandler(sys.stdout)]
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                self.log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"))
        except Exception:
            pass
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
        return logging.getLogger("NewsGeminiBot")

    def _mt5(self, func, *args, **kw):
        fut = self._io.submit(func, *args, **kw)
        return fut.result(timeout=self._mt5_timeout)

    def _heartbeat(self):
        try:
            with open(self.heartbeat_file, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception:
            pass

    def _telegram(self, msg: str):
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat:
            return
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
        except Exception as e:
            self.log.warning(f"telegram failed: {e}")

    def _load_state(self) -> dict:
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except Exception:
            return {"seen_urls": {}, "positions": {}, "consec_losses": 0,
                    "last_poll": ""}

    def _save_state(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    def _own_positions(self, bsym: Optional[str] = None) -> list:
        pos = [p for p in (self._mt5(self.connector.get_open_positions) or [])
               if int(p.get("magic", 0)) == MAGIC]
        if bsym:
            pos = [p for p in pos if p.get("symbol") == bsym]
        return pos

    # ── ATR14 H1, computed from real MT5 bars (never asked from the LLM) ──
    def _atr14(self, bsym: str) -> Optional[float]:
        candles = self._mt5(self.connector.fetch_ohlcv, bsym, "1h", 60)
        if not candles or len(candles) < 20:
            return None
        trs = []
        prev_close = None
        for c in candles:
            h, l, cl = float(c[2]), float(c[3]), float(c[4])
            tr = (h - l) if prev_close is None else max(
                h - l, abs(h - prev_close), abs(l - prev_close))
            trs.append(tr)
            prev_close = cl
        trs = trs[-14:]
        if len(trs) < 14 or any(not math.isfinite(t) for t in trs):
            return None
        return sum(trs) / len(trs)

    # ── main loop ────────────────────────────────────────────────────────
    def run(self):
        if not self._mt5(self.connector.connect):
            self.log.error("MT5 connect failed")
            sys.exit(1)
        for canon in SYMBOLS:
            self.symbols[canon] = self.connector.resolve_symbol(canon)
        eq = self._mt5(self.connector.get_equity)
        self.log.info("=" * 70)
        self.log.info(f"  NEWS-GEMINI BOT  magic={MAGIC}  symbols={self.symbols}")
        self.log.info(f"  equity={eq:.2f}  risk/trade={self.risk_pct}%  "
                      f"poll={self.poll_min}min  model={self.gemini_model}")
        self.log.info(f"  kill-switch: {os.path.basename(self.stop_file)}  "
                      f"breaker: {os.path.basename(self.breaker_file)}")
        self.log.info("  ** UNVALIDATED STRATEGY -- no historical backtest exists "
                      "for this signal source. Live by explicit user decision. **")
        self.log.info("=" * 70)
        self._telegram(f"\U0001F680 START news_gemini  equity={eq:.2f}  "
                       f"risk={self.risk_pct}%/trade  poll={self.poll_min}min")

        while True:
            try:
                self._heartbeat()
                self._watch_positions()
                self._check_time_stops()
                now = datetime.now(timezone.utc)
                last = self.state.get("last_poll", "")
                due = (not last or
                      (now - datetime.fromisoformat(last)).total_seconds() >= self.poll_min * 60)
                if due:
                    if os.path.exists(self.stop_file):
                        self.log.info("[KILL-SWITCH] present -- skipping poll cycle")
                    elif os.path.exists(self.breaker_file):
                        self.log.warning("[BREAKER] consecutive-loss breaker active -- "
                                         "skipping poll cycle (clear file to resume)")
                    else:
                        self._poll_cycle()
                    self.state["last_poll"] = now.isoformat()
                    self._save_state()
            except Exception as e:
                self.log.error(f"loop error: {e}")
            time.sleep(60)

    def _watch_positions(self):
        known = self.state.get("positions", {})
        if not known:
            return
        live = {p["id"] for p in self._own_positions()}
        for k in list(known):
            info = known[k]
            if info.get("ticket") not in live:
                # closed at broker (SL or manual) -- look up the real outcome
                # from deal history so the loss-streak breaker actually sees
                # SL hits, not just time-stops.
                net = self._closed_position_net_pnl(info["ticket"])
                self._update_loss_streak(net)
                self.log.info(f"[BROKER-CLOSE] {k} position closed, pnl={net:+.2f} "
                              f"-- {info}")
                self._telegram(f"\U0001F534 CLOSED (broker) news_gemini: {k} "
                               f"entry={info.get('entry')} pnl={net:+.2f}")
                known.pop(k, None)
        self._save_state()

    def _closed_position_net_pnl(self, position_id: str) -> float:
        """Sum profit+swap+commission across a closed position's deals.
        Returns 0.0 (treated as non-loss) if history is unavailable -- a
        missing lookup must never itself trigger the breaker."""
        try:
            deals = self._mt5(self.connector.get_position_deals, position_id, 1440)
        except Exception as e:
            self.log.warning(f"deal history lookup failed for {position_id}: {e}")
            return 0.0
        return sum(d.get("profit", 0.0) + d.get("swap", 0.0) + d.get("commission", 0.0)
                  for d in deals)

    def _check_time_stops(self):
        known = self.state.get("positions", {})
        now = datetime.now(timezone.utc)
        for k, info in list(known.items()):
            opened = datetime.fromisoformat(info["opened_utc"])
            if (now - opened).total_seconds() < TIME_STOP_HOURS * 3600:
                continue
            pos = [p for p in self._own_positions(info["bsym"])
                   if p["id"] == info["ticket"]]
            if not pos:
                known.pop(k, None)
                continue
            p = pos[0]
            side = "long" if p["type"] == "BUY" else "short"
            r = self._mt5(self.executor.close_position_market, info["bsym"], side,
                          float(p["volume"]), str(p["id"]), "news-timestop")
            net = float(p.get("profit", 0.0))
            self._update_loss_streak(net)
            self.log.info(f"[TIME-STOP] closed {k} after {TIME_STOP_HOURS}h, pnl={net:+.2f}")
            self._telegram(f"⏱ TIME-STOP news_gemini: {k} closed after "
                           f"{TIME_STOP_HOURS}h, pnl={net:+.2f}")
            known.pop(k, None)
        self._save_state()

    def _update_loss_streak(self, net_pnl: float):
        if net_pnl < 0:
            self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1
        else:
            self.state["consec_losses"] = 0
        if self.state["consec_losses"] >= MAX_CONSEC_LOSSES:
            with open(self.breaker_file, "w") as f:
                f.write(f"{self.state['consec_losses']} consecutive losses as of "
                       f"{datetime.now(timezone.utc).isoformat()}")
            self.log.error(f"[BREAKER] {self.state['consec_losses']} consecutive "
                           f"losses -- auto-stopping new entries")
            self._telegram(f"⛔ news_gemini AUTO-STOPPED: "
                           f"{self.state['consec_losses']} consecutive losses. "
                           f"Delete {os.path.basename(self.breaker_file)} to resume "
                           f"after review.")

    # ── decision cycle ───────────────────────────────────────────────────
    def _poll_cycle(self):
        self.log.info("── news poll cycle ──")
        try:
            candidates = gemini_scan(self.gemini_key, self.gemini_model, self.poll_min)
        except Exception as e:
            msg = str(e)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
                self.log.warning(f"[GEMINI] quota exceeded -- skip cycle: {msg[:200]}")
                self._telegram("⚠️ news_gemini: Gemini API quota exceeded — "
                               "skipped this cycle (fail-safe, no trades)")
            else:
                self.log.error(f"[GEMINI] scan failed -- skip cycle: {msg[:300]}")
                self._telegram(f"⚠️ news_gemini: scan failed — skipped this "
                               f"cycle: {msg[:150]}")
            return

        if not candidates:
            self.log.info("[GEMINI] no qualifying candidates this cycle")
            return

        for c in candidates:
            self._evaluate_candidate(c)

    def _evaluate_candidate(self, c: dict):
        symbol = c.get("symbol", "")
        signal = c.get("signal", "none")
        conf = float(c.get("confidence", 0) or 0)
        url = c.get("source_url", "")
        src = c.get("source_name", "")
        headline = c.get("headline", "")
        reasoning = c.get("reasoning", "")
        dom = _domain(url)

        tag = f"{symbol} {signal} conf={conf:.2f} src={src}({dom}) :: {headline[:80]}"

        if signal == "none":
            self.log.info(f"[SKIP no-direction] {tag}")
            return
        if symbol not in SYMBOLS:
            self.log.warning(f"[SKIP unknown-symbol] {tag}")
            return
        if conf < CONF_MIN:
            self.log.info(f"[SKIP low-confidence] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (confidence {conf:.2f} < "
                           f"{CONF_MIN}): {headline[:150]}")
            return
        if dom not in TIER1_DOMAINS:
            self.log.info(f"[SKIP untrusted-source] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (source '{dom}' not tier-1): "
                           f"{headline[:150]}")
            return
        if not url:
            self.log.info(f"[SKIP no-url] {tag}")
            return

        seen = self.state.setdefault("seen_urls", {})
        last_seen = seen.get(url)
        if last_seen:
            age_h = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(last_seen)).total_seconds() / 3600
            if age_h < DEDUP_HOURS:
                self.log.info(f"[SKIP dedup {age_h:.1f}h ago] {tag}")
                return
        seen[url] = datetime.now(timezone.utc).isoformat()
        self._save_state()

        bsym = self.symbols[symbol]
        if self._own_positions(bsym):
            self.log.info(f"[SKIP already-positioned] {tag}")
            self._telegram(f"ℹ️ news_gemini SKIP (already have a {symbol} "
                           f"position open): {headline[:150]}")
            return

        self.log.info(f"[QUALIFIED] {tag}")
        self._enter(symbol, bsym, signal, headline, src, url, reasoning, conf)

    def _enter(self, symbol: str, bsym: str, signal: str, headline: str,
              src: str, url: str, reasoning: str, conf: float):
        atr = self._atr14(bsym)
        if not atr or not math.isfinite(atr) or atr <= 0:
            self.log.warning(f"[{symbol}] invalid ATR -- skip entry")
            self._telegram(f"⚠️ news_gemini: {symbol} ATR unavailable — "
                           f"entry skipped (fail-safe)")
            return

        eq = self._mt5(self.connector.get_equity)
        bid, ask = self._mt5(self.connector.get_current_price, bsym)
        if bid <= 0 or ask <= 0:
            self.log.warning(f"[{symbol}] invalid price -- skip entry")
            return
        long_ = signal == "long"
        px = ask if long_ else bid
        sd = SL_ATR_MULT * atr
        sl = px - sd if long_ else px + sd

        # sizing: proven pip_size/get_pip_value_live pattern (2026-08-10
        # cent-account sizing fix -- see feedback_cent_account_sizing memory)
        pip_size = self.cfg.get_pip_size(bsym)
        pip_value = self._mt5(self.connector.get_pip_value_live, bsym)
        sd_pips = sd / pip_size
        if sd_pips <= 0 or pip_value <= 0:
            self.log.warning(f"[{symbol}] invalid pip_value -- skip entry")
            return
        risk_cash = eq * self.risk_pct / 100.0
        lot = round(risk_cash / (sd_pips * pip_value), 2)
        if lot < 0.01:
            self.log.warning(f"[{symbol}] lot rounds to 0 -- skip entry")
            return
        actual_risk_pct = (sd_pips * pip_value * lot) / eq * 100.0 if eq > 0 else float("inf")
        if actual_risk_pct > self.risk_pct * 1.5:
            self.log.error(f"[{symbol}] SIZING SANITY CHECK FAILED: lot={lot} implies "
                           f"{actual_risk_pct:.2f}% > 1.5x intended {self.risk_pct}% "
                           f"-- REFUSING to open")
            self._telegram(f"⛔ news_gemini: {symbol} sizing sanity check failed "
                           f"— entry refused")
            return

        side = "long" if long_ else "short"
        result = self._mt5(self.executor.open_position, bsym, side, lot,
                          sl, 0.0, "NEWS-" + side[:4].upper())
        if not result:
            self.log.error(f"[{symbol}] open failed")
            self._telegram(f"⚠️ news_gemini: {symbol} order failed to open")
            return

        fill = float(result.get("fill_price", px) or px)
        self.state.setdefault("positions", {})[f"{symbol}-{result.get('trade_id')}"] = {
            "bsym": bsym, "ticket": str(result.get("trade_id", "") or ""),
            "side": side, "entry": fill, "sl": sl, "lot": lot,
            "opened_utc": datetime.now(timezone.utc).isoformat(),
            "headline": headline, "source": src,
        }
        self._save_state()

        self.log.info(f"[OPEN] {side.upper()} {bsym} lot={lot} fill={fill:.2f} "
                      f"sl={sl:.2f} (risk={actual_risk_pct:.2f}%)")
        self._telegram(
            f"\U0001F7E2 NEWS ENTRY {side.upper()} {bsym}\n"
            f"lot={lot}  fill={fill:.2f}  SL={sl:.2f} ({SL_ATR_MULT}xATR)\n"
            f"conf={conf:.2f}  time-stop={TIME_STOP_HOURS}h\n"
            f"headline: {headline}\n"
            f"source: {src} ({url})\n"
            f"reasoning: {reasoning[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--risk", type=float, default=DEFAULT_RISK_PCT,
                    help=f"%% equity risked per trade (default {DEFAULT_RISK_PCT} -- "
                         f"deliberately far below every other live bot; there is no "
                         f"historical edge estimate to size against)")
    ap.add_argument("--poll-min", type=int, default=NEWS_POLL_MIN,
                    help=f"minutes between Gemini news scans (default {NEWS_POLL_MIN})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-real", action="store_true")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    if not os.environ.get("GEMINI_API_KEY"):
        print("[ERROR] GEMINI_API_KEY not set (add it to .env)")
        sys.exit(1)

    cfg = ForexConfig()
    cfg.symbols = ["XAUUSD", "BTCUSDC", "ETHUSDC"]
    cfg.magic_number = MAGIC
    cfg.dry_run = args.dry_run
    cfg.allow_real = args.allow_real
    for s in ("BTCUSDC", "ETHUSDC"):
        cfg.pip_size[s] = 1.0
        cfg.pip_value_usd_approx[s] = 0.01

    if not cfg.dry_run and not _cfg_has_credentials(cfg):
        print("[ERROR] MT5 unavailable -- use --dry-run off-Windows")
        sys.exit(1)

    NewsGeminiBot(cfg, args.risk, args.poll_min).run()


if __name__ == "__main__":
    main()
