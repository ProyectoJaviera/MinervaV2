from __future__ import annotations

import pandas as pd
import pytest

from app.strategies.base import Signal
from app.strategies.ema_cross import EMACrossStrategy


def test_fast_must_be_less_than_slow():
    with pytest.raises(ValueError):
        EMACrossStrategy(fast=21, slow=9)


def test_hold_with_insufficient_data():
    strategy = EMACrossStrategy(fast=2, slow=3)
    df = pd.DataFrame({"close": [100.0]})
    assert strategy.evaluate(df) == Signal.HOLD


def test_detects_bullish_cross():
    # Serie que baja sostenidamente (fast < slow) y da un salto fuerte en la
    # ultima vela que invierte el signo del diferencial exactamente ahi
    # (verificado numericamente: diff pasa de -1.383 a +17.911).
    prices = [100, 99, 98, 97, 96, 95, 94, 93, 150]
    df = pd.DataFrame({"close": [float(p) for p in prices]})
    strategy = EMACrossStrategy(fast=2, slow=5)
    assert strategy.evaluate(df) == Signal.LONG


def test_detects_bearish_cross():
    # Simetrico al caso alcista: sube sostenidamente y cae fuerte en la
    # ultima vela (diff pasa de +1.383 a -17.911).
    prices = [100, 101, 102, 103, 104, 105, 106, 107, 50]
    df = pd.DataFrame({"close": [float(p) for p in prices]})
    strategy = EMACrossStrategy(fast=2, slow=5)
    assert strategy.evaluate(df) == Signal.SHORT


def test_no_cross_holds():
    prices = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    df = pd.DataFrame({"close": [float(p) for p in prices]})
    strategy = EMACrossStrategy(fast=2, slow=5)
    # tendencia sostenida sin cruce nuevo en la ultima vela
    assert strategy.evaluate(df) == Signal.HOLD
