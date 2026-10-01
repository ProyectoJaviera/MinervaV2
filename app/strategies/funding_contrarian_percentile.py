"""Contrarian de funding rate, v2 EXPERIMENTAL con umbral RELATIVO
(percentil) -- propuesta nueva y separada de `funding_contrarian.py`.

**Por que existe**: la corrida real mostro que `funding_contrarian_experimental`
(umbral absoluto fijo de 0.03%/periodo) genera pocas senales porque el
funding real observado en la practica ronda ~0.01%/periodo. Por la regla
de pre-registro (docs/FASE2_CRITERIOS.md, "tras ver resultados OOS no se
modifican parametros ni criterios"), el umbral de `funding_contrarian.py`
NO se toca. En su lugar, esta es una estrategia NUEVA y DISTINTA, con su
propio nombre, que usa un umbral relativo (percentil de la distribucion
reciente de funding, no un valor absoluto) -- es, en si misma, una
propuesta fresca, igual de EXPERIMENTAL que la original (no participa en
el veredicto de descarte) y con su propia historia aparte en
`backtest_runs`/`backtest_verdicts` (nombre de estrategia distinto).

**Correccion**: la primera version de esta estrategia tenia un bug real --
exigia `len(df) >= history_window + 1` (720+1 velas) dentro de `evaluate`,
pero el motor de backtest (`app/backtesting/engine.py`) nunca le pasa mas
de `MAX_LOOKBACK_BARS` (300) velas por vela evaluada, asi que la condicion
nunca se cumplia y la estrategia SIEMPRE devolvia HOLD (0 senales en
cualquier corrida real). Esto NO era el hallazgo legitimo que se reporto
originalmente -- era un bug de esta implementacion. Se corrige igual que
las demas estrategias con indicadores (ver `BaseStrategy.precompute`):
los percentiles moviles (causales, `rolling().quantile()`) y el promedio
reciente se calculan UNA SOLA VEZ sobre toda la serie en `precompute()`;
`evaluate()` solo lee las columnas ya calculadas, sin importar cuantas
filas reciba la vista actual.

**Funding aproximado (antes de ~2024)**: antes de que exista historial
real de funding para el simbolo, `app/backtesting/funding.py::build_funding_series`
rellena con la MEDIANA constante del funding real observado (marcado
`funding_is_approximated=True` por vela) -- en ese tramo, cualquier
percentil calculado sobre la ventana de referencia es degenerado (la
distribucion es casi constante, no refleja variabilidad real). Decision:
esta estrategia NO emite señales mientras la ventana de referencia
(`history_window` velas) contenga AUNQUE SEA UNA vela de funding
aproximado -- se exige funding 100% real en toda la ventana, no solo en
la vela actual. Documentado explicitamente aqui y en
docs/FASE2_CRITERIOS.md.
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

    def precompute(self, df: pd.DataFrame) -> pd.DataFrame:
        if "funding_rate" not in df.columns:
            return df
        df = df.copy()
        funding = df["funding_rate"]
        df["funding_recent_avg"] = funding.rolling(
            window=self.lookback, min_periods=self.lookback
        ).mean()
        df["funding_pctl_upper"] = funding.rolling(
            window=self.history_window, min_periods=self.history_window
        ).quantile(self.upper_percentile / 100)
        df["funding_pctl_lower"] = funding.rolling(
            window=self.history_window, min_periods=self.history_window
        ).quantile(self.lower_percentile / 100)

        if "funding_is_approximated" in df.columns:
            approx = df["funding_is_approximated"].astype(float)
            any_approx_in_window = approx.rolling(
                window=self.history_window, min_periods=self.history_window
            ).max()
            df["funding_history_all_real"] = any_approx_in_window == 0.0
        else:
            df["funding_history_all_real"] = False

        return df

    def evaluate(self, df: pd.DataFrame) -> Signal:
        required = {"funding_rate", "funding_pctl_upper", "funding_pctl_lower",
                    "funding_recent_avg", "funding_history_all_real"}
        if not required.issubset(df.columns) or len(df) < 2:
            return Signal.HOLD

        last = df.iloc[-1]
        if not bool(last["funding_history_all_real"]):
            # Sin funding 100% real en toda la ventana de referencia (p.ej.
            # tramo pre-2024 aproximado con mediana constante): no se opera.
            return Signal.HOLD
        if pd.isna(last["funding_pctl_upper"]) or pd.isna(last["funding_pctl_lower"]):
            return Signal.HOLD

        avg_funding = last["funding_recent_avg"]
        upper_threshold = last["funding_pctl_upper"]
        lower_threshold = last["funding_pctl_lower"]
        momentum = df["close"].iloc[-1] - df["close"].iloc[-2]

        if avg_funding > upper_threshold and momentum < 0:
            return Signal.SHORT
        if avg_funding < lower_threshold and momentum > 0:
            return Signal.LONG
        return Signal.HOLD
