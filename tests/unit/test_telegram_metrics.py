"""/metrics del bot de Telegram (subfase 3.7): SOLO AVANCE de la medicion, con el gate
cerrado y tambien abierto (decision del 2026-10-17). Nunca PnL de sombras ni la
comparacion APROBADA/RECHAZADA; el veredicto sale de ai_value_report.py."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.persistence.database import Database
from app.telegram.commands import CommandRouter
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256
from tests.unit.synthetic_measurement import insert_groups, scenario
from tests.unit.test_position_monitor import _open_long
from tests.unit.test_telegram_commands import HUGE, SqlTrace, make_ctx

SMALL_N = 20
FORBIDDEN_IN_REPLY = (
    "Diferencia de esperanza", "IC 95", "APORTA_VALOR", "NO_APORTA", "INCONCLUSO",
    "pnl_net", "exit_price", "close_reason", str(int(HUGE)), "4938270",
)


@pytest.fixture
async def file_db(tmp_path):
    path = tmp_path / "metrics.db"
    database = Database(str(path))
    await database.connect()
    yield database, path
    await database.close()


def frozen_scenario(n_approved, n_rejected, **kw):
    groups = scenario(n_approved, n_rejected, approved_ret=HUGE, rejected_ret=-HUGE, **kw)
    for g in groups:
        g.prompt_hash = FROZEN_PROMPT_SHA256  # la medicion real usa el hash congelado
        g.model = "claude-sonnet-5-5"
    return groups


@pytest.fixture
def small_gate(monkeypatch):
    monkeypatch.setattr("app.trading.measurement_status.MIN_EFFECTIVE_N", SMALL_N)
    monkeypatch.setattr("app.telegram.commands.MIN_EFFECTIVE_N", SMALL_N)


async def test_metrics_without_any_measurement_rows(db):
    ctx, _ = make_ctx(db)

    text = await CommandRouter(ctx)("/metrics")

    assert text.splitlines()[0] == "MODO PAPER TRADING"
    assert "Sin filas de medicion todavia." in text
    assert "Gate CERRADO" in text
    assert "Cuenta real simulada: sin operaciones cerradas todavia." in text


async def test_metrics_with_the_gate_closed_show_only_progress(file_db):
    db, path = file_db
    insert_groups(path, frozen_scenario(8, 5))
    ctx, _ = make_ctx(db)

    text = await CommandRouter(ctx)("/metrics")

    assert "N efectivo APROBADA: 8/300 (faltan 292" in text
    assert "N efectivo RECHAZADA: 5/300 (faltan 295" in text
    assert "Tasa de aprobacion del LLM: 61.5%" in text
    assert "Gate CERRADO" in text and "GATE ABIERTO" not in text
    for forbidden in FORBIDDEN_IN_REPLY:
        assert forbidden not in text


async def test_metrics_with_the_gate_open_still_show_only_progress_and_point_to_the_procedure(
    file_db, small_gate,
):
    db, path = file_db
    insert_groups(path, frozen_scenario(SMALL_N, SMALL_N))
    ctx, _ = make_ctx(db)

    text = await CommandRouter(ctx)("/metrics")

    assert f"N efectivo APROBADA: {SMALL_N}/{SMALL_N}" in text
    assert "GATE ABIERTO" in text and "docs/VEREDICTO_3_6.md" in text
    assert "no muestra resultados ni la comparacion APROBADA/RECHAZADA" in text
    assert "Gate CERRADO" not in text
    for forbidden in FORBIDDEN_IN_REPLY:
        assert forbidden not in text


async def test_metrics_never_run_a_pnl_query_over_shadow_trades(file_db, small_gate):
    db, path = file_db
    insert_groups(path, frozen_scenario(SMALL_N, SMALL_N))
    ctx, _ = make_ctx(db)

    async with SqlTrace(db) as trace:
        await CommandRouter(ctx)("/metrics")

    touched = [s.lower() for s in trace.statements if "shadow_trades" in s.lower()]
    assert touched  # lee los conteos de la medicion...
    for sql in touched:  # ...y ningun campo de resultado
        for forbidden in ("pnl", "exit_price", "close_reason", "select *"):
            assert forbidden not in sql, sql


async def test_metrics_include_the_real_account_by_strategy(db, reveal_open):
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 105.0})
    trade = await _open_long(backend)
    await backend.close_if_open(trade.id, 105.0, "TP", fill_source="TICK")

    text = await CommandRouter(ctx)("/metrics")

    assert "Cuenta real simulada: 1 operaciones cerradas" in text
    assert "- ema_cross_9_21: 1 cerradas | PF n/d | esperanza +" in text


async def test_metrics_hide_the_real_account_results_without_the_official_record(db):
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 105.0})
    trade = await _open_long(backend)
    await backend.close_if_open(trade.id, 105.0, "TP", fill_source="TICK")

    text = await CommandRouter(ctx)("/metrics")

    assert (
        "Cuenta real simulada: 1 operaciones cerradas "
        "(PF y esperanza: ocultos hasta el registro oficial del veredicto" in text
    )
    for leaked in ("PF n/d", "PF 1", "esperanza +", "esperanza -", "ema_cross_9_21:"):
        assert leaked not in text


async def test_metrics_never_read_the_account_pnl_while_the_results_are_hidden(file_db):
    db, path = file_db
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 105.0})
    trade = await _open_long(backend)
    await backend.close_if_open(trade.id, 105.0, "TP", fill_source="TICK")

    async with SqlTrace(db) as trace:
        await CommandRouter(ctx)("/metrics")

    trade_reads = [s.lower() for s in trace.statements if "from trades" in s.lower()]
    assert trade_reads  # cuenta las cerradas...
    assert not any("pnl" in s or "select *" in s for s in trade_reads)  # ...sin leer el PnL


def test_the_commands_module_cannot_reach_the_verdict_or_the_shadow_results():
    """Garantia estructural: no importa nada que calcule o muestre el veredicto ni
    los resultados de las sombras."""
    source = (Path(__file__).resolve().parents[2] / "app" / "telegram" / "commands.py").read_text(
        encoding="utf-8"
    )
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    for banned in (
        "shadow_report", "ai_value", "ai_value_report", "ai_value_placebo", "shadow_repo",
    ):
        assert not any(name.split(".")[-1] == banned for name in imported), banned
