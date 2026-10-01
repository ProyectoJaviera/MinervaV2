"""Tests de `FundingContrarianPercentileStrategy` (v2 experimental, umbral
por percentiles -- ver docstring del modulo para el porque de esta
estrategia separada de `funding_contrarian.py`)."""

from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.funding_contrarian_percentile import FundingContrarianPercentileStrategy

HISTORY = 20  # ventana chica para que los tests sean legibles


def _df(funding: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes, "funding_rate": funding})


def test_hold_without_funding_column():
    strat = FundingContrarianPercentileStrategy(history_window=HISTORY)
    df = pd.DataFrame({"close": [100.0] * 30})
    assert strat.evaluate(df) == Signal.HOLD


def test_hold_with_insufficient_history():
    strat = FundingContrarianPercentileStrategy(history_window=HISTORY)
    funding = [0.0001] * (HISTORY - 1)
    closes = [100.0] * (HISTORY - 1)
    assert strat.evaluate(_df(funding, closes)) == Signal.HOLD


def test_short_when_funding_in_upper_percentile_with_negative_momentum():
    """Funding tipico bajo (~0.0001) con un pico reciente alto: el pico cae
    en el percentil superior de su propia historia, aunque sea muy inferior
    al 0.03% absoluto de la estrategia original -- por eso SI genera senal
    donde la original (umbral fijo) no la generaria. Solo las ultimas 2
    (10% de 20) velas tienen el pico, para que el percentil 90 quede
    claramente por debajo del promedio reciente (lookback=4, que mezcla 2
    velas base + las 2 del pico)."""
    strat = FundingContrarianPercentileStrategy(
        history_window=HISTORY, lookback=4, upper_percentile=90.0, lower_percentile=10.0
    )
    funding = [0.0001] * (HISTORY - 1) + [0.0005] * 2
    closes = [100.0] * HISTORY + [99.0]  # momentum negativo
    assert strat.evaluate(_df(funding, closes)) == Signal.SHORT


def test_long_when_funding_in_lower_percentile_with_positive_momentum():
    strat = FundingContrarianPercentileStrategy(
        history_window=HISTORY, lookback=4, upper_percentile=90.0, lower_percentile=10.0
    )
    funding = [0.0001] * (HISTORY - 1) + [-0.0003] * 2
    closes = [100.0] * HISTORY + [101.0]  # momentum positivo
    assert strat.evaluate(_df(funding, closes)) == Signal.LONG


def test_hold_when_funding_flat_no_relative_extreme():
    strat = FundingContrarianPercentileStrategy(history_window=HISTORY, lookback=4)
    funding = [0.0001] * (HISTORY + 1)  # sin variacion -> ningun percentil extremo real
    closes = [100.0] * HISTORY + [99.0]
    assert strat.evaluate(_df(funding, closes)) == Signal.HOLD


def test_hold_when_funding_has_nan_in_history_window():
    strat = FundingContrarianPercentileStrategy(history_window=HISTORY, lookback=4)
    funding = [0.0001] * HISTORY + [float("nan")]
    closes = [100.0] * HISTORY + [99.0]
    assert strat.evaluate(_df(funding, closes)) == Signal.HOLD
