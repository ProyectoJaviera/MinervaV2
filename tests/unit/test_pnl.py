"""Tests de las formulas compartidas de comisiones/PnL (app/execution/pnl.py),
usadas tanto por PaperBackend como por el motor de backtest de Fase 2."""

from __future__ import annotations

import pytest

from app.execution.pnl import compute_close_result, compute_open_fill
from app.persistence.models import Side


def test_compute_open_fill_matches_documented_example():
    # Ejemplo numerico de PROGRESS.md: margen 10, leverage 10x, entrada 100.
    fill = compute_open_fill(margin_usdt=10.0, leverage=10, entry_price=100.0, taker_fee_pct=0.0006)
    assert fill.notional_usdt == pytest.approx(100.0)
    assert fill.qty == pytest.approx(1.0)
    assert fill.fee_entry_usdt == pytest.approx(0.06)


def test_compute_close_result_long_matches_documented_example():
    fill = compute_open_fill(10.0, 10, 100.0, 0.0006)
    result = compute_close_result(
        Side.LONG, fill.qty, entry_price=100.0, exit_price=110.0,
        fee_entry_usdt=fill.fee_entry_usdt, taker_fee_pct=0.0006,
    )
    assert result.fee_exit_usdt == pytest.approx(0.066)
    assert result.pnl_gross_usdt == pytest.approx(10.0)
    assert result.pnl_net_usdt == pytest.approx(9.874)


def test_compute_close_result_short_profits_on_drop():
    fill = compute_open_fill(10.0, 10, 100.0, 0.0006)
    result = compute_close_result(
        Side.SHORT, fill.qty, entry_price=100.0, exit_price=90.0,
        fee_entry_usdt=fill.fee_entry_usdt, taker_fee_pct=0.0006,
    )
    assert result.pnl_gross_usdt == pytest.approx(10.0)
    assert result.pnl_gross_usdt > 0


def test_compute_close_result_applies_extra_costs():
    fill = compute_open_fill(10.0, 10, 100.0, 0.0006)
    without_extra = compute_close_result(
        Side.LONG, fill.qty, 100.0, 110.0, fill.fee_entry_usdt, 0.0006,
    )
    with_extra = compute_close_result(
        Side.LONG, fill.qty, 100.0, 110.0, fill.fee_entry_usdt, 0.0006,
        extra_costs_usdt=1.5,
    )
    assert with_extra.pnl_net_usdt == pytest.approx(without_extra.pnl_net_usdt - 1.5)
    assert with_extra.pnl_gross_usdt == without_extra.pnl_gross_usdt  # el bruto no cambia


def test_zero_price_move_yields_only_fee_loss():
    fill = compute_open_fill(10.0, 10, 100.0, 0.0006)
    result = compute_close_result(
        Side.LONG, fill.qty, 100.0, 100.0, fill.fee_entry_usdt, 0.0006,
    )
    assert result.pnl_gross_usdt == pytest.approx(0.0)
    assert result.pnl_net_usdt < 0  # solo pierde las comisiones
