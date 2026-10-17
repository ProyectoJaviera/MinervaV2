"""Subfase 3.9, aislamiento del brazo ejecutado (como se hizo con Telegram): un fallo o un
cuelgue del brazo NUNCA afecta al etiquetado ni al scheduler; la etiqueta ya esta persistida
cuando el brazo la ve; la medicion es identica con el brazo encendido y apagado; y la cuenta
es solo paper (ninguna ruta a `BitunixBackend`, hash de prompt congelado intacto).

Todo sin red ni gasto: `FakeLlmClient` y un cliente REST falso."""

from __future__ import annotations

import ast
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.core.scheduler import Scheduler
from app.execution.paper_backend import PaperBackend
from app.llm.client import FakeLlmClient
from app.llm.decision_service import LlmDecisionService
from app.llm.prompts import PROMPT_SHA256
from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.persistence.repositories import llm_logs_repo, shadow_repo, trades_repo
from app.persistence.repositories import paper_execution_repo as log_repo
from app.trading.measurement_status import compute_status_async
from app.trading.paper_execution import PaperExecutionArm
from app.trading.shadow_book import ShadowBook
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256, ShadowLiveLabeler
from tests.unit.test_paper_execution import (
    FakeRestClient,
    add_spec,
    make_candidate,
    make_result,
)
from tests.unit.test_shadow_live_labeler import valid_response

REPO_ROOT = Path(__file__).resolve().parents[2]


def world_settings(**overrides) -> Settings:
    defaults = dict(
        PAPER_EXECUTION_ENABLED=True, LLM_LIVE_LABELING_ENABLED=True, LLM_DAILY_BUDGET_USD=1.0,
        LLM_MAX_TOKENS=300, LLM_MAX_CONCURRENCY=4, LLM_PRICE_INPUT_PER_MTOK=2.0,
        LLM_PRICE_OUTPUT_PER_MTOK=10.0, ANTHROPIC_SONNET_MODEL="claude-sonnet-5-5",
        DEFAULT_MARGIN_USDT=5.0, LEVERAGE=10, INITIAL_CAPITAL_USDT=100.0,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def build_world(db, responses, *, arm_enabled=True, price=100.0, symbols=("BTCUSDT",)):
    settings = world_settings(PAPER_EXECUTION_ENABLED=arm_enabled)
    rest = FakeRestClient(price)
    backend = PaperBackend(db, rest, settings)
    arm = PaperExecutionArm(db, settings, backend)
    client = FakeLlmClient(responses)
    service = LlmDecisionService(db, settings, client_factory=lambda s: client)
    labeler = ShadowLiveLabeler(db, settings, service, on_decision=arm.submit)
    for symbol in symbols:
        await add_spec(db, symbol=symbol)
    await arm.activate()
    labeler.note_cycle_start()
    return arm, labeler, backend, settings, rest


async def open_shadow(db, candidate, *, key=None) -> ShadowTrade:
    close = candidate.candle_close_time.isoformat()
    key = key or f"{candidate.symbol}|{candidate.side.value}|{close}"
    trade = ShadowTrade(
        symbol=candidate.symbol, side=candidate.side,
        strategy=candidate.contributing_strategies[0],
        status=TradeStatus.OPEN, leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=0.5,
        entry_price=100.0, opened_at=candidate.candle_close_time,
        contributing_strategies=list(candidate.contributing_strategies), signal_group_key=key,
        candle_close_time=candidate.candle_close_time, sl_price=97.0, tp_price=110.0,
    )
    trade.id = await shadow_repo.insert_if_new(db, trade)
    return trade


async def label_and_wait(labeler, arm, shadow, candidate) -> None:
    labeler.maybe_label(shadow, candidate)
    for _ in range(3):
        tasks = list(labeler._tasks) + list(arm._tasks)
        if not tasks:
            break
        await asyncio.wait(tasks, timeout=10)


# --- camino completo y orden -----------------------------------------------------------------


async def test_full_path_labels_first_and_then_opens_a_paper_trade(db):
    arm, labeler, _, _, _ = await build_world(db, [valid_response("APROBAR")])
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    stored = await shadow_repo.get(db, shadow.id)
    assert stored.llm_decision == "APROBADA"
    assert stored.executed_in_real_account is False  # el brazo NO escribe en las sombras
    log = await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key)
    assert log["status"] == "OK" and log["decision"] == "APROBAR" and log["fase"] == "MEDICION"
    entry = await log_repo.get_by_key(db, shadow.signal_group_key)
    assert entry.outcome == "OPENED"
    trade = (await trades_repo.get_open_positions(db))[0]
    assert trade.decision_source == "LLM_APROBADA" and trade.id == entry.trade_id


async def test_the_label_is_persisted_before_the_arm_hears_about_it(db):
    events: list[str] = []
    arm, labeler, _, settings, _ = await build_world(db, [valid_response("APROBAR")])
    inner = labeler.decision_service.decide_group

    async def spying_decide(**kwargs):
        result = await inner(**kwargs)
        row = await shadow_repo.get(db, kwargs["shadow_trade_id"])
        log = await llm_logs_repo.get_by_signal_group_key(db, kwargs["signal_group_key"])
        assert row.llm_decision == "APROBADA" and log["status"] == "OK"  # ya escrito
        events.append("etiqueta_persistida")
        return result

    labeler.decision_service.decide_group = spying_decide  # type: ignore[method-assign]
    real_submit = arm.submit
    labeler.on_decision = lambda *a: (events.append("brazo_avisado"), real_submit(*a))[1]
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    assert events == ["etiqueta_persistida", "brazo_avisado"]


async def test_the_hook_receives_the_fresh_shadow_the_result_and_the_unchanged_candidate(db):
    seen: list[tuple] = []
    arm, labeler, _, _, _ = await build_world(db, [valid_response("RECHAZAR")])
    labeler.on_decision = lambda *a: seen.append(a)
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    (got_shadow, got_result, got_candidate), = seen
    assert got_shadow.id == shadow.id and got_result.label == "RECHAZADA"
    assert got_candidate is candidate


async def test_a_rejected_decision_leaves_the_account_and_the_log_untouched(db):
    arm, labeler, _, _, _ = await build_world(db, [valid_response("RECHAZAR")])
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    assert (await shadow_repo.get(db, shadow.id)).llm_decision == "RECHAZADA"
    assert await trades_repo.get_trades(db) == []
    assert await log_repo.get_recent(db) == []


# --- un fallo o un cuelgue del brazo no afectan a nadie -----------------------------------


async def test_a_hook_that_raises_does_not_affect_the_labeling(db, caplog):
    arm, labeler, _, _, _ = await build_world(db, [valid_response("APROBAR")])

    def boom(*args):
        raise RuntimeError("el brazo exploto")

    labeler.on_decision = boom
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    with caplog.at_level(logging.ERROR, logger="app.trading.shadow_live_labeler"):
        await label_and_wait(labeler, arm, shadow, candidate)
        await asyncio.sleep(0)

    assert (await shadow_repo.get(db, shadow.id)).llm_decision == "APROBADA"
    assert await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key) is not None
    messages = [r.getMessage() for r in caplog.records]
    assert any("Gancho de decision fallo (RuntimeError)" in m for m in messages)
    assert not any("fallo de forma inesperada" in m for m in messages)  # la tarea no fallo
    assert labeler._tasks == set()


async def test_an_arm_failure_after_the_label_is_contained_and_leaves_a_reason(db, monkeypatch):
    arm, labeler, backend, _, _ = await build_world(db, [valid_response("APROBAR")])

    async def broken_open(*args, **kwargs):
        raise RuntimeError("PaperBackend roto")

    monkeypatch.setattr(backend, "open_position", broken_open)
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    assert (await shadow_repo.get(db, shadow.id)).llm_decision == "APROBADA"
    entry = await log_repo.get_by_key(db, shadow.signal_group_key)
    assert entry.outcome == "SKIPPED" and entry.reason == "OPEN_ERROR"
    assert labeler._tasks == set() and arm._tasks == set()


async def test_a_hung_arm_blocks_neither_the_labeler_nor_the_scheduler_cycle(db, monkeypatch):
    arm, labeler, _, settings, rest = await build_world(db, [valid_response("APROBAR")])
    gate = asyncio.Event()

    async def hung_handle(*args, **kwargs):
        await gate.wait()

    monkeypatch.setattr(arm, "handle", hung_handle)
    candidate = make_candidate()

    async def fake_generation(db_, rest_, settings_):
        return [candidate]

    monkeypatch.setattr("app.core.scheduler.run_signal_generation_cycle", fake_generation)
    scheduler = Scheduler(
        db, rest, PaperBackend(db, rest, settings), settings, shadow=ShadowBook(db, settings),
        live_labeler=labeler,
    )

    await asyncio.wait_for(scheduler.run_cycle(), timeout=5)  # vuelve aunque el brazo cuelgue
    await asyncio.wait(list(labeler._tasks), timeout=10)  # el etiquetado termina igual

    assert scheduler.last_cycle_at is not None
    shadows = await shadow_repo.get_open(db)
    assert [s.llm_decision for s in shadows] == ["APROBADA"]
    assert len(arm._tasks) == 1  # el brazo sigue colgado, solo, en su propia tarea
    assert labeler._tasks == set()
    await arm.shutdown()
    assert arm._tasks == set()


async def test_with_the_switch_off_nothing_is_created_at_all(db):
    arm, labeler, _, _, _ = await build_world(db, [valid_response("APROBAR")], arm_enabled=False)
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)

    await label_and_wait(labeler, arm, shadow, candidate)

    assert arm.active is False
    assert (await shadow_repo.get(db, shadow.id)).llm_decision == "APROBADA"  # igual se etiqueta
    assert await trades_repo.get_trades(db) == []
    assert await log_repo.get_recent(db) == []


# --- la medicion no cambia ----------------------------------------------------------------


async def _scenario_dump(arm_enabled: bool, candle_close: datetime) -> dict:
    database = Database(":memory:")
    await database.connect()
    try:
        arm, labeler, _, _, _ = await build_world(
            database,
            [valid_response("APROBAR"), valid_response("RECHAZAR"), valid_response("APROBAR")],
            arm_enabled=arm_enabled, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        )
        groups = [
            make_candidate(symbol="BTCUSDT", side=Side.LONG),
            make_candidate(symbol="ETHUSDT", side=Side.LONG),
            make_candidate(symbol="SOLUSDT", side=Side.SHORT, strategies=("donchian_breakout_20",),
                           stop=103.0, take_profit=90.0),
        ]
        for candidate in groups:
            candidate.candle_close_time = candle_close  # mismos instantes en ambas corridas
            shadow = await open_shadow(database, candidate)
            await label_and_wait(labeler, arm, shadow, candidate)  # en orden: respuestas fijas

        status = await compute_status_async(database)
        shadows = [dict(r) for r in await database.fetch_all(
            "SELECT * FROM shadow_trades ORDER BY signal_group_key"
        )]
        logs = [
            {k: r[k] for k in (
                "signal_group_key", "shadow_trade_id", "fase", "status", "decision",
                "prompt_version", "prompt_sha256", "features_version", "model", "json_repaired",
                "input_tokens", "output_tokens", "cost_usd",
            )}
            for r in await database.fetch_all("SELECT * FROM llm_logs ORDER BY signal_group_key")
        ]
        return {
            "shadows": shadows, "logs": logs,
            "status": (
                status.total_medicion_rows, status.ok_by_label, status.n_eff_approved,
                status.n_eff_rejected, status.non_ok_counts, status.approval_rate,
                status.frozen_hash,
            ),
            "trades": len(await trades_repo.get_trades(database)),
            "log_rows": len(await log_repo.get_recent(database)),
        }
    finally:
        await database.close()


async def test_the_measurement_is_identical_with_the_arm_on_and_off():
    candle_close = datetime.now(UTC) - timedelta(seconds=20)

    off = await _scenario_dump(False, candle_close)
    on = await _scenario_dump(True, candle_close)

    # lo que alimenta el veredicto: toda la fila de cada sombra, los llm_logs y el avance
    assert on["shadows"] == off["shadows"]
    assert on["logs"] == off["logs"]
    assert on["status"] == off["status"]
    assert sorted(row["llm_decision"] for row in on["shadows"]) == [
        "APROBADA", "APROBADA", "RECHAZADA",
    ]
    # y solo la cuenta paper difiere: 1 apertura (BTC) y 2 omisiones registradas (SOL no
    # elegible; ETH fue RECHAZADA y ni siquiera tiene fila)
    assert (off["trades"], off["log_rows"]) == (0, 0)
    assert (on["trades"], on["log_rows"]) == (1, 2)


async def test_the_arm_writes_to_no_shadow_nor_llm_table(db):
    arm, _, _, _, _ = await build_world(db, [])
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)
    statements: list[str] = []
    await db.conn.set_trace_callback(statements.append)  # solo lo que hace el brazo desde aqui

    await arm.handle(shadow, make_result(), candidate)
    await db.conn.set_trace_callback(None)

    written = set()
    for sql in statements:
        words = sql.strip().split()
        head = words[0].upper() if words else ""
        if head == "INSERT":
            table = words[4] if words[1].upper() == "OR" else words[2]
        elif head in {"UPDATE", "DELETE"}:
            table = words[1] if head == "UPDATE" else words[2]
        else:
            continue
        written.add(table.split("(")[0].lower())
    assert written, statements
    assert written <= {"paper_execution_log", "trades", "system_state", "risk_rejections"}, written
    assert not written & {"shadow_trades", "llm_logs", "signals"}


# --- solo paper y prompt congelado --------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def test_no_module_of_the_bot_imports_the_real_execution_backend():
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        if path.name == "bitunix_backend.py":
            continue
        if any("bitunix_backend" in m or m.endswith("BitunixBackend")
               for m in _imported_modules(path)):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_the_arm_only_knows_the_paper_backend():
    imported = _imported_modules(REPO_ROOT / "app" / "trading" / "paper_execution.py")

    assert "app.execution.paper_backend.PaperBackend" in imported
    assert not any("bitunix_backend" in m or "BitunixBackend" in m for m in imported)
    assert not any(m.split(".")[-1] in {"anthropic", "httpx", "requests"} for m in imported)


def test_main_builds_a_paper_backend_and_never_a_real_one():
    source = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert "PaperBackend(" in source and "PaperExecutionArm(" in source
    assert "BitunixBackend" not in source


def test_the_frozen_prompt_hash_is_untouched_and_still_matches_the_label_hash():
    assert FROZEN_PROMPT_SHA256 == (
        "0fa0dd75a3fecdae36f3b77c8b7ec5d97d58a2238349283c61bfa7632188c777"
    )
    assert PROMPT_SHA256 == FROZEN_PROMPT_SHA256  # el etiquetado en vivo sigue habilitado


def test_the_arm_contract_constants_match_the_reports():
    """`real_money_report.py` no puede importar el brazo (su guardia de imports prohibe cualquier
    backend), asi que repite el contrato; aqui se comparan."""
    from app.trading import paper_execution, real_money_report

    assert paper_execution.DECISION_SOURCE == real_money_report.EXECUTED_ARM_DECISION_SOURCE
    assert paper_execution.KEY_REAL_ARM_STARTED_AT == real_money_report.KEY_REAL_ARM_STARTED_AT
    assert (
        paper_execution.KEY_REAL_ARM_DRAWDOWN_BASELINE
        == real_money_report.KEY_REAL_ARM_DRAWDOWN_BASELINE
    )


# --- /status de Telegram: una linea, sin PnL ------------------------------------------------


async def test_telegram_status_reports_the_arm_without_any_result(db):
    from app.telegram.commands import CommandRouter
    from tests.unit.test_telegram_commands import make_ctx

    ctx, _ = make_ctx(db)
    off = await CommandRouter(ctx)("/status")
    assert "Brazo ejecutado (paper): apagado" in off

    arm, _, _, _, _ = await build_world(db, [])
    candidate = make_candidate()
    shadow = await open_shadow(db, candidate)
    await arm.handle(shadow, make_result(), candidate)  # abre
    late = make_candidate(symbol="ETHUSDT")
    await add_spec(db, symbol="ETHUSDT")
    await arm.handle(await open_shadow(db, late), make_result(delay=75.0), late)  # tarde
    ctx.paper_arm = arm

    on = await CommandRouter(ctx)("/status")

    assert "Brazo ejecutado (paper): activo | abiertas hoy 1 | omitidas hoy 1" in on
