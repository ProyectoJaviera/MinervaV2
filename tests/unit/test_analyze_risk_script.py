"""Smoke test de punta a punta para `scripts/analyze_risk.py` (regla de
CLAUDE.md: todo lo que toque persistencia necesita un smoke test, no solo
tests unitarios) -- datos sinteticos pequenos, nunca la base real."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from app.persistence.database import Database
from app.persistence.models import BacktestTrade, Side
from app.persistence.repositories import backtest_repo
from app.strategies.registry import EXPERIMENTAL_STRATEGIES, STRATEGIES
from scripts import analyze_risk

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _trade(
    strategy: str, i: int, pnl_net: float, sl_margin_loss_pct: float, margin: float = 10.0,
) -> BacktestTrade:
    ts = T0 + timedelta(hours=i)
    return BacktestTrade(
        strategy=strategy, symbol="BTCUSDT", timeframe="4h", segment="OOS", side=Side.LONG,
        entry_time=ts, exit_time=ts + timedelta(hours=1), entry_price=100.0, exit_price=101.0,
        qty=1.0, margin_usdt=margin, leverage=10, fee_entry_usdt=0.0, fee_exit_usdt=0.0,
        slippage_cost_usdt=0.0, funding_paid_usdt=0.0, funding_is_approximated=False,
        pnl_gross_usdt=pnl_net, pnl_net_usdt=pnl_net, close_reason="TP",
        sl_margin_loss_pct=sl_margin_loss_pct,
    )


@pytest_asyncio.fixture
async def populated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "synthetic_risk.db"
    database = Database(str(db_path))
    await database.connect()

    trades = []
    non_experimental = [s for s in STRATEGIES if s not in EXPERIMENTAL_STRATEGIES]
    for strategy in non_experimental:
        trades += [
            _trade(strategy, 0, pnl_net=4.0, sl_margin_loss_pct=20.0),
            _trade(strategy, 1, pnl_net=-3.0, sl_margin_loss_pct=20.0),
            _trade(strategy, 2, pnl_net=-2.0, sl_margin_loss_pct=20.0),
            _trade(strategy, 3, pnl_net=9.0, sl_margin_loss_pct=60.0),  # excluida con tope 30%
        ]
    one_experimental = next(iter(EXPERIMENTAL_STRATEGIES))
    trades += [
        _trade(one_experimental, 0, pnl_net=2.0, sl_margin_loss_pct=15.0),
        _trade(one_experimental, 1, pnl_net=-1.0, sl_margin_loss_pct=15.0),
    ]
    await backtest_repo.insert_trades(database, trades)
    await database.close()

    report_path = tmp_path / "FASE2_RIESGO_test.md"
    monkeypatch.setattr(analyze_risk.settings, "database_path", str(db_path))
    monkeypatch.setattr(analyze_risk, "REPORT_PATH", str(report_path))
    monkeypatch.setattr(analyze_risk, "GRID_TRIALS", 20)
    monkeypatch.setattr(analyze_risk, "GRID_TRIAL_LENGTH", 10)
    return report_path


@pytest.mark.asyncio
async def test_main_writes_report_with_all_sections(populated_db):
    report_path = populated_db
    await analyze_risk.main()

    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")

    assert "## Por estrategia" in content
    assert "## Pool combinado" in content
    assert "## Sensibilidad" in content
    non_experimental = [s for s in STRATEGIES if s not in EXPERIMENTAL_STRATEGIES]
    for strategy in non_experimental:
        assert f"`{strategy}`" in content
    one_experimental = next(iter(EXPERIMENTAL_STRATEGIES))
    assert f"`{one_experimental}`" in content
    # Las estrategias sin ninguna operacion OOS se reportan, no rompen el script.
    other_experimental = next(s for s in EXPERIMENTAL_STRATEGIES if s != one_experimental)
    assert f"`{other_experimental}`" in content
    assert "Sin operaciones OOS" in content


@pytest.mark.asyncio
async def test_main_reports_sl_cap_exclusions(populated_db):
    report_path = populated_db
    await analyze_risk.main()
    content = report_path.read_text(encoding="utf-8")
    # La fila de tope 30% debe mostrar al menos 1 excluida por estrategia
    # (la operacion sintetica con sl_margin_loss_pct=60.0).
    assert "| 30%" in content
