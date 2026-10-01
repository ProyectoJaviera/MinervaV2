from __future__ import annotations

import pandas as pd
import pytest

from app.strategies.base import Signal
from app.strategies.trend_atr_stop import TrendATRStopStrategy

# Series verificadas numericamente (ver sesion de desarrollo): tendencia
# sostenida 50 barras, dip profundo que invierte el signo del diferencial
# EMA9-21, y un salto fuerte en las dos ultimas barras que cruza de vuelta
# manteniendose del lado correcto del filtro EMA50.
_UP_BASE = [100.0 + i for i in range(50)] + [140, 130, 120, 110, 100, 95, 90, 150, 220]
_DOWN_BASE = [300.0 - (p - 100.0) for p in _UP_BASE]


def _df_from_prices(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "open": prices, "close": prices,
        "high": [p * 1.002 for p in prices], "low": [p * 0.998 for p in prices],
    })


def test_requires_fast_lt_slow_lt_trend():
    with pytest.raises(ValueError):
        TrendATRStopStrategy(fast=21, slow=9, trend=50)


def test_hold_with_insufficient_data():
    strat = TrendATRStopStrategy()
    df = _df_from_prices([100.0] * 10)
    assert strat.evaluate(df) == Signal.HOLD


def test_detects_long_cross_above_trend_filter():
    strat = TrendATRStopStrategy()
    assert strat.evaluate(_df_from_prices(_UP_BASE)) == Signal.LONG


def test_detects_short_cross_below_trend_filter():
    strat = TrendATRStopStrategy()
    assert strat.evaluate(_df_from_prices(_DOWN_BASE)) == Signal.SHORT


def test_hold_on_plain_uptrend_without_new_cross():
    strat = TrendATRStopStrategy()
    prices = [100.0 + i for i in range(60)]  # tendencia sostenida, sin cruce nuevo
    assert strat.evaluate(_df_from_prices(prices)) == Signal.HOLD


def test_stop_price_is_below_entry_for_long_and_above_for_short():
    strat = TrendATRStopStrategy()
    df = _df_from_prices(_UP_BASE)
    entry = df["close"].iloc[-1]
    stop = strat.stop_price(df, Signal.LONG, entry)
    assert stop is not None
    assert stop < entry

    df_short = _df_from_prices(_DOWN_BASE)
    entry_short = df_short["close"].iloc[-1]
    stop_short = strat.stop_price(df_short, Signal.SHORT, entry_short)
    assert stop_short is not None
    assert stop_short > entry_short


def test_trailing_distance_is_positive():
    strat = TrendATRStopStrategy()
    distance = strat.trailing_distance(_df_from_prices(_UP_BASE))
    assert distance is not None
    assert distance > 0
