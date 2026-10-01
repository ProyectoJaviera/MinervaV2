"""Estrategia de cruce de EMAs (una de las 3-5 candidatas a backtestear en
Fase 2; en Fase 1 se usa directo, sin backtest, solo para validar la vertical
minima end-to-end)."""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import ema
from app.strategies.base import BaseStrategy, Signal


class EMACrossStrategy(BaseStrategy):
    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        if fast >= slow:
            raise ValueError("fast debe ser menor que slow")
        self.fast = fast
        self.slow = slow
        self.name = f"ema_cross_{fast}_{slow}"

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ema(df["close"], self.fast)
        df["ema_slow"] = ema(df["close"], self.slow)
        return df

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < 2:
            return Signal.HOLD

        ema_fast = df["ema_fast"] if "ema_fast" in df.columns else ema(df["close"], self.fast)
        ema_slow = df["ema_slow"] if "ema_slow" in df.columns else ema(df["close"], self.slow)

        prev_diff = ema_fast.iloc[-2] - ema_slow.iloc[-2]
        curr_diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]

        if prev_diff <= 0 and curr_diff > 0:
            return Signal.LONG
        if prev_diff >= 0 and curr_diff < 0:
            return Signal.SHORT
        return Signal.HOLD
