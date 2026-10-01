from __future__ import annotations

import pandas as pd

from app.strategies.base import Signal
from app.strategies.mean_reversion_rsi_bb import MeanReversionRSIBBStrategy

# Verificado numericamente: caida sostenida + crash final que perfora la
# banda de Bollinger inferior con RSI ya en 0 (sobrevendido).
_DOWN = [100.0] * 5 + [100 - i * 2 for i in range(1, 20)] + [40.0]
_UP = [300 - p for p in _DOWN]  # espejo: sube sostenido + salto que perfora la banda superior


def _df(prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "close": prices, "open": prices,
        "high": [p + 0.1 for p in prices], "low": [p - 0.1 for p in prices],
    })


def test_hold_with_insufficient_data():
    strat = MeanReversionRSIBBStrategy()
    assert strat.evaluate(_df([100.0] * 5)) == Signal.HOLD


def test_long_on_oversold_touching_lower_band():
    strat = MeanReversionRSIBBStrategy()
    assert strat.evaluate(_df(_DOWN)) == Signal.LONG


def test_short_on_overbought_touching_upper_band():
    strat = MeanReversionRSIBBStrategy()
    assert strat.evaluate(_df(_UP)) == Signal.SHORT


def test_hold_on_flat_price():
    strat = MeanReversionRSIBBStrategy()
    assert strat.evaluate(_df([100.0] * 25)) == Signal.HOLD


def test_take_profit_targets_middle_band():
    strat = MeanReversionRSIBBStrategy()
    df = _df(_DOWN)
    tp = strat.take_profit_price(df, Signal.LONG, entry_price=df["close"].iloc[-1])
    assert tp is not None
    assert tp > df["close"].iloc[-1]  # el objetivo (banda media) esta por encima de la entrada


def test_stop_price_below_entry_for_long():
    strat = MeanReversionRSIBBStrategy()
    df = _df(_DOWN)
    entry = df["close"].iloc[-1]
    sl = strat.stop_price(df, Signal.LONG, entry)
    assert sl is not None
    assert sl < entry
