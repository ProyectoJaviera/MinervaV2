from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.funding_contrarian import FundingContrarianStrategy


def _df(funding: list[float], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes, "funding_rate": funding})


def test_hold_without_funding_column():
    strat = FundingContrarianStrategy()
    df = pd.DataFrame({"close": [100.0] * 10})
    assert strat.evaluate(df) == Signal.HOLD


def test_hold_with_insufficient_data():
    strat = FundingContrarianStrategy(lookback=8)
    assert strat.evaluate(_df([0.001] * 3, [100.0] * 3)) == Signal.HOLD


def test_short_on_sustained_high_positive_funding_with_negative_momentum():
    strat = FundingContrarianStrategy(lookback=8, threshold=0.0003)
    funding = [0.0005] * 10
    closes = [100.0] * 9 + [99.0]  # ultimo momentum negativo
    assert strat.evaluate(_df(funding, closes)) == Signal.SHORT


def test_long_on_sustained_negative_funding_with_positive_momentum():
    strat = FundingContrarianStrategy(lookback=8, threshold=0.0003)
    funding = [-0.0005] * 10
    closes = [100.0] * 9 + [101.0]  # ultimo momentum positivo
    assert strat.evaluate(_df(funding, closes)) == Signal.LONG


def test_hold_when_funding_within_threshold():
    strat = FundingContrarianStrategy(lookback=8, threshold=0.0003)
    funding = [0.0001] * 10  # por debajo del umbral
    closes = [100.0] * 9 + [99.0]
    assert strat.evaluate(_df(funding, closes)) == Signal.HOLD


def test_hold_when_funding_has_nan():
    strat = FundingContrarianStrategy(lookback=8, threshold=0.0003)
    funding = [0.0005] * 8 + [float("nan")]
    closes = [100.0] * 8 + [99.0]
    assert strat.evaluate(_df(funding, closes)) == Signal.HOLD
