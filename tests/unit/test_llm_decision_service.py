"""Servicio de decision del LLM (subfase 3.6, fase i): etiqueta correcta en
cada caso, una fila de `llm_logs` por llamada, fallo -> SIN_LLM (nunca
RECHAZADA), etiqueta inmutable, presupuesto sin red ni cliente real, y
concurrencia sin superar el tope diario. Todo sin red (`FakeLlmClient`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.llm.client import FakeLlmClient, LlmHttpError, LlmRawResponse, LlmTimeoutError
from app.llm.decision_service import (
    STATUS_BUDGET_EXCEEDED,
    STATUS_ERROR_HTTP,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_TIMEOUT,
    LlmDecisionService,
    real_open_allowed,
)
from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.persistence.repositories import llm_logs_repo, shadow_repo

CANDLE = datetime(2026, 1, 1, 4, tzinfo=UTC)


def make_settings(**overrides) -> Settings:
    defaults = {
        "LLM_DAILY_BUDGET_USD": 1.0, "LLM_MAX_TOKENS": 300, "LLM_MAX_CONCURRENCY": 4,
        "LLM_REAL_MAX_DELAY_SECONDS": 60.0, "LLM_PRICE_INPUT_PER_MTOK": 2.0,
        "LLM_PRICE_OUTPUT_PER_MTOK": 10.0, "ANTHROPIC_SONNET_MODEL": "claude-sonnet-5-5",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def exploding_factory(_settings):
    raise AssertionError("no deberia construirse un cliente real sin red")


async def _open_shadow(db, key="BTCUSDT|LONG|c1") -> ShadowTrade:
    trade = ShadowTrade(
        symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21", status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=CANDLE, contributing_strategies=["ema_cross_9_21"], signal_group_key=key,
        candle_close_time=CANDLE,
    )
    trade.id = await shadow_repo.insert_if_new(db, trade)
    return trade


def valid_response(decision="APROBAR", confianza=0.8) -> LlmRawResponse:
    text = f'{{"decision": "{decision}", "confianza": {confianza}, "razonamiento": "ok"}}'
    return LlmRawResponse(text=text, input_tokens=1100, output_tokens=40, latency_ms=800)


# --- etiqueta correcta en cada caso --------------------------------------------


@pytest.mark.asyncio
async def test_approve_labels_the_shadow_trade_and_logs_ok(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("APROBAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="sistema", prompt_version="v1",
        user_message="usuario", estimated_input_tokens=1200, now=CANDLE + timedelta(seconds=5),
    )

    assert result.status == STATUS_OK and result.label == "APROBADA"
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "APROBADA"
    row = await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key)
    assert row["status"] == "OK" and row["decision"] == "APROBAR" and row["cost_usd"] > 0


@pytest.mark.asyncio
async def test_reject_labels_the_shadow_trade_as_rechazada(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("RECHAZAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="sistema", prompt_version="v1",
        user_message="usuario", estimated_input_tokens=1200, now=CANDLE,
    )

    assert result.status == STATUS_OK and result.label == "RECHAZADA"
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "RECHAZADA"


# --- fallo -> SIN_LLM, nunca RECHAZADA ------------------------------------------


@pytest.mark.asyncio
async def test_timeout_leaves_sin_llm_and_logs_the_cause(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([LlmTimeoutError("se agoto el tiempo")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="s", prompt_version="v1",
        user_message="u", estimated_input_tokens=1200, now=CANDLE,
    )

    assert result.status == STATUS_TIMEOUT and result.label == "SIN_LLM"
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "SIN_LLM"


@pytest.mark.asyncio
async def test_http_error_leaves_sin_llm(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([LlmHttpError("500")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="s", prompt_version="v1",
        user_message="u", estimated_input_tokens=1200, now=CANDLE,
    )

    assert result.status == STATUS_ERROR_HTTP and result.label == "SIN_LLM"


@pytest.mark.asyncio
async def test_invalid_json_leaves_sin_llm_and_keeps_the_raw_response(db):
    shadow = await _open_shadow(db)
    bad = LlmRawResponse(text="no es json", input_tokens=10, output_tokens=5, latency_ms=50)
    client = FakeLlmClient([bad])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="s", prompt_version="v1",
        user_message="u", estimated_input_tokens=1200, now=CANDLE,
    )

    assert result.status == STATUS_INVALID and result.label == "SIN_LLM"
    row = await llm_logs_repo.get_by_signal_group_key(db, shadow.signal_group_key)
    assert row["response_raw"] == "no es json"
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "SIN_LLM"


# --- presupuesto: sin red ni cliente real ---------------------------------------


@pytest.mark.asyncio
async def test_budget_exhausted_never_builds_or_calls_a_real_client(db):
    shadow = await _open_shadow(db)
    service = LlmDecisionService(
        db, make_settings(LLM_DAILY_BUDGET_USD=0.0), client_factory=exploding_factory
    )

    result = await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="s", prompt_version="v1",
        user_message="u", estimated_input_tokens=1200, now=CANDLE,
    )

    assert result.status == STATUS_BUDGET_EXCEEDED and result.label == "SIN_LLM"
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "SIN_LLM"


# --- etiqueta inmutable ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_call_on_the_same_group_does_not_relabel_the_shadow(db):
    shadow = await _open_shadow(db)
    client = FakeLlmClient([valid_response("APROBAR")])
    service = LlmDecisionService(db, make_settings(), client_factory=lambda s: client)
    await service.decide_group(
        signal_group_key=shadow.signal_group_key, shadow_trade_id=shadow.id,
        candle_close_time=CANDLE, system_prompt="s", prompt_version="v1",
        user_message="u", estimated_input_tokens=1200, now=CANDLE,
    )

    changed = await shadow_repo.set_llm_decision(db, shadow.id, "RECHAZADA")

    assert changed is False
    reloaded = await shadow_repo.get(db, shadow.id)
    assert reloaded.llm_decision == "APROBADA"


@pytest.mark.asyncio
async def test_the_trigger_blocks_a_direct_update_that_flips_the_label(db):
    shadow = await _open_shadow(db)
    await shadow_repo.set_llm_decision(db, shadow.id, "APROBADA")

    with pytest.raises(Exception, match="inmutable"):
        await db.execute(
            "UPDATE shadow_trades SET llm_decision = 'RECHAZADA' WHERE id = ?", (shadow.id,)
        )


# --- real_open_allowed (seccion g; no conectado al scheduler todavia) ----------


@pytest.mark.parametrize(
    "label,delay,expected",
    [
        ("APROBADA", 10.0, True),
        ("APROBADA", 60.0, True),
        ("APROBADA", 60.1, False),
        ("RECHAZADA", 10.0, False),
        ("SIN_LLM", 10.0, False),
    ],
)
def test_real_open_allowed(label, delay, expected):
    assert real_open_allowed(label, delay, make_settings()) is expected


# --- concurrencia: nunca se supera el tope diario -------------------------------


@pytest.mark.asyncio
async def test_concurrent_decisions_never_exceed_the_daily_budget(db):
    settings = make_settings(LLM_DAILY_BUDGET_USD=0.02, LLM_MAX_CONCURRENCY=4)
    # coste_max por llamada = 1200*2/1e6 + 300*10/1e6 = 0.0054 USD -> caben 3 de 10.
    client = FakeLlmClient([valid_response("APROBAR") for _ in range(10)])
    service = LlmDecisionService(db, settings, client_factory=lambda s: client)

    async def run(i: int):
        return await service.decide_group(
            signal_group_key=f"g{i}", shadow_trade_id=None, candle_close_time=CANDLE,
            system_prompt="s", prompt_version="v1", user_message="u",
            estimated_input_tokens=1200, now=CANDLE,
        )

    results = await asyncio.gather(*(run(i) for i in range(10)))

    accepted = [r for r in results if r.status != STATUS_BUDGET_EXCEEDED]
    assert 0 < len(accepted) < 10
    rows = await db.fetch_all("SELECT cost_usd FROM llm_logs")
    spent = sum(row["cost_usd"] for row in rows)
    assert spent <= settings.llm_daily_budget_usd + 1e-9
