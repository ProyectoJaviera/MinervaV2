"""Etiquetado en vivo de sombras durante la medicion (TAREA B, docs/
FASE3_6_LLM.md seccion k): fuego y olvido sobre `LlmDecisionService.
decide_group` tal cual existe, con las reglas del plan aprobado por el
usuario -- hash congelado (sin descartes silenciosos, WARNING cada ciclo),
retraso maximo (SIN_LLM sin fila nueva), fallo -> SIN_LLM (nunca RECHAZADA),
nunca toca trades reales, tareas en un set con done-callback que nunca es
silencioso, y cierre limpio que cancela lo pendiente. Todo sin red
(`FakeLlmClient`)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.llm.client import FakeLlmClient, LlmRawResponse, LlmTimeoutError
from app.llm.decision_service import LlmDecisionService
from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.persistence.repositories import llm_logs_repo, shadow_repo, trades_repo
from app.trading import shadow_live_labeler as live_labeler_module
from app.trading.shadow_live_labeler import MAX_LABELING_DELAY_SECONDS, ShadowLiveLabeler

RECENT = lambda: datetime.now(UTC) - timedelta(seconds=5)  # noqa: E731


def make_settings(**overrides) -> Settings:
    defaults = {
        "LLM_LIVE_LABELING_ENABLED": True, "LLM_DAILY_BUDGET_USD": 1.0, "LLM_MAX_TOKENS": 300,
        "LLM_MAX_CONCURRENCY": 4, "LLM_PRICE_INPUT_PER_MTOK": 2.0,
        "LLM_PRICE_OUTPUT_PER_MTOK": 10.0, "ANTHROPIC_SONNET_MODEL": "claude-sonnet-5-5",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


async def _open_shadow(
    db, key="BTCUSDT|LONG|c1", candle_close_time: datetime | None = None,
    status: TradeStatus = TradeStatus.OPEN,
) -> ShadowTrade:
    candle_close_time = candle_close_time or RECENT()
    trade = ShadowTrade(
        symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21", status=status,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=candle_close_time, contributing_strategies=["ema_cross_9_21"],
        signal_group_key=key, candle_close_time=candle_close_time,
    )
    trade.id = await shadow_repo.insert_if_new(db, trade)
    return trade


def valid_response(decision="APROBAR") -> LlmRawResponse:
    text = f'{{"decision": "{decision}", "confianza": 0.8, "razonamiento": "ok"}}'
    return LlmRawResponse(text=text, input_tokens=1100, output_tokens=40, latency_ms=800)


class FakeDecisionService:
    """Sustituye `LlmDecisionService.decide_group` para probar
    `ShadowLiveLabeler` en aislamiento (encolado, retraso, hash congelado,
    cierre limpio) sin depender de construir features reales."""

    def __init__(self, exc: Exception | None = None, delay: float = 0.0) -> None:
        self.calls: list[dict] = []
        self._exc = exc
        self._delay = delay

    async def decide_group(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc is not None:
            raise self._exc


async def _wait_for_tasks(labeler: ShadowLiveLabeler) -> None:
    tasks = list(labeler._tasks)
    if tasks:
        await asyncio.wait(tasks)


# --- flag apagado = comportamiento identico al actual ---------------------------


@pytest.mark.asyncio
async def test_flag_disabled_never_creates_a_task(db):
    shadow = await _open_shadow(db)
    fake = FakeDecisionService()
    labeler = ShadowLiveLabeler(db, make_settings(LLM_LIVE_LABELING_ENABLED=False), fake)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)

    assert labeler._tasks == set()
    assert fake.calls == []


# --- hash congelado: sin descartes silenciosos -----------------------------------


def test_frozen_hash_mismatch_warns_every_cycle_not_just_once(monkeypatch, caplog):
    monkeypatch.setattr(live_labeler_module, "FROZEN_PROMPT_SHA256", "distinto-a-proposito")
    labeler = ShadowLiveLabeler(None, make_settings(), FakeDecisionService())

    with caplog.at_level(logging.WARNING, logger="app.trading.shadow_live_labeler"):
        labeler.note_cycle_start()
        labeler.note_cycle_start()

    assert labeler.frozen_hash_ok is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


@pytest.mark.asyncio
async def test_frozen_hash_mismatch_means_maybe_label_does_nothing(monkeypatch, db):
    monkeypatch.setattr(live_labeler_module, "FROZEN_PROMPT_SHA256", "distinto-a-proposito")
    shadow = await _open_shadow(db)
    fake = FakeDecisionService()
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()  # deja la cache del ciclo en False

    labeler.maybe_label(shadow)

    assert labeler._tasks == set()
    assert fake.calls == []


def test_frozen_hash_ok_matches_the_value_verified_against_the_real_v4_pilot():
    # El valor congelado que el usuario dio tras revisar el piloto real
    # (docs/FASE3_6_LLM.md seccion k) debe coincidir con el PROMPT_SHA256
    # actual del codigo -- si no, algo cambio el prompt/features sin permiso.
    labeler = ShadowLiveLabeler(None, make_settings(), FakeDecisionService())
    assert labeler.frozen_hash_ok is True


# --- retraso: deja SIN_LLM sin fila nueva ---------------------------------------


@pytest.mark.asyncio
async def test_delay_over_the_threshold_leaves_sin_llm_without_a_new_row(db):
    old_candle = datetime.now(UTC) - timedelta(seconds=MAX_LABELING_DELAY_SECONDS + 60)
    shadow = await _open_shadow(db, candle_close_time=old_candle)
    fake = FakeDecisionService()
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    assert fake.calls == []  # nunca llego a llamar a decide_group
    assert await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key) is None
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "SIN_LLM"


@pytest.mark.asyncio
async def test_delay_just_under_the_threshold_still_labels(db):
    recent_candle = datetime.now(UTC) - timedelta(seconds=MAX_LABELING_DELAY_SECONDS - 30)
    shadow = await _open_shadow(db, candle_close_time=recent_candle)
    fake = FakeDecisionService()
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_an_already_closed_shadow_leaves_sin_llm_without_a_new_row(db):
    shadow = await _open_shadow(db)
    await shadow_repo.close(
        db, shadow.id, exit_price=101.0, fee_exit_usdt=0.01, slippage_exit_usdt=0.0,
        pnl_gross_usdt=1.0, pnl_net_usdt=0.99, close_reason="TP", fill_source="TICK",
    )
    fake = FakeDecisionService()
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)  # el objeto en memoria todavia dice OPEN
    await _wait_for_tasks(labeler)

    assert fake.calls == []  # _label_one relee el estado actual de la base
    assert await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key) is None


# --- integracion real con LlmDecisionService (sin red) --------------------------


@pytest.mark.asyncio
async def test_happy_path_labels_the_shadow_via_the_real_decision_service(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("APROBAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)
    labeler = ShadowLiveLabeler(db, make_settings(), service)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "APROBADA"
    row = await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key)
    assert row["fase"] == "MEDICION"
    assert row["status"] == "OK"


@pytest.mark.asyncio
async def test_a_real_failure_leaves_sin_llm_never_rechazada(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([LlmTimeoutError("se agoto el tiempo")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)
    labeler = ShadowLiveLabeler(db, make_settings(), service)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "SIN_LLM"
    assert reloaded.llm_decision != "RECHAZADA"


@pytest.mark.asyncio
async def test_labeling_never_creates_or_touches_a_real_trade(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("APROBAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)
    labeler = ShadowLiveLabeler(db, make_settings(), service)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    assert await trades_repo.get_open_positions(db) == []


# --- sin fuga de datos futuros ---------------------------------------------------


@pytest.mark.asyncio
async def test_features_only_use_data_available_at_the_candle_close(db):
    """`build_features_from_shadow_trade` (reusado tal cual, sin cambios en
    `app/llm/prompts.py`) ya tiene su propia prueba de fuga dedicada en
    `test_llm_market_features.py`/`test_llm_prompts.py`. Aqui solo se verifica
    que `ShadowLiveLabeler` le pasa `trade.candle_close_time` sin modificarlo
    -- el limite de instante real lo aplican esas funciones, no este modulo."""
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("APROBAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)
    labeler = ShadowLiveLabeler(db, make_settings(), service)
    labeler.note_cycle_start()

    labeler.maybe_label(shadow)
    await _wait_for_tasks(labeler)

    row = await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key)
    assert row["candle_close_time"] == shadow.candle_close_time.isoformat()


# --- tareas en un set con done-callback que nunca es silenciosa -----------------


@pytest.mark.asyncio
async def test_an_unexpected_exception_is_logged_never_silent(db, caplog):
    shadow = await _open_shadow(db)
    fake = FakeDecisionService(exc=RuntimeError("boom"))
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()

    with caplog.at_level(logging.ERROR, logger="app.trading.shadow_live_labeler"):
        labeler.maybe_label(shadow)
        await _wait_for_tasks(labeler)
        await asyncio.sleep(0)  # deja correr el done-callback agendado con call_soon

    assert any("fallo de forma inesperada" in r.getMessage() for r in caplog.records)
    assert labeler._tasks == set()


# --- cierre limpio: cancela lo pendiente -----------------------------------------


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_tasks(db):
    shadow = await _open_shadow(db)
    fake = FakeDecisionService(delay=5.0)
    labeler = ShadowLiveLabeler(db, make_settings(), fake)
    labeler.note_cycle_start()
    labeler.maybe_label(shadow)
    assert len(labeler._tasks) == 1

    await labeler.shutdown()

    assert labeler._tasks == set()


@pytest.mark.asyncio
async def test_shutdown_with_no_pending_tasks_is_a_no_op(db):
    labeler = ShadowLiveLabeler(db, make_settings(), FakeDecisionService())

    await labeler.shutdown()  # no debe lanzar ni bloquear

    assert labeler._tasks == set()
