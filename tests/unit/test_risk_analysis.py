"""Tests de `app/backtesting/risk_analysis.py` -- bootstrap de riesgo de
ruina sobre operaciones del backtest (tarea 5; quinta revision: el tope de
SL excluye operaciones enteras en vez de truncar perdidas; sexta revision:
0 operaciones elegibles ya no lanza excepcion, ver el docstring del
modulo)."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from app.backtesting.engine import RawTrade
from app.backtesting.risk_analysis import (
    filter_trades_by_sl_cap,
    format_markdown_table,
    format_sensitivity_table,
    pct_returns_from_trades,
    rescale_returns_to_target_pf,
    run_bootstrap_scenario,
    run_full_grid,
    run_sensitivity_grid,
)
from app.persistence.models import Side

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def _trade(
    pnl_net: float, margin_usdt: float = 10.0, sl_margin_loss_pct: float | None = None
) -> RawTrade:
    return RawTrade(
        strategy="x", symbol="BTCUSDT", timeframe="4h", side=Side.LONG,
        entry_time=T0, exit_time=T0, entry_price=100.0, exit_price=100.0, qty=1.0,
        margin_usdt=margin_usdt, leverage=10, fee_entry_usdt=0.0, fee_exit_usdt=0.0,
        slippage_cost_usdt=0.0, funding_paid_usdt=0.0, funding_is_approximated=False,
        pnl_gross_usdt=pnl_net, pnl_net_usdt=pnl_net, close_reason="TP",
        sl_margin_loss_pct=sl_margin_loss_pct,
    )


def test_pct_returns_from_trades_divides_by_own_margin():
    trades = [_trade(5.0, margin_usdt=10.0), _trade(-3.0, margin_usdt=10.0)]
    assert pct_returns_from_trades(trades) == [0.5, -0.3]


def test_pct_returns_skips_zero_margin_trades():
    trades = [_trade(5.0, margin_usdt=10.0), _trade(0.0, margin_usdt=0.0)]
    assert pct_returns_from_trades(trades) == [0.5]


def test_filter_by_sl_cap_excludes_whole_trades_above_threshold():
    """El tope de SL excluye la operacion ENTERA (con su ganancia incluida
    si la tenia) cuyo riesgo planeado al abrir supera el tope -- no trunca
    nada. Caso deterministico, sin aleatoriedad."""
    trades = [
        _trade(50.0, margin_usdt=10.0, sl_margin_loss_pct=60.0),  # excluida (tope 30%)
        _trade(5.0, margin_usdt=10.0, sl_margin_loss_pct=20.0),
        _trade(-3.0, margin_usdt=10.0, sl_margin_loss_pct=20.0),
    ]
    kept, excluded = filter_trades_by_sl_cap(trades, sl_cap_pct=0.30)
    assert excluded == 1
    assert [t.pnl_net_usdt for t in kept] == [5.0, -3.0]


def test_filter_by_sl_cap_keeps_trades_with_no_recorded_sl_risk():
    """Si una operacion no tiene `sl_margin_loss_pct` (None -- p.ej. datos
    antiguos o una entrada sin SL propio), no se excluye por este filtro."""
    trades = [_trade(5.0, sl_margin_loss_pct=None)]
    kept, excluded = filter_trades_by_sl_cap(trades, sl_cap_pct=0.30)
    assert excluded == 0
    assert len(kept) == 1


def test_excluding_high_risk_winner_can_worsen_survivor_expectancy():
    """Prueba de que la propiedad "tope mas ajustado nunca empeora las
    cosas" YA NO es cierta por construccion (a diferencia del diseno
    anterior, que truncaba perdidas): si la operacion de riesgo planeado
    alto es la UNICA ganadora del pool, un tope mas ajustado la excluye y
    EMPEORA la esperanza del pool sobreviviente, no la mejora."""
    trades = [
        _trade(50.0, margin_usdt=10.0, sl_margin_loss_pct=60.0),
        _trade(-3.0, margin_usdt=10.0, sl_margin_loss_pct=20.0),
        _trade(-2.0, margin_usdt=10.0, sl_margin_loss_pct=20.0),
    ]
    kept_loose, _ = filter_trades_by_sl_cap(trades, sl_cap_pct=0.70)
    kept_tight, _ = filter_trades_by_sl_cap(trades, sl_cap_pct=0.30)
    expectancy_loose = sum(t.pnl_net_usdt for t in kept_loose) / len(kept_loose)
    expectancy_tight = sum(t.pnl_net_usdt for t in kept_tight) / len(kept_tight)
    assert expectancy_tight < expectancy_loose


def test_rescale_returns_to_target_pf_hits_target_exactly():
    pct_returns = [0.5, 0.3, -0.4, -0.6, -0.2]
    rescaled = rescale_returns_to_target_pf(pct_returns, target_pf=1.2)
    gains = sum(r for r in rescaled if r > 0)
    losses = -sum(r for r in rescaled if r < 0)
    assert gains / losses == pytest.approx(1.2)
    # las ganancias no se tocan
    assert sorted(r for r in rescaled if r > 0) == sorted(
        r for r in pct_returns if r > 0
    )


def test_rescale_raises_without_losses():
    with pytest.raises(ValueError):
        rescale_returns_to_target_pf([0.1, 0.2], target_pf=1.0)


def test_all_winning_returns_never_ruin_or_drawdown():
    trades = [_trade(1.0), _trade(2.0), _trade(3.0)]  # solo ganancias
    rng = random.Random(0)
    scenario = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.5, rng=rng,
        trials=200, trial_length=50,
    )
    assert scenario.prob_ruin_pct == 0.0
    assert scenario.prob_drawdown_gt_30_pct == 0.0


def test_all_losing_returns_always_ruin():
    trades = [_trade(-9.0), _trade(-8.0), _trade(-9.5)]  # pierde casi todo el margen
    rng = random.Random(0)
    scenario = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.95, rng=rng,
        trials=200, trial_length=50,
    )
    assert scenario.prob_ruin_pct == 100.0


def test_run_bootstrap_scenario_reports_exclusion_counts():
    trades = [
        _trade(50.0, sl_margin_loss_pct=60.0),  # excluida
        _trade(1.0, sl_margin_loss_pct=20.0),
        _trade(-1.0, sl_margin_loss_pct=20.0),
    ]
    scenario = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.30, rng=random.Random(0),
        trials=50, trial_length=10,
    )
    assert scenario.excluded_by_sl_cap == 1
    assert scenario.included_trades == 2


def test_more_simultaneous_positions_does_not_decrease_ruin_probability():
    """Con la MISMA distribucion de retornos (sesgo perdedor) y el MISMO
    tope de SL (por lo tanto la MISMA exclusion en ambas corridas), mas
    posiciones simultaneas no deberia REDUCIR el riesgo de ruina frente a
    menos posiciones."""
    rng_seed = 11
    trades = [
        _trade(-5.0, sl_margin_loss_pct=20.0), _trade(-3.0, sl_margin_loss_pct=20.0),
        _trade(-1.0, sl_margin_loss_pct=20.0), _trade(2.0, sl_margin_loss_pct=20.0),
        _trade(-4.0, sl_margin_loss_pct=20.0),
    ]

    scenario_1 = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.50,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    scenario_3 = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=3, sl_cap_pct=0.50,
        rng=random.Random(rng_seed), trials=1000, trial_length=100,
    )
    assert scenario_3.prob_ruin_pct >= scenario_1.prob_ruin_pct


def test_block_bootstrap_preserves_consecutive_order():
    """Con `block_size` igual a la longitud del pool, cada bloque remuestreado
    es SIEMPRE el pool entero en su orden original (rotado) -- en este caso
    con retornos que alternan ganar/perder en un orden fijo, el resultado
    de ruina debe coincidir exactamente con simular esa secuencia fija
    repetida, no con un remuestreo i.i.d. independiente."""
    trades = [_trade(5.0, sl_margin_loss_pct=10.0), _trade(-9.0, sl_margin_loss_pct=10.0)]
    scenario = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.50, rng=random.Random(3),
        trials=20, trial_length=10, block_size=2,
    )
    # La secuencia (ganar, perder) o (perder, ganar) repetida 5 veces da el
    # mismo capital final sin importar la fase -- nunca ruina (10% margen,
    # perdidas de 9 USDT no bajan el capital de 10 USDT en ningun punto
    # porque siempre alternan con una ganancia antes de la siguiente).
    assert scenario.prob_ruin_pct == 0.0


def test_empty_trades_returns_na_scenario_instead_of_raising():
    """`trades` vacio de entrada (nunca hubo operaciones) debe comportarse
    igual que 0 operaciones elegibles tras el filtro -- escenario "n/a",
    nunca una excepcion que tumbe el resto de la grilla."""
    scenario = run_bootstrap_scenario(
        [], initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.5, rng=random.Random(0),
    )
    assert scenario.prob_ruin_pct is None
    assert scenario.prob_drawdown_gt_30_pct is None
    assert scenario.included_trades == 0
    assert scenario.excluded_by_sl_cap == 0


def test_fixed_fallback_sl_strategy_is_na_under_a_tighter_cap_without_raising():
    """Reproduce el fallo real reportado contra la base real: una
    estrategia (p.ej. `ema_cross_9_21` o las de funding) cuyo UNICO SL es
    el de respaldo porcentual fijo cae con `sl_margin_loss_pct=50.0` en el
    100% de sus operaciones -- un tope mas ajustado (30%) las deja con 0
    operaciones elegibles. Antes esto lanzaba `ValueError` y tumbaba
    `scripts/analyze_risk.py` a mitad de la grilla; ahora debe devolver un
    escenario "n/a" sin excepcion, reportando cuantas se excluyeron."""
    trades = [_trade(4.0, sl_margin_loss_pct=50.0) for _ in range(5)] + [
        _trade(-3.0, sl_margin_loss_pct=50.0) for _ in range(5)
    ]
    scenario = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.30, rng=random.Random(0),
        trials=50, trial_length=10,
    )
    assert scenario.prob_ruin_pct is None
    assert scenario.prob_drawdown_gt_30_pct is None
    assert scenario.included_trades == 0
    assert scenario.excluded_by_sl_cap == 10

    table = format_markdown_table([scenario])
    assert "n/a" in table
    assert "0 de 10 elegibles" in table

    # El mismo pool, con un tope que SI cubre su SL fijo (50%), si opera.
    scenario_ok = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.50, rng=random.Random(0),
        trials=50, trial_length=10,
    )
    assert scenario_ok.prob_ruin_pct is not None
    assert scenario_ok.included_trades == 10
    assert scenario_ok.excluded_by_sl_cap == 0


def test_run_full_grid_does_not_raise_when_one_cell_is_empty():
    """Toda la grilla debe terminar de calcularse aunque alguna de sus
    filas quede sin operaciones elegibles -- no debe interrumpirse a mitad
    de camino (el fallo real reportado)."""
    trades = [_trade(4.0, sl_margin_loss_pct=50.0), _trade(-3.0, sl_margin_loss_pct=50.0)]
    results = run_full_grid(
        trades, margins=(10.0,), position_caps=(1,), sl_caps=(0.30, 0.50),
        trials=20, trial_length=10, seed=0,
    )
    assert len(results) == 2
    na_row, ok_row = results
    assert na_row.prob_ruin_pct is None
    assert ok_row.prob_ruin_pct is not None


def test_sin_tope_matches_50_percent_cap_when_no_trade_exceeds_it():
    """`sl_cap_pct=inf` ("sin tope") y 50% deben dar el MISMO resultado
    cuando ninguna operacion del pool supera el 50% (el caso real de este
    dataset, donde el backtest ya solo admite operaciones <= 50% de
    riesgo planeado) -- confirma que la fila "sin tope" no es un error de
    calculo, es la evidencia de que 50% ya no excluye nada."""
    trades = [_trade(4.0, sl_margin_loss_pct=50.0), _trade(-3.0, sl_margin_loss_pct=20.0)]
    scenario_50 = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=0.50, rng=random.Random(5),
        trials=50, trial_length=10,
    )
    scenario_inf = run_bootstrap_scenario(
        trades, initial_capital=100.0, margin_usdt=10.0,
        max_simultaneous_positions=1, sl_cap_pct=float("inf"), rng=random.Random(5),
        trials=50, trial_length=10,
    )
    assert scenario_50.included_trades == scenario_inf.included_trades == 2
    assert scenario_50.prob_ruin_pct == scenario_inf.prob_ruin_pct


def test_run_full_grid_covers_all_combinations():
    trades = [_trade(1.0, sl_margin_loss_pct=10.0), _trade(-2.0, sl_margin_loss_pct=10.0),
              _trade(3.0, sl_margin_loss_pct=10.0), _trade(-1.0, sl_margin_loss_pct=10.0)]
    results = run_full_grid(
        trades, margins=(5.0, 10.0), position_caps=(1, 2, 3),
        sl_caps=(0.30, 0.50), trials=50, trial_length=20, seed=1,
    )
    assert len(results) == 2 * 3 * 2
    combos = {(r.margin_usdt, r.max_simultaneous_positions, r.sl_cap_pct) for r in results}
    assert len(combos) == 12


def test_run_full_grid_is_deterministic_with_fixed_seed():
    trades = [_trade(1.0, sl_margin_loss_pct=10.0), _trade(-2.0, sl_margin_loss_pct=10.0),
              _trade(3.0, sl_margin_loss_pct=10.0), _trade(-1.0, sl_margin_loss_pct=10.0),
              _trade(-5.0, sl_margin_loss_pct=10.0)]
    first = run_full_grid(trades, trials=100, trial_length=30, seed=42)
    second = run_full_grid(trades, trials=100, trial_length=30, seed=42)
    assert [r.prob_ruin_pct for r in first] == [r.prob_ruin_pct for r in second]
    assert [r.prob_drawdown_gt_30_pct for r in first] == [
        r.prob_drawdown_gt_30_pct for r in second
    ]


def test_format_markdown_table_has_header_and_one_row_per_scenario():
    trades = [_trade(1.0, sl_margin_loss_pct=10.0), _trade(-2.0, sl_margin_loss_pct=10.0)]
    results = run_full_grid(
        trades, margins=(10.0,), position_caps=(1,), sl_caps=(0.5,),
        trials=10, trial_length=5, seed=0,
    )
    table = format_markdown_table(results)
    lines = table.splitlines()
    assert lines[0].startswith("| Margen")
    assert lines[1].startswith("|---")
    assert len(lines) == 2 + len(results)


def test_run_sensitivity_grid_covers_all_combinations():
    pct_returns = [0.1, -0.2, 0.3, -0.1, -0.4]
    results = run_sensitivity_grid(
        pct_returns, margins=(5.0, 10.0), position_caps=(1, 3),
        target_pfs=(1.0, 1.2), trials=50, trial_length=20, seed=0,
    )
    assert len(results) == 2 * 2 * 2
    combos = {(r.target_pf, r.margin_usdt, r.max_simultaneous_positions) for r in results}
    assert len(combos) == 8


def test_format_sensitivity_table_has_header_and_one_row_per_scenario():
    results = run_sensitivity_grid(
        [0.1, -0.2, 0.3, -0.1], margins=(10.0,), position_caps=(1,),
        target_pfs=(1.0,), trials=10, trial_length=5, seed=0,
    )
    table = format_sensitivity_table(results)
    lines = table.splitlines()
    assert lines[0].startswith("| PF objetivo")
    assert lines[1].startswith("|---")
    assert len(lines) == 2 + len(results)
