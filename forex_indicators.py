#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 forex_indicators.py  —  Technical Indicators (Market-Agnostic)
================================================================================
ลอจิกคำนวณ indicators เหมือนกับ crypto ทุกประการ
คณิตศาสตร์ไม่สนใจว่าราคาคือ BTC หรือ EUR/USD

ปรับ:
  - EMA_TREND: 120 → 96  (Forex trend cycle สั้นกว่าเล็กน้อย)
  - BO_WIN: 48 → 48      (เหมือนเดิม)
================================================================================
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Signal:
    action: str = "HOLD"    # BUY | SELL | HOLD
    reason: str = ""


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """เพิ่ม indicators ทุกตัวที่กลยุทธ์ต้องการ.

    เหมือน crypto ทุกตัว — ค่า window ปรับให้เหมาะกับ Forex 15m:
      MR_WIN=20, ATR_WIN=14, BO_WIN=48 → เดิม
      EMA_TREND=96 (จาก 120), EMA_FAST=24, EMA_SLOW=96, ROC_WIN=48
    """
    df = df.copy()
    MR_WIN    = 20
    ATR_WIN   = 14
    BO_WIN    = 48
    EMA_TREND = 96   # ลดจาก 120: Forex major pairs มี cycle สั้นกว่า crypto เล็กน้อย
    EMA_FAST  = 24
    EMA_SLOW  = 96
    ROC_WIN   = 48

    # mean-reversion: z-score
    df["sma"]    = df["close"].rolling(MR_WIN).mean()
    df["std"]    = df["close"].rolling(MR_WIN).std()
    df["zscore"] = (df["close"] - df["sma"]) / df["std"]

    # ATR (Average True Range) — หน่วยเดียวกับราคา
    prev_c = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_c).abs(),
        (df["low"]  - prev_c).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_WIN).mean()

    # Donchian channel (.shift(1) → ไม่มี look-ahead)
    df["donch_hi"] = df["high"].rolling(BO_WIN).max().shift(1)
    df["donch_lo"] = df["low"].rolling(BO_WIN).min().shift(1)

    # EMA
    df["ema"]      = df["close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST,  adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW,  adjust=False).mean()

    # ROC (Rate of Change)
    df["roc"] = df["close"] / df["close"].shift(ROC_WIN) - 1.0

    return df


def build_data_dict(df: pd.DataFrame) -> dict:
    """แปลง DataFrame → dict ของ numpy arrays สำหรับ strategy.signal()."""
    col_map = [
        ("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"),
        ("atr", "atr"), ("zscore", "zscore"),
        ("donch_hi", "donch_hi"), ("donch_lo", "donch_lo"),
        ("ema", "ema"), ("ema_fast", "ema_fast"), ("ema_slow", "ema_slow"),
        ("roc", "roc"),
    ]
    d = {k: df[col].to_numpy(float) for k, col in col_map}
    d["ts"] = df["timestamp"].astype(str).to_numpy()
    return d
