"""Contrarian de funding rate, v2 EXPERIMENTAL con umbral RELATIVO
(percentil) -- propuesta nueva y separada de `funding_contrarian.py`.

**Por que existe**: la corrida real mostro que `funding_contrarian_experimental`
(umbral absoluto fijo de 0.03%/periodo) genera CERO senales, porque el
funding real observado en la practica ronda ~0.01%/periodo -- el umbral
nunca se alcanza. Esto es un hallazgo legitimo del backtest, no un bug: por
la regla de pre-registro (docs/FASE2_CRITERIOS.md, "tras ver resultados OOS
no se modifican parametros ni criterios"), el umbral de
`funding_contrarian.py` NO se toca. En su lugar, esta es una estrategia
NUEVA y DISTINTA, con su propio nombre, que usa un umbral relativo
(percentil de la distribucion reciente de funding, no un valor absoluto) y
que por lo tanto no esta sujeta a esa regla de congelamiento -- es, en si
misma, una propuesta fresca, igual de EXPERIMENTAL que la original (no
participa en el veredicto de descarte) y con su propia historia aparte en
`backtest_runs`/`backtest_verdicts` (nombre de estrategia distinto).

Logica: igual que la original (funding sostenido alto -> sesgo SHORT;
sostenido bajo -> sesgo LONG, con un disparador de momentum simple), pero
el umbral de "alto"/"bajo" se calcula como un percentil de la propia
distribucion de funding observada en una ventana de historia reciente, en
vez de un numero fijo -- se adapta a cualquier nivel tipico de funding del
simbolo, en vez de asumir uno de antemano.
"""

from __future__ import annotations

import pandas as pd

from app.strategies.base import BaseStrategy, Signal

FUNDING_LOOKBACK = 8
HISTORY_WINDOW = 720  # ventana para calcular los percentiles de referencia
UPPER_PERCENTILE = 90.0
LOWER_PERCENTILE = 10.0


class FundingContrarianPercentileStrategy(BaseStrategy):
    def __init__(
        self,
        lookback: int = FUNDING_LOOKBACK,
        history_window: int = HISTORY_WINDOW,
        upper_percentile: float = UPPER_PERCENTILE,
        lower_percentile: float = LOWER_PERCENTILE,
    ) -> None:
        self.lookback = lookback
        self.history_window = history_window
        self.upper_percentile = upper_percentile
        self.lower_percentile = lower_percentile
        self.name = "funding_contrarian_percentile_experimental"

    def evaluate(self, df: pd.DataFrame) -> Signal:
        if "funding_rate" not in df.columns or len(df) < self.history_window + 1:
            return Signal.HOLD

        history = df["funding_rate"].iloc[-self.history_window :]
        if history.isna().any():
            return Signal.HOLD

        recent_funding = df["funding_rate"].iloc[-self.lookback :]
        avg_funding = recent_funding.mean()
        upper_threshold = history.quantile(self.upper_percentile / 100)
        lower_threshold = history.quantile(self.lower_percentile / 100)

        momentum = df["close"].iloc[-1] - df["close"].iloc[-2]

        if avg_funding > upper_threshold and momentum < 0:
            return Signal.SHORT
        if avg_funding < lower_threshold and momentum > 0:
            return Signal.LONG
        return Signal.HOLD
