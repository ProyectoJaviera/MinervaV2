from __future__ import annotations

import pandas as pd
import pytest

from app.indicators.engine import atr, bollinger_bands, donchian_channel, ema, rsi, sma


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


def test_sma_period_must_be_positive():
    with pytest.raises(ValueError):
        sma(pd.Series([1.0, 2.0]), period=0)


def test_sma_constant_series_equals_constant():
    series = pd.Series([7.0] * 10)
    assert sma(series, period=4).iloc[-1] == pytest.approx(7.0)


def test_sma_matches_manual_average():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(series, period=3)
    assert result.iloc[-1] == pytest.approx((3.0 + 4.0 + 5.0) / 3)
    assert result.iloc[:2].isna().all()  # no hay suficiente historia todavia


def test_bollinger_bands_flat_price_collapses_bands():
    series = pd.Series([50.0] * 25)
    upper, middle, lower = bollinger_bands(series, period=20, num_std=2.0)
    assert upper.iloc[-1] == pytest.approx(50.0)
    assert middle.iloc[-1] == pytest.approx(50.0)
    assert lower.iloc[-1] == pytest.approx(50.0)


def test_bollinger_bands_upper_above_middle_above_lower():
    series = pd.Series([50.0 + (i % 3) for i in range(30)])  # con algo de ruido
    upper, middle, lower = bollinger_bands(series, period=10, num_std=2.0)
    assert upper.iloc[-1] > middle.iloc[-1] > lower.iloc[-1]


def test_donchian_channel_requires_columns():
    with pytest.raises(ValueError):
        donchian_channel(pd.DataFrame({"close": [1.0, 2.0]}), period=3)


def test_donchian_channel_tracks_rolling_extremes():
    df = pd.DataFrame({
        "high": [10.0, 12.0, 11.0, 15.0, 9.0],
        "low": [8.0, 9.0, 7.0, 10.0, 6.0],
    })
    upper, lower = donchian_channel(df, period=3)
    # ventana de las ultimas 3 velas en el indice final: high=[11,15,9] low=[7,10,6]
    assert upper.iloc[-1] == pytest.approx(15.0)
    assert lower.iloc[-1] == pytest.approx(6.0)
