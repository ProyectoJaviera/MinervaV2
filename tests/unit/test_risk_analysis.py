"""Tests de `app/backtesting/risk_analysis.py` -- bootstrap de riesgo de
ruina sobre operaciones del backtest (tarea 5)."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from app.backtesting.engine import RawTrade
from app.backtesting.risk_analysis import (
    format_markdown_table,
    pct_returns_from_trades,
    run_bootstrap_scenario,
    run_full_grid,
)
from app.persistence.models import Side

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _trade(pnl_net: float, margin_usdt: float = 10.0) -> RawTrade:
    return RawTrade(
        strategy="x", symbol="BTCUSDT", timeframe="4h", side=Side.LONG,
        entry_time=T0, exit_time=T0, entry_price=100.0, exit_price=100.0, qty=1.0,
        margin_usdt=margin_usdt, leverage=10, fee_entry_usdt=0.0, fee_exit_usdt=0.0,
        slippage_cost_usdt=0.0, funding_paid_usdt=0.0, funding_is_approximated=False,
        pnl_gross_usdt=pnl_net, pnl_net_usdt=pnl_net, close_reason="TP",
        sl_margin_loss_pct=None,
    )


def test_pct_returns_from_trades_divides_by_own_margin():
    trades = [_trade(5.0, margin_usdt=10.0), _trade(-3.0, margin_usdt=10.0)]
    assert pct_returns_from_trades(trades) == [0.5, -0.3]


def test_pct_returns_skips_zero_margin_trades():
    trades = [_trade(5.0, margin_usdt=10.0), _trade(0.0, margin_usdt=0.0)]
    assert pct_returns_from_trades(trades) == [0.5]


def test_all_winning_returns_never_ruin_or_drawdown():
    pct_returns = [0.1, 0.2, 0.3]  # solo ganancias
    rng = random.Random(0)
    scenario = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.5, rng=rng,
        trials=200, trial_length=50,
    )
    assert scenario.prob_ruin_pct == 0.0
    assert scenario.prob_drawdown_gt_30_pct == 0.0


def test_all_losing_returns_always_ruin():
    pct_returns = [-0.9, -0.8, -0.95]  # siempre pierde casi todo el margen
    rng = random.Random(0)
    scenario = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.95, rng=rng,
        trials=200, trial_length=50,
    )
    assert scenario.prob_ruin_pct == 100.0


def test_sl_cap_truncates_losses_beyond_the_cap():
    """Una sola operacion que perderia el 90% del margen, con un tope de
    SL de 30%: la perdida real aplicada no puede superar el 30% del
    margen -- un solo trial, una sola operacion, resultado exacto."""
    pct_returns = [-0.9]
    rng = random.Random(0)
    scenario = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.30, rng=rng,
        trials=1, trial_length=1,
    )
    # perdida aplicada = 30% de 10 = 3.0 -> capital final = 97.0, no ruina
    assert scenario.prob_ruin_pct == 0.0


def test_tighter_sl_cap_never_increases_ruin_probability():
    """Un tope de SL mas ajustado (30%) nunca deberia dar MAS ruina que
    uno mas laxo (50%) sobre la MISMA distribucion de retornos -- limita
    las perdidas, nunca las agranda."""
    rng_seed = 7
    pct_returns = [-0.6, -0.4, -0.2, 0.1, 0.3, 0.5, -0.8]

    scenario_30 = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=2, sl_cap_pct=0.30,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    scenario_50 = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=2, sl_cap_pct=0.50,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    assert scenario_30.prob_ruin_pct <= scenario_50.prob_ruin_pct


def test_more_simultaneous_positions_does_not_decrease_ruin_probability():
    """Mas posiciones simultaneas (mas exposicion por ronda) no deberia
    REDUCIR el riesgo de ruina frente a menos posiciones, sobre la misma
    distribucion de retornos con sesgo perdedor."""
    rng_seed = 11
    pct_returns = [-0.5, -0.3, -0.1, 0.2, -0.4]  # sesgo perdedor

    scenario_1 = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.50,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    scenario_3 = run_bootstrap_scenario(
        pct_returns, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=3, sl_cap_pct=0.50,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    assert scenario_3.prob_ruin_pct >= scenario_1.prob_ruin_pct


def test_empty_returns_raises():
    with pytest.raises(ValueError):
        run_bootstrap_scenario(
            [], initial_capital=100.0, margin_usdt=10.0,
            max_simultaneous_positions=1, sl_cap_pct=0.5, rng=random.Random(0),
        )


def test_run_full_grid_covers_all_combinations():
    pct_returns = [0.1, -0.2, 0.3, -0.1]
    results = run_full_grid(
        pct_returns, margins=(5.0, 10.0), position_caps=(1, 2, 3),
        sl_caps=(0.30, 0.50), trials=50, trial_length=20, seed=1,
    )
    assert len(results) == 2 * 3 * 2
    combos = {(r.margin_usdt, r.max_simultaneous_positions, r.sl_cap_pct) for r in results}
    assert len(combos) == 12


def test_run_full_grid_is_deterministic_with_fixed_seed():
    pct_returns = [0.1, -0.2, 0.3, -0.1, -0.5]
    first = run_full_grid(pct_returns, trials=100, trial_length=30, seed=42)
    second = run_full_grid(pct_returns, trials=100, trial_length=30, seed=42)
    assert [r.prob_ruin_pct for r in first] == [r.prob_ruin_pct for r in second]
    assert [r.prob_drawdown_gt_30_pct for r in first] == [
        r.prob_drawdown_gt_30_pct for r in second
    ]


def test_format_markdown_table_has_header_and_one_row_per_scenario():
    pct_returns = [0.1, -0.2]
    results = run_full_grid(
        pct_returns, margins=(10.0,), position_caps=(1,), sl_caps=(0.5,),
        trials=10, trial_length=5, seed=0,
    )
    table = format_markdown_table(results)
    lines = table.splitlines()
    assert lines[0].startswith("| Margen")
    assert lines[1].startswith("|---")
    assert len(lines) == 2 + len(results)
