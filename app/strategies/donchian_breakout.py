"""Ruptura de canal Donchian (familia: momentum/breakout sistematico).

LONG si el cierre supera el maximo de las `period` velas PREVIAS (sin
contar la vela actual); SHORT si cae bajo el minimo de las previas. SL en
el punto medio del canal vigente. Logica de disparo distinta a un cruce de
medias -- familia de ruptura de rango (docs/FASE2_PLAN.md).
"""

from __future__ import annotations

import pandas as pd

from app.indicators.engine import donchian_channel
from app.strategies.base import BaseStrategy, Signal


class DonchianBreakoutStrategy(BaseStrategy):
    """Ruptura del canal de Donchian (momentum o breakout sistematico).

    LONG si el cierre supera el maximo de las `period` velas PREVIAS (sin contar la actual);
    SHORT si cae bajo el minimo de las previas. SL en el punto medio del canal vigente. Se
    evalua en 4h y 1d. Nombre: `donchian_breakout_<period>`.

    Attributes:
        period: ventana del canal.
    """
    def __init__(self, period: int = 20) -> None:
        self.period = period
        self.name = f"donchian_breakout_{period}"

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Agrega las columnas `donchian_upper` y `donchian_lower`.

        Args:
            df: velas con `high` y `low`.

        Returns:
            Una copia de `df` con el canal.
        """
        df = df.copy()
        upper, lower = donchian_channel(df, self.period)
        df["donchian_upper"], df["donchian_lower"] = upper, lower
        return df

    def evaluate(self, df: pd.DataFrame) -> Signal:
        """LONG o SHORT si el cierre rompe el canal calculado con las velas previas.

        El canal se desplaza una vela (`shift(1)`) para no comparar el cierre contra un canal
        que ya lo incluye, lo que seria trivialmente falso.

        Args:
            df: velas (con el canal precalculado si existe).

        Returns:
            La senal; `HOLD` con menos de `period + 2` velas, canal indefinido o sin ruptura.
        """
        if len(df) < self.period + 2:
            return Signal.HOLD

        if "donchian_upper" in df.columns:
            upper, lower = df["donchian_upper"], df["donchian_lower"]
        else:
            upper, lower = donchian_channel(df, self.period)
        # Canal calculado con las velas PREVIAS (shift 1): evita comparar el
        # cierre contra un canal que ya incluye esa misma vela (trivialmente
        # siempre verdadero/falso).
        prior_upper = upper.shift(1)
        prior_lower = lower.shift(1)
        close = df["close"]

        if pd.isna(prior_upper.iloc[-1]) or pd.isna(prior_lower.iloc[-1]):
            return Signal.HOLD

        if close.iloc[-1] > prior_upper.iloc[-1]:
            return Signal.LONG
        if close.iloc[-1] < prior_lower.iloc[-1]:
            return Signal.SHORT
        return Signal.HOLD

    def stop_price(self, df: pd.DataFrame, side: Signal, entry_price: float) -> float | None:
        """El punto medio del canal vigente (el de la ultima vela, que la incluye).

        Args:
            df: velas hasta la de la senal.
            side: direccion de la entrada (no cambia el nivel).
            entry_price: precio de entrada (no se usa).

        Returns:
            El precio del SL, o `None` si el canal aun no esta definido.
        """
        if "donchian_upper" in df.columns:
            upper, lower = df["donchian_upper"], df["donchian_lower"]
        else:
            upper, lower = donchian_channel(df, self.period)
        if pd.isna(upper.iloc[-1]) or pd.isna(lower.iloc[-1]):
            return None
        midpoint = (upper.iloc[-1] + lower.iloc[-1]) / 2
        return float(midpoint)
