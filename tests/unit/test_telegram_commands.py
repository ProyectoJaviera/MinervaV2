"""Comandos de lectura del bot de Telegram (subfase 3.7): /status, /positions,
/balance, ayuda y enrutador. Cuenta real simulada; las sombras (con un PnL
inconfundible sembrado) jamas aparecen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.execution.paper_backend import PaperBackend
from app.llm.decision_service import LlmDecisionService
from app.persistence.database import Database
from app.persistence.repositories import llm_logs_repo, system_state_repo
from app.telegram.commands import (
    COMMANDS,
    READ_ONLY,
    BotContext,
    CommandRouter,
    help_text,
    parse_command,
)
from app.trading import control_state, risk_engine
from app.trading.shadow_live_labeler import ShadowLiveLabeler
from tests.unit.synthetic_measurement import insert_groups, scenario
from tests.unit.test_position_monitor import FakeRest, _open_long, make_settings

NOW = datetime(2026, 10, 17, 15, 30, tzinfo=UTC)
HUGE = 987654.0  # PnL de sombra inconfundible: no debe aparecer jamas


def make_ctx(db, *, settings=None, marks=None, last_cycle_at=NOW - timedelta(minutes=3)):
    settings = settings or make_settings(LLM_DAILY_BUDGET_USD=1.0)
    rest = FakeRest(last=100.0)
    backend = PaperBackend(db, rest, settings)
    labeler = ShadowLiveLabeler(db, settings, LlmDecisionService(db, settings))
    ctx = BotContext(
        db=db, settings=settings, paper_backend=backend,
        scheduler=SimpleNamespace(last_cycle_at=last_cycle_at), live_labeler=labeler,
        monitor=SimpleNamespace(marks=dict(marks or {})), now=lambda: NOW,
    )
    return ctx, backend


@pytest.fixture
async def file_db(tmp_path):
    path = tmp_path / "commands.db"
    database = Database(str(path))
    await database.connect()
    yield database, path
    await database.close()


class SqlTrace:
    """Registra todo el SQL de la conexion de `Database` mientras se ejecuta un comando."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.statements: list[str] = []

    async def __aenter__(self):
        await self.db.conn.set_trace_callback(self.statements.append)
        return self

    async def __aexit__(self, *exc):
        await self.db.conn.set_trace_callback(None)


# --- enrutador y ayuda ---------------------------------------------------------------


def test_parse_command_handles_case_arguments_and_the_bot_suffix():
    assert parse_command("/status") == ("/status", [])
    assert parse_command("  /STATUS  ") == ("/status", [])
    assert parse_command("/status@MinervaBot") == ("/status", [])
    assert parse_command("/pause SI") == ("/pause", ["SI"])
    assert parse_command("hola") is None
    assert parse_command("") is None


def test_help_lists_the_seven_commands():
    text = help_text()

    assert len(COMMANDS) == 7
    for command in COMMANDS:
        assert command in text
    assert text.startswith("MODO PAPER TRADING")


async def test_router_answers_help_for_unknown_commands_and_silence_for_plain_text(db):
    ctx, _ = make_ctx(db)
    router = CommandRouter(ctx)

    assert await router("/start") == help_text()
    assert await router("/help") == help_text()
    assert await router("/inexistente") == help_text()
    assert await router("buenos dias") is None


# --- /status ------------------------------------------------------------------------------


async def test_status_on_an_empty_account(db):
    ctx, _ = make_ctx(db)

    text = await CommandRouter(ctx)("/status")

    assert text.splitlines()[0] == "MODO PAPER TRADING"
    assert "Capital inicial 100.00 | balance 100.00 | equity 100.00 USDT" in text
    assert "Posiciones abiertas: 0/5" in text
    assert "Kill switch: inactivo | Scheduler pausado: no" in text
    assert "Circuit breaker: inactivo | Stop por drawdown: inactivo" in text
    assert "Gasto LLM hoy: 0.0000 / 1.00 USD" in text
    assert "Ultimo ciclo de senales: hace 3 min" in text
    assert "Etiquetado en vivo: DESACTIVADO | hash congelado OK: si" in text
    assert "Medicion: APROBADA 0/300, RECHAZADA 0/300 | gate CERRADO" in text


async def test_status_reflects_pause_kill_switch_breaker_and_drawdown_stop(db):
    ctx, _ = make_ctx(db)
    await control_state.set_scheduler_paused(db, True)
    await risk_engine.activate_kill_switch(db)
    await system_state_repo.set_state(db, "drawdown_stop_active", "1")
    until = datetime.now(UTC) + timedelta(hours=2)
    await system_state_repo.set_state(db, "circuit_breaker_until", until.isoformat())

    text = await CommandRouter(ctx)("/status")

    assert "Kill switch: ACTIVO | Scheduler pausado: SI (la medicion esta detenida)" in text
    assert "Circuit breaker: ACTIVO hasta" in text and "Stop por drawdown: ACTIVO" in text


async def test_status_ignores_an_expired_circuit_breaker(db):
    ctx, _ = make_ctx(db)
    past = datetime.now(UTC) - timedelta(hours=1)
    await system_state_repo.set_state(db, "circuit_breaker_until", past.isoformat())

    assert "Circuit breaker: inactivo" in await CommandRouter(ctx)("/status")


async def test_status_shows_todays_llm_spend_against_the_budget(db):
    ctx, _ = make_ctx(db)
    await llm_logs_repo.insert(
        db, signal_group_key="k1", shadow_trade_id=None, fase="MEDICION",
        candle_close_time=NOW, decision_delay_s=5.0, hour_utc=15, atr_pct=None,
        model="m", prompt_version="v", prompt_sha256="h", prompt="p", response_raw=None,
        status="OK", error=None, decision="APROBAR", input_tokens=1, output_tokens=1,
        cost_usd=0.25, latency_ms=10, created_at=NOW - timedelta(hours=1),
    )

    text = await CommandRouter(ctx)("/status")

    assert "Gasto LLM hoy: 0.2500 / 1.00 USD" in text


async def test_status_flags_a_stale_or_missing_cycle(db):
    stale, _ = make_ctx(db, last_cycle_at=NOW - timedelta(minutes=120))
    never, _ = make_ctx(db, last_cycle_at=None)

    assert "hace 120 min (STALE)" in await CommandRouter(stale)("/status")
    assert "Ultimo ciclo de senales: ninguno todavia" in await CommandRouter(never)("/status")


# --- /positions y /balance ---------------------------------------------------------------


async def test_positions_without_open_trades(db):
    ctx, _ = make_ctx(db)

    text = await CommandRouter(ctx)("/positions")

    assert text == "MODO PAPER TRADING\nSin posiciones abiertas (cuenta real simulada)."


async def test_positions_show_the_floating_pnl_with_the_monitor_marks(db):
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 98.0})
    await _open_long(backend)

    text = await CommandRouter(ctx)("/positions")

    assert "Posiciones abiertas (cuenta real simulada): 1" in text
    assert "BTCUSDT LONG" in text and "SL 95.0" in text and "TP 110.0" in text
    assert "PnL flotante -" in text and "(mark 98.0)" in text


async def test_positions_without_a_mark_say_so_and_never_call_the_network(db):
    ctx, backend = make_ctx(db, marks={})
    await _open_long(backend)
    calls_before = list(backend.rest_client.calls)

    text = await CommandRouter(ctx)("/positions")

    assert "PnL flotante n/d (sin mark todavia)" in text
    assert backend.rest_client.calls == calls_before  # ningun REST desde el comando


async def test_balance_with_floating_pnl_realized_pnl_and_missing_marks(db):
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 104.0})
    trade = await _open_long(backend)
    initial = ctx.settings.initial_capital_usdt

    text = await CommandRouter(ctx)("/balance")

    assert "Cuenta real simulada (no incluye sombras):" in text
    assert f"Capital inicial: {initial:.2f} USDT" in text
    assert "Balance realizado: 100.00 USDT" in text
    equity_line = next(line for line in text.splitlines() if line.startswith("- Equity"))
    assert "parcial" not in equity_line
    assert float(equity_line.split(": ")[1].split()[0]) > initial  # mark 104 > entrada 100

    ctx.monitor.marks.clear()
    partial = await CommandRouter(ctx)("/balance")
    assert "parcial, sin mark de BTCUSDT" in partial

    ctx.monitor.marks["BTCUSDT"] = 105.0
    await backend.close_if_open(trade.id, 105.0, "TP", fill_source="TICK")
    closed = await CommandRouter(ctx)("/balance")
    realized = next(line for line in closed.splitlines() if "Balance realizado" in line)
    assert float(realized.split(": ")[1].split()[0]) > initial
    assert "Posiciones abiertas: 0" in closed


async def test_equity_matches_the_paper_backend_calculation(db):
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 97.5})
    await _open_long(backend)

    text = await CommandRouter(ctx)("/balance")

    expected = await backend.get_equity(marks={"BTCUSDT": 97.5})
    equity_line = next(line for line in text.splitlines() if line.startswith("- Equity"))
    assert float(equity_line.split(": ")[1].split()[0]) == pytest.approx(expected, abs=0.01)


# --- nunca sombras con resultados ----------------------------------------------------------


async def test_positions_and_balance_never_touch_shadow_trades(file_db):
    db, path = file_db
    insert_groups(path, scenario(5, 5, approved_ret=HUGE, rejected_ret=-HUGE))
    ctx, backend = make_ctx(db, marks={"BTCUSDT": 98.0})
    await _open_long(backend)

    async with SqlTrace(db) as trace:
        positions = await CommandRouter(ctx)("/positions")
        balance = await CommandRouter(ctx)("/balance")

    assert trace.statements
    assert not any("shadow_trades" in s.lower() for s in trace.statements)
    for text in (positions, balance):
        assert str(int(HUGE)) not in text


async def test_status_reads_shadow_trades_only_through_the_pnl_free_measurement_queries(file_db):
    db, path = file_db
    insert_groups(path, scenario(5, 5, approved_ret=HUGE, rejected_ret=-HUGE))
    ctx, _ = make_ctx(db)

    async with SqlTrace(db) as trace:
        text = await CommandRouter(ctx)("/status")

    assert str(int(HUGE)) not in text and "4938270" not in text
    for sql in (s.lower() for s in trace.statements):
        if "shadow_trades" in sql:
            for forbidden in ("pnl", "exit_price", "close_reason", "select *"):
                assert forbidden not in sql, sql


async def test_a_command_that_hangs_is_cut_off_by_the_router_timeout(db, monkeypatch):
    import asyncio

    ctx, _ = make_ctx(db)

    async def hang(_ctx):
        await asyncio.sleep(3600)

    monkeypatch.setitem(READ_ONLY, "/status", hang)
    real_wait_for = asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        return await real_wait_for(awaitable, timeout=0.05)

    monkeypatch.setattr("app.telegram.commands.asyncio.wait_for", fast_wait_for)

    with pytest.raises(TimeoutError):
        await CommandRouter(ctx)("/status")
