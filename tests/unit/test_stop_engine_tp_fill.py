"""Relleno del TP en modo tick: nominal salvo hueco evidente (subfase 3.4)."""

from __future__ import annotations

import pytest

from app.trading.stop_engine import tp_tick_fill_price

TOL = 0.002  # 0.2%


def test_long_tp_crossed_within_tolerance_fills_at_nominal():
    assert tp_tick_fill_price(True, 110.1, 110.0, TOL) == pytest.approx(110.0)


def test_long_tp_evident_gap_fills_at_observed():
    assert tp_tick_fill_price(True, 112.0, 110.0, TOL) == pytest.approx(112.0)


def test_long_tp_exactly_at_tolerance_boundary_is_nominal():
    boundary = 110.0 * (1 + TOL)
    assert tp_tick_fill_price(True, boundary, 110.0, TOL) == pytest.approx(110.0)


def test_short_tp_crossed_within_tolerance_fills_at_nominal():
    assert tp_tick_fill_price(False, 89.9, 90.0, TOL) == pytest.approx(90.0)


def test_short_tp_evident_gap_fills_at_observed():
    assert tp_tick_fill_price(False, 88.0, 90.0, TOL) == pytest.approx(88.0)


def test_zero_tolerance_fills_at_observed_on_any_crossing():
    assert tp_tick_fill_price(True, 110.01, 110.0, 0.0) == pytest.approx(110.01)
