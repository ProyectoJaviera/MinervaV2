"""Contrarian de funding rate (EXPERIMENTAL -- docs/FASE2_PLAN.md seccion B.5).

Unica estrategia que no es puramente de precio: usa el funding rate como
filtro de posicionamiento de la mayoria (funding sostenido muy positivo ->
la mayoria esta LONG -> sesgo contrarian SHORT; muy negativo -> sesgo
contrarian LONG) combinado con un disparador de momentum simple para el
timing de entrada. Requiere que el DataFrame tenga una columna
`funding_rate` (el motor de backtest la agrega, alineada a cada vela); si
no esta presente, la estrategia no opera (HOLD).

Marcada explicitamente como experimental: su historia util real es mas
corta que las demas (funding_rate_history de Bitunix llega solo hasta
~2024, ver docs/FASE2_PLAN.md) y por acuerdo con el usuario NO participa en
el veredicto global de descarte -- solo se reporta.
"""

from __future__ import annotations

import pandas as pd

from app.strategies.base import BaseStrategy, Signal

FUNDING_LOOKBACK = 8  # ~8 periodos de funding recientes (tipicamente 8h cada uno)
FUNDING_THRESHOLD = 0.0003  # 0.03% por periodo, umbral de "funding alto" de referencia


class FundingContrarianStrategy(BaseStrategy):
    """Contrarian de funding con umbral absoluto (EXPERIMENTAL; no participa en el veredicto).

    Usa el funding como filtro de posicionamiento de la mayoria: promedio de los ultimos
    `lookback` periodos sobre `threshold` mas momentum negativo da SHORT; bajo `-threshold`
    con momentum positivo da LONG. Necesita la columna `funding_rate` en el DataFrame (el
    generador en vivo y el backtest la agregan); sin ella no opera. Su umbral NO se toca
    (pre-registro de la Fase 2): por eso existe la variante por percentiles.

    Attributes:
        lookback: periodos de funding que se promedian.
        threshold: umbral absoluto por periodo (0.0003 = 0.03 %).
    """
    def __init__(
        self, lookback: int = FUNDING_LOOKBACK, threshold: float = FUNDING_THRESHOLD
    ) -> None:
        self.lookback = lookback
        self.threshold = threshold
        self.name = "funding_contrarian_experimental"

    def evaluate(self, df: pd.DataFrame) -> Signal:
        """SHORT con funding alto y momentum negativo; LONG con funding bajo y momentum positivo.

        Args:
            df: velas con la columna `funding_rate`.

        Returns:
            La senal; `HOLD` si falta `funding_rate`, faltan datos o hay NaN en la ventana.
        """
        if "funding_rate" not in df.columns or len(df) < self.lookback + 1:
            return Signal.HOLD

        recent_funding = df["funding_rate"].iloc[-self.lookback :]
        if recent_funding.isna().any():
            return Signal.HOLD
        avg_funding = recent_funding.mean()

        momentum = df["close"].iloc[-1] - df["close"].iloc[-2]

        if avg_funding > self.threshold and momentum < 0:
            return Signal.SHORT
        if avg_funding < -self.threshold and momentum > 0:
            return Signal.LONG
        return Signal.HOLD
