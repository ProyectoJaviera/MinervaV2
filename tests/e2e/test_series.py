"""Las series sinteticas de las pruebas E2E se validan con las estrategias REALES: si un cambio
futuro de los indicadores las dejara de hacer disparar (o hiciera disparar de mas), falla ESTA
prueba con un mensaje claro y no los escenarios de punta a punta con un sintoma confuso."""

from __future__ import annotations

import pytest

from app.persistence.models import Side
from app.strategies.base import Signal
from tests.e2e import series

DIRECTIONS = [(Side.LONG, Signal.LONG), (Side.SHORT, Signal.SHORT)]


@pytest.mark.parametrize(("direction", "wanted"), DIRECTIONS)
def test_the_trend_cross_series_fires_the_ema_cross_and_the_atr_trend_but_not_donchian(
    direction, wanted,
):
    closes = list(series.find_trend_cross_closes(direction))

    assert series.signals_on_last_bar(closes) == {
        "ema_cross_9_21": wanted, "trend_atr_stop_9_21_50": wanted,
        "donchian_breakout_20": Signal.HOLD,
    }
    assert len(closes) == series.BARS_4H
    assert closes[-1] == pytest.approx(100.0)  # termina en el precio base pedido


def test_the_trend_cross_series_scales_to_another_base_price():
    closes = list(series.find_trend_cross_closes(Side.LONG, 2500.0))

    assert closes[-1] == pytest.approx(2500.0)
    assert series.signals_on_last_bar(closes)["ema_cross_9_21"] == Signal.LONG


@pytest.mark.parametrize("price", [0.12, 100.0, 64000.0])
def test_the_neutral_series_fires_nothing_at_any_price(price):
    closes = series.neutral_4h_closes(price)

    assert set(series.signals_on_last_bar(closes).values()) == {Signal.HOLD}
    assert closes[-1] == price


def test_a_plain_alternating_wiggle_is_not_neutral_which_is_why_the_tail_is_constant():
    # Documenta el hallazgo que motivo el tramo final constante: segun la paridad del largo, un
    # vaiven alterno hasta la ultima vela cruza las EMA y dispara sin que nadie lo pida.
    fired = {
        series.signals_on_last_bar(series.flat_closes(n, 100.0))["ema_cross_9_21"]
        for n in (series.BARS_4H, series.BARS_4H + 1)
    }

    assert fired != {Signal.HOLD}


def test_bars_end_exactly_at_the_requested_close_time():
    closes = series.neutral_4h_closes(100.0)
    end_ms = 1_800_000_000_000

    bars = series.bars_from_closes(closes, symbol="BTCUSDT", interval="4h", end_ms=end_ms)

    assert bars[-1].open_time + series.STEP_MS["4h"] == end_ms
    assert all(b.high >= max(b.open, b.close) and b.low <= min(b.open, b.close) for b in bars)
    assert [b.open_time for b in bars] == sorted({b.open_time for b in bars})
