"""Tendencia con stop ATR (familia: seguimiento de tendencia).

Filtro de tendencia mayor: EMA(50). Disparo: cruce EMA(9/21) en la
direccion del filtro (ignora cruces en contra de la tendencia mayor). SL
inicial y trailing (chandelier exit) a `ATR_MULTIPLIER * ATR(14)` del
precio. Parametros fijos de literatura, sin optimizar (docs/FASE2_PLAN.md).
"""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import atr, ema
from app.strategies.base import BaseStrategy, Signal

ATR_MULTIPLIER = 2.5


class TrendATRStopStrategy(BaseStrategy):
    """Tendencia con stop ATR: cruce de EMAs a favor de la EMA mayor, con SL y trailing por ATR.

    Dispara con el cruce EMA(fast)/EMA(slow) solo en la direccion del filtro EMA(trend)
    (ignora los cruces en contra). El SL inicial y el trailing (chandelier exit) estan a
    `ATR_MULTIPLIER` (2.5) veces el ATR del precio. Parametros fijos de literatura, sin
    optimizar. Nombre: `trend_atr_stop_<fast>_<slow>_<trend>`.

    Attributes:
        fast: periodo de la EMA rapida.
        slow: periodo de la EMA lenta.
        trend: periodo de la EMA de tendencia.
        atr_period: periodo del ATR.

    Raises:
        ValueError: al construirla, si no se cumple `fast < slow < trend`.
    """
    def __init__(
        self, fast: int = 9, slow: int = 21, trend: int = 50, atr_period: int = 14
    ) -> None:
        if not (fast < slow < trend):
            raise ValueError("se requiere fast < slow < trend")
        self.fast = fast
        self.slow = slow
        self.trend = trend
        self.atr_period = atr_period
        self.name = f"trend_atr_stop_{fast}_{slow}_{trend}"

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrega las columnas `ema_trend`, `ema_fast`, `ema_slow` y `atr`.

        Args:
            df: velas con `high`, `low` y `close`.

        Returns:
            Una copia de `df` con los indicadores.
        """
        df = df.copy()
        df["ema_trend"] = ema(df["close"], self.trend)
        df["ema_fast"] = ema(df["close"], self.fast)
        df["ema_slow"] = ema(df["close"], self.slow)
        df["atr"] = atr(df, self.atr_period)
        return df

    def evaluate(self, df: pd.DataFrame) -> Signal:
        """LONG con cruce alcista sobre la EMA de tendencia; SHORT con cruce bajista bajo ella.

        Args:
            df: velas (con las columnas precalculadas si existen).

        Returns:
            La senal; `HOLD` con menos de `trend + 1` velas, sin cruce o contra la tendencia.
        """
        if len(df) < self.trend + 1:
            return Signal.HOLD

        close = df["close"]
        has_cache = "ema_trend" in df.columns
        ema_trend = df["ema_trend"] if has_cache else ema(close, self.trend)
        ema_fast = df["ema_fast"] if has_cache else ema(close, self.fast)
        ema_slow = df["ema_slow"] if has_cache else ema(close, self.slow)

        prev_diff = ema_fast.iloc[-2] - ema_slow.iloc[-2]
        curr_diff = ema_fast.iloc[-1] - ema_slow.iloc[-1]
        crossed_up = prev_diff <= 0 and curr_diff > 0
        crossed_down = prev_diff >= 0 and curr_diff < 0

        above_trend = close.iloc[-1] > ema_trend.iloc[-1]
        below_trend = close.iloc[-1] < ema_trend.iloc[-1]

        if crossed_up and above_trend:
            return Signal.LONG
        if crossed_down and below_trend:
            return Signal.SHORT
        return Signal.HOLD

    def _atr_last(self, df: pd.DataFrame) -> float:
        series = df["atr"] if "atr" in df.columns else atr(df, self.atr_period)
        return series.iloc[-1]

    def stop_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """SL inicial a `ATR_MULTIPLIER` veces el ATR de la entrada, en contra de la direccion.

        Args:
            df: velas hasta la de la senal.
            side: direccion de la entrada.
            entry_price: precio de entrada.

        Returns:
            El precio del SL, o `None` si el ATR aun no esta definido.
        """
        distance = ATR_MULTIPLIER * self._atr_last(df)
        if pd.isna(distance):
            return None
        return entry_price - distance if side == Signal.LONG else entry_price + distance

    def trailing_distance(self, df: pd.DataFrame) -> float | None:
        """Distancia del trailing: `ATR_MULTIPLIER` veces el ultimo ATR.

        Args:
            df: velas hasta la de la senal.

        Returns:
            La distancia, o `None` si el ATR aun no esta definido.
        """
        distance = ATR_MULTIPLIER * self._atr_last(df)
        return None if pd.isna(distance) else float(distance)
