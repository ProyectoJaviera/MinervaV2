from __future__ import annotations

import pandas as pd
import pytest

from app.strategies.base import Signal
from app.strategies.donchian_breakout import DonchianBreakoutStrategy

_N = 22  # periodo (20) + margen


def _df(last_close: float, last_high: float, last_low: float) -> pd.DataFrame:
    closes = [100.0] * _N + [last_close]
    highs = [100.5] * _N + [last_high]
    lows = [99.5] * _N + [last_low]
    return pd.DataFrame({"close": closes, "open": closes, "high": highs, "low": lows})


def test_evaluate_propagates_missing_columns_error():
    strat = DonchianBreakoutStrategy(period=20)
    df = pd.DataFrame({"close": [1.0] * 25})  # falta high/low
    with pytest.raises(ValueError):
        strat.evaluate(df)


def test_hold_with_insufficient_data():
    strat = DonchianBreakoutStrategy(period=20)
    df = pd.DataFrame({
        "close": [100.0] * 5, "open": [100.0] * 5,
        "high": [100.5] * 5, "low": [99.5] * 5,
    })
    assert strat.evaluate(df) == Signal.HOLD


def test_long_on_breakout_above_prior_range():
    strat = DonchianBreakoutStrategy(period=20)
    df = _df(last_close=120.0, last_high=120.5, last_low=119.5)
    assert strat.evaluate(df) == Signal.LONG


def test_short_on_breakout_below_prior_range():
    strat = DonchianBreakoutStrategy(period=20)
    df = _df(last_close=80.0, last_high=80.5, last_low=79.5)
    assert strat.evaluate(df) == Signal.SHORT


def test_hold_without_breakout():
    strat = DonchianBreakoutStrategy(period=20)
    df = _df(last_close=100.0, last_high=100.5, last_low=99.5)
    assert strat.evaluate(df) == Signal.HOLD


def test_stop_price_is_channel_midpoint():
    strat = DonchianBreakoutStrategy(period=20)
    df = _df(last_close=120.0, last_high=120.5, last_low=119.5)
    stop = strat.stop_price(df, Signal.LONG, entry_price=120.0)
    assert stop is not None
    assert stop < 120.0  # el punto medio del canal queda por debajo de la entrada
