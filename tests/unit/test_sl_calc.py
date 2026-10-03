"""Tests de `app/trading/sl_calc.py` -- extraido de
`app/backtesting/engine.py` (Fase 3, subfase 3.3) para compartirlo con el
generador de senales en vivo."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.strategies.base import Signal
from app.trading.sl_calc import fallback_sl_tp_prices, margin_loss_pct


def make_settings(**overrides) -> Settings:
    defaults = dict(BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10)
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_fallback_sl_tp_prices_long_moves_sl_down_and_tp_up():
    settings = make_settings()
    sl, tp = fallback_sl_tp_prices(Signal.LONG, 100.0, settings)
    assert sl == pytest.approx(95.0)
    assert tp == pytest.approx(110.0)


def test_fallback_sl_tp_prices_short_moves_sl_up_and_tp_down():
    settings = make_settings()
    sl, tp = fallback_sl_tp_prices(Signal.SHORT, 100.0, settings)
    assert sl == pytest.approx(105.0)
    assert tp == pytest.approx(90.0)


def test_margin_loss_pct_scales_with_leverage():
    # 5% de movimiento de precio a 10x -> 50% del margen.
    assert margin_loss_pct(entry_price=100.0, sl_price=95.0, leverage=10) == 50.0
    # Mismo movimiento de precio a 1x -> 5% del margen.
    assert margin_loss_pct(entry_price=100.0, sl_price=95.0, leverage=1) == 5.0


def test_margin_loss_pct_is_symmetric_for_long_and_short_sl():
    # La distancia es absoluta -- no importa si el SL queda por encima o
    # por debajo del precio de entrada.
    long_pct = margin_loss_pct(entry_price=100.0, sl_price=95.0, leverage=10)
    short_pct = margin_loss_pct(entry_price=100.0, sl_price=105.0, leverage=10)
    assert long_pct == short_pct == 50.0


def test_margin_loss_pct_zero_distance_is_zero():
    assert margin_loss_pct(entry_price=100.0, sl_price=100.0, leverage=10) == 0.0


@pytest.mark.parametrize("entry", [83738.6, 0.1234567, 12345.678, 3.0, 99999.99])
def test_fallback_sl_at_cap_is_exactly_50_percent_for_any_price(entry):
    """El SL de respaldo (5% a 10x) cae EXACTAMENTE en el tope de 50%. Sin
    redondeo, la coma flotante daba 50.00000000000001 en ~77% de los precios y
    la comparacion `>` descartaba la senal al azar."""
    sl_long = entry * (1 - 0.05)
    sl_short = entry * (1 + 0.05)
    assert margin_loss_pct(entry, sl_long, 10) == 50.0
    assert margin_loss_pct(entry, sl_short, 10) == 50.0
