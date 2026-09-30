from __future__ import annotations

import pandas as pd
import pytest

from app.indicators.engine import atr, ema, rsi


def test_ema_span_must_be_positive():
    with pytest.raises(ValueError):
        ema(pd.Series([1.0, 2.0]), span=0)


def test_ema_constant_series_equals_constant():
    series = pd.Series([5.0] * 10)
    result = ema(series, span=3)
    assert result.iloc[-1] == pytest.approx(5.0)


def test_ema_reacts_to_trend():
    series = pd.Series([float(i) for i in range(1, 21)])  # tendencia ascendente
    fast = ema(series, span=3)
    slow = ema(series, span=10)
    # en una tendencia ascendente sostenida la EMA rapida queda por encima de la lenta
    assert fast.iloc[-1] > slow.iloc[-1]


def test_rsi_period_must_be_positive():
    with pytest.raises(ValueError):
        rsi(pd.Series([1.0, 2.0]), period=0)


def test_rsi_all_gains_is_100():
    series = pd.Series([float(i) for i in range(1, 20)])  # solo sube
    result = rsi(series, period=5)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    series = pd.Series([float(i) for i in range(20, 1, -1)])  # solo baja
    result = rsi(series, period=5)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_price_is_50():
    series = pd.Series([10.0] * 20)
    result = rsi(series, period=5)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_atr_requires_ohlc_columns():
    df = pd.DataFrame({"close": [1.0, 2.0]})
    with pytest.raises(ValueError):
        atr(df, period=3)


def test_atr_non_negative():
    df = pd.DataFrame(
        {
            "high": [10.0, 11.0, 9.0, 12.0, 13.0, 14.0, 12.0],
            "low": [9.0, 9.5, 8.0, 10.0, 11.0, 12.0, 10.0],
            "close": [9.5, 10.5, 8.5, 11.0, 12.0, 13.0, 11.0],
        }
    )
    result = atr(df, period=3)
    assert (result.dropna() >= 0).all()
