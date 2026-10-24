"""Estrategia de cruce de EMAs (una de las 3-5 candidatas a backtestear en
Fase 2; en Fase 1 se usa directo, sin backtest, solo para validar la vertical
minima end-to-end)."""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import ema
from app.strategies.base import BaseStrategy, Signal


class EMACrossStrategy(BaseStrategy):
    """Cruce de dos EMAs: LONG cuando la rapida cruza hacia arriba, SHORT hacia abajo.

    Sin SL/TP propios: el motor usa el respaldo porcentual. Su nombre es
    `ema_cross_<fast>_<slow>` (la vigente es `ema_cross_9_21`).

    Attributes:
        fast: periodo de la EMA rapida.
        slow: periodo de la EMA lenta (mayor que `fast`).

    Raises:
        ValueError: al construirla, si `fast` no es menor que `slow`.
    """
    def __init__(self, fast: int = 9, slow: int = 21) -> None:
        if fast >= slow:
            raise ValueError("fast debe ser menor que slow")
        self.fast = fast
        self.slow = slow
        self.name = f"ema_cross_{fast}_{slow}"

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrega las columnas `ema_fast` y `ema_slow`.

        Args:
            df: velas con la columna `close`.

        Returns:
            Una copia de `df` con las dos EMAs.
        """
        df = df.copy()
        df["ema_fast"] = ema(df["close"], self.fast)
        df["ema_slow"] = ema(df["close"], self.slow)
        return df

    def evaluate(self, df: pd.DataFrame) -> Signal:
        """LONG si la EMA rapida cruza hacia arriba a la lenta en la ultima vela; SHORT al reves.

        El cruce se mide con la diferencia rapida menos lenta: pasa de <= 0 a > 0 (LONG) o de
        >= 0 a < 0 (SHORT).

        Args:
            df: velas (con `ema_fast` y `ema_slow` si se precalculo; si no, las calcula).

        Returns:
            La senal; `HOLD` con menos de 2 velas o sin cruce.
        """
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
