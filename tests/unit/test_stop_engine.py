"""Tests de `app/trading/stop_engine.py` (subfase 3.1 de
`docs/FASE3_PLAN.md`).

Las funciones de modo VELA (`order_adverse_thresholds`, `check_adverse_bar`,
`check_favorable_tp_bar`) son una extraccion pura de
`app/backtesting/engine.py` -- su comportamiento ya esta cubierto por los
tests de integracion de `test_backtest_engine.py` (que siguen pasando sin
tocarlos); aqui se agregan tests unitarios directos de la funcion pura,
mas rapidos y precisos, y los tests nuevos del modo TICK con los mismos
casos limite."""

from __future__ import annotations

from app.trading.stop_engine import (
    check_adverse_bar,
    check_adverse_tick,
    check_favorable_tp_bar,
    check_favorable_tp_tick,
    order_adverse_thresholds,
)

# --- order_adverse_thresholds -------------------------------------------


def test_order_adverse_thresholds_long_puts_higher_price_first():
    """LONG: el umbral mas CERCANO al precio de entrada es el mas ALTO
    (el primero que se tocaria si el precio cae de forma monotona)."""
    order = order_adverse_thresholds(is_long=True, sl_threshold=90.0, liq_threshold=80.0)
    assert order == [("SL", 90.0), ("LIQUIDATION", 80.0)]


def test_order_adverse_thresholds_long_liquidation_closer():
    order = order_adverse_thresholds(is_long=True, sl_threshold=70.0, liq_threshold=85.0)
    assert order == [("LIQUIDATION", 85.0), ("SL", 70.0)]


def test_order_adverse_thresholds_short_puts_lower_price_first():
    """SHORT: el umbral mas cercano es el mas BAJO (el primero que se
    tocaria si el precio sube de forma monotona)."""
    order = order_adverse_thresholds(is_long=False, sl_threshold=110.0, liq_threshold=120.0)
    assert order == [("SL", 110.0), ("LIQUIDATION", 120.0)]


# --- check_adverse_bar (modo vela) --------------------------------------


def test_check_adverse_bar_gap_at_open_long():
    """Si la vela ya abre por debajo del SL (gap), se ejecuta al OPEN, no
    al precio nominal del SL."""
    result = check_adverse_bar(
        is_long=True, bar_open=85.0, last_low=80.0, last_high=86.0,
        mark_low=80.0, mark_high=86.0, sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("SL", 85.0)


def test_check_adverse_bar_hit_within_bar_long():
    """Sin gap: el SL se toca dentro del rango de la vela, se ejecuta al
    precio nominal del SL."""
    result = check_adverse_bar(
        is_long=True, bar_open=100.0, last_low=88.0, last_high=101.0,
        mark_low=88.0, mark_high=101.0, sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("SL", 90.0)


def test_check_adverse_bar_sl_wins_when_closer_long():
    """SL y liquidacion ambos tocados en la misma vela -- gana el mas
    CERCANO al precio de entrada (SL en este caso)."""
    result = check_adverse_bar(
        is_long=True, bar_open=100.0, last_low=60.0, last_high=101.0,
        mark_low=60.0, mark_high=101.0, sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("SL", 90.0)


def test_check_adverse_bar_liquidation_wins_when_closer_long():
    """Si la liquidacion esta mas CERCA que el SL (caso raro pero posible
    con margenes muy ajustados), gana la liquidacion."""
    result = check_adverse_bar(
        is_long=True, bar_open=100.0, last_low=60.0, last_high=101.0,
        mark_low=60.0, mark_high=101.0, sl_threshold=70.0, liq_threshold=90.0,
    )
    assert result == ("LIQUIDATION", 90.0)


def test_check_adverse_bar_none_when_nothing_hit():
    result = check_adverse_bar(
        is_long=True, bar_open=100.0, last_low=95.0, last_high=101.0,
        mark_low=95.0, mark_high=101.0, sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result is None


def test_check_adverse_bar_short_mirrors_long():
    result = check_adverse_bar(
        is_long=False, bar_open=100.0, last_low=99.0, last_high=112.0,
        mark_low=99.0, mark_high=112.0, sl_threshold=110.0, liq_threshold=130.0,
    )
    assert result == ("SL", 110.0)


# --- check_favorable_tp_bar (modo vela) ---------------------------------


def test_check_favorable_tp_bar_gap_at_open():
    result = check_favorable_tp_bar(
        is_long=True, bar_open=115.0, last_high=116.0, last_low=114.0, tp_threshold=110.0,
    )
    assert result == 115.0


def test_check_favorable_tp_bar_hit_within_bar():
    result = check_favorable_tp_bar(
        is_long=True, bar_open=100.0, last_high=111.0, last_low=99.0, tp_threshold=110.0,
    )
    assert result == 110.0


def test_check_favorable_tp_bar_none_when_not_reached():
    result = check_favorable_tp_bar(
        is_long=True, bar_open=100.0, last_high=105.0, last_low=99.0, tp_threshold=110.0,
    )
    assert result is None


# --- check_adverse_tick (modo tick, nuevo) ------------------------------


def test_check_adverse_tick_sl_hit_returns_real_tick_price():
    """A diferencia del modo vela (que devuelve el umbral nominal cuando
    no hay gap), el modo tick SIEMPRE devuelve el precio real observado --
    no hay "dentro del rango", solo el precio de este tick."""
    result = check_adverse_tick(
        is_long=True, last_price=88.5, mark_price=95.0,
        sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("SL", 88.5)


def test_check_adverse_tick_liquidation_uses_mark_price_not_last_price():
    """La liquidacion se revisa contra `mark_price`, el SL contra
    `last_price` -- igual que el modo vela (MARK_PRICE para liquidacion,
    LAST_PRICE para SL)."""
    result = check_adverse_tick(
        is_long=True, last_price=95.0, mark_price=65.0,
        sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("LIQUIDATION", 65.0)


def test_check_adverse_tick_sl_wins_when_closer_even_if_liq_also_crossed():
    """Caso limite pedido explicitamente: SL y liquidacion cruzados en el
    MISMO tick -- gana el umbral mas cercano al precio de entrada (SL),
    con el mismo orden que `order_adverse_thresholds` ya usa en modo vela."""
    result = check_adverse_tick(
        is_long=True, last_price=85.0, mark_price=65.0,
        sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result == ("SL", 85.0)


def test_check_adverse_tick_liquidation_wins_when_closer():
    result = check_adverse_tick(
        is_long=True, last_price=85.0, mark_price=65.0,
        sl_threshold=70.0, liq_threshold=90.0,
    )
    assert result == ("LIQUIDATION", 65.0)


def test_check_adverse_tick_none_when_nothing_crossed():
    result = check_adverse_tick(
        is_long=True, last_price=95.0, mark_price=96.0,
        sl_threshold=90.0, liq_threshold=70.0,
    )
    assert result is None


def test_check_adverse_tick_short_mirrors_long():
    result = check_adverse_tick(
        is_long=False, last_price=111.0, mark_price=105.0,
        sl_threshold=110.0, liq_threshold=130.0,
    )
    assert result == ("SL", 111.0)


# --- check_favorable_tp_tick (modo tick, nuevo) -------------------------


def test_check_favorable_tp_tick_hit_returns_real_price_long():
    """El TP en modo tick tambien devuelve el precio real -- puede
    superar el umbral nominal del TP (el bot nunca "pierde" esa mejora de
    precio, a diferencia de asumir siempre el umbral nominal)."""
    result = check_favorable_tp_tick(is_long=True, price=112.3, tp_threshold=110.0)
    assert result == 112.3


def test_check_favorable_tp_tick_none_when_not_reached():
    result = check_favorable_tp_tick(is_long=True, price=108.0, tp_threshold=110.0)
    assert result is None


def test_check_favorable_tp_tick_short_mirrors_long():
    result = check_favorable_tp_tick(is_long=False, price=87.0, tp_threshold=90.0)
    assert result == 87.0


def test_check_favorable_tp_tick_exact_threshold_hits():
    result = check_favorable_tp_tick(is_long=True, price=110.0, tp_threshold=110.0)
    assert result == 110.0
