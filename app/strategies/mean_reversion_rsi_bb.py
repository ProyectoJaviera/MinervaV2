"""Reversion a la media en rango (familia: mean reversion).

Entra LONG cuando RSI(14) < 30 Y el cierre toca/cruza la banda de Bollinger
inferior (20, 2); SHORT simetrico con RSI > 70 y banda superior. Salida
objetivo: banda media (SMA20); SL a `ATR_MULTIPLIER * ATR(14)` en contra,
para acotar el caso en que el precio sigue alejandose en vez de revertir.
Familia deliberadamente distinta a la de tendencia (docs/FASE2_PLAN.md).
"""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import atr, bollinger_bands, rsi
from app.strategies.base import BaseStrategy, Signal

ATR_MULTIPLIER = 2.0


class MeanReversionRSIBBStrategy(BaseStrategy):
    def __init__(
        self, rsi_period: int = 14, bb_period: int = 20, bb_std: float = 2.0,
        oversold: float = 30.0, overbought: float = 70.0, atr_period: int = 14,
    ) -> None:
        self.rsi_period = rsi_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.oversold = oversold
        self.overbought = overbought
        self.atr_period = atr_period
        self.name = f"mean_reversion_rsi{rsi_period}_bb{bb_period}"

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if len(df) < max(self.rsi_period, self.bb_period) + 1:
            return Signal.HOLD

        close = df["close"]
        r = rsi(close, self.rsi_period)
        upper, _middle, lower = bollinger_bands(close, self.bb_period, self.bb_std)

        if r.iloc[-1] < self.oversold and close.iloc[-1] <= lower.iloc[-1]:
            return Signal.LONG
        if r.iloc[-1] > self.overbought and close.iloc[-1] >= upper.iloc[-1]:
            return Signal.SHORT
        return Signal.HOLD

    def take_profit_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        _upper, middle, _lower = bollinger_bands(df["close"], self.bb_period, self.bb_std)
        value = middle.iloc[-1]
        return None if pd.isna(value) else float(value)

    def stop_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        distance = ATR_MULTIPLIER * atr(df, self.atr_period).iloc[-1]
        if pd.isna(distance):
            return None
        return entry_price - distance if side == Signal.LONG else entry_price + distance
