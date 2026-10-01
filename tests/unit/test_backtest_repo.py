"""Tests de `app/persistence/repositories/backtest_repo.py`.

Cubre el bug real encontrado en produccion: `insert_verdict` tenia un
INSERT con 30 columnas listadas pero solo 29 `?` en VALUES (escritos a
mano, desincronizados al agregar la simulacion de cartera Monte Carlo) --
sqlite3.OperationalError: "29 values for 30 columns". La correccion genera
los `?` a partir de `len(columns)`, asi que un desajuste ya no es posible
por construccion; estos tests hacen el round-trip completo (insertar un
`BacktestVerdict` con TODOS los campos de cartera rellenos, leerlo de
vuelta, comparar) para detectar cualquier regresion futura de este tipo,
en cualquiera de las tablas de este repositorio."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence.models import BacktestVerdict
from app.persistence.repositories import backtest_repo


def _full_verdict(strategy: str = "ema_cross_9_21") -> BacktestVerdict:
    """Un BacktestVerdict con TODOS los campos rellenos (incluidos todos
    los de la simulacion de cartera) -- ningun `None` que pudiera esconder
    un desajuste de columnas si justo cae en una posicion con valor NULL
    valido por coincidencia."""
    return BacktestVerdict(
        strategy=strategy, is_experimental=False, combos_tested=12,
        total_trades_all_segments=345, pf_oos_aggregate=1.25, pct_symbols_pf_gt1=60.0,
        pct_folds_positive=55.0, pf_stressed=1.05, pf_real_funding_only=1.30,
        pf_full_period_approx=1.25, max_drawdown_oos_pct=22.5, concentration_pct=18.0,
        pf_control_group=1.10, evidence_insufficient=False, discarded=False,
        discard_reasons_json=None,
        portfolio_simulation_runs=200,
        portfolio_final_capital_median=123.45, portfolio_final_capital_p10=90.0,
        portfolio_final_capital_p90=150.0,
        portfolio_max_drawdown_median=30.0, portfolio_max_drawdown_p10=10.0,
        portfolio_max_drawdown_p90=60.0,
        portfolio_mtm_max_drawdown_median=35.0, portfolio_mtm_max_drawdown_p10=12.0,
        portfolio_mtm_max_drawdown_p90=70.0,
        portfolio_concentration_pct_median=25.0, portfolio_trades_included_median=300.0,
        portfolio_trades_skipped_no_margin_median=5.0,
        run_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_insert_verdict_round_trip_with_all_portfolio_fields_filled(db):
    verdict = _full_verdict()
    await backtest_repo.insert_verdict(db, verdict)

    rows = await backtest_repo.get_verdicts(db, strategy="ema_cross_9_21")
    assert len(rows) == 1
    got = rows[0]

    assert got.strategy == verdict.strategy
    assert got.combos_tested == verdict.combos_tested
    assert got.total_trades_all_segments == verdict.total_trades_all_segments
    assert got.pf_oos_aggregate == pytest.approx(verdict.pf_oos_aggregate)
    assert got.max_drawdown_oos_pct == pytest.approx(verdict.max_drawdown_oos_pct)
    assert got.portfolio_simulation_runs == verdict.portfolio_simulation_runs
    assert got.portfolio_final_capital_median == pytest.approx(
        verdict.portfolio_final_capital_median
    )
    assert got.portfolio_final_capital_p10 == pytest.approx(verdict.portfolio_final_capital_p10)
    assert got.portfolio_final_capital_p90 == pytest.approx(verdict.portfolio_final_capital_p90)
    assert got.portfolio_max_drawdown_median == pytest.approx(verdict.portfolio_max_drawdown_median)
    assert got.portfolio_max_drawdown_p10 == pytest.approx(verdict.portfolio_max_drawdown_p10)
    assert got.portfolio_max_drawdown_p90 == pytest.approx(verdict.portfolio_max_drawdown_p90)
    assert got.portfolio_mtm_max_drawdown_median == pytest.approx(
        verdict.portfolio_mtm_max_drawdown_median
    )
    assert got.portfolio_mtm_max_drawdown_p10 == pytest.approx(
        verdict.portfolio_mtm_max_drawdown_p10
    )
    assert got.portfolio_mtm_max_drawdown_p90 == pytest.approx(
        verdict.portfolio_mtm_max_drawdown_p90
    )
    assert got.portfolio_concentration_pct_median == pytest.approx(
        verdict.portfolio_concentration_pct_median
    )
    assert got.portfolio_trades_included_median == pytest.approx(
        verdict.portfolio_trades_included_median
    )
    assert got.portfolio_trades_skipped_no_margin_median == pytest.approx(
        verdict.portfolio_trades_skipped_no_margin_median
    )
    assert got.run_at == verdict.run_at


@pytest.mark.asyncio
async def test_insert_verdict_round_trip_with_none_portfolio_fields(db):
    """Tambien debe funcionar cuando los campos de cartera quedan en
    `None` (p.ej. una estrategia sin trades, sin simulacion posible)."""
    verdict = BacktestVerdict(
        strategy="donchian_breakout_20", is_experimental=False, combos_tested=0,
        total_trades_all_segments=0, evidence_insufficient=True, discarded=False,
        run_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await backtest_repo.insert_verdict(db, verdict)

    rows = await backtest_repo.get_verdicts(db, strategy="donchian_breakout_20")
    assert len(rows) == 1
    assert rows[0].portfolio_final_capital_median is None
    assert rows[0].evidence_insufficient is True


@pytest.mark.asyncio
async def test_get_verdicts_without_strategy_filter_returns_all(db):
    await backtest_repo.insert_verdict(db, _full_verdict("ema_cross_9_21"))
    await backtest_repo.insert_verdict(db, _full_verdict("donchian_breakout_20"))

    rows = await backtest_repo.get_verdicts(db)
    assert {r.strategy for r in rows} == {"ema_cross_9_21", "donchian_breakout_20"}
