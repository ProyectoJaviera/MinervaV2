"""Presupuesto diario del LLM con reserva bajo lock (subfase 3.6, fase i)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.llm.budget import LlmBudgetTracker, local_day_start_utc
from app.persistence.repositories import llm_logs_repo


def make_settings(**overrides) -> Settings:
    defaults = {"LLM_DAILY_BUDGET_USD": 1.0, "REPORT_TIMEZONE": "America/Santiago"}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_local_day_start_is_midnight_in_the_configured_timezone():
    # 2026-01-01 02:00 UTC es 2025-12-31 23:00 en America/Santiago (UTC-3 en verano).
    now = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    start = local_day_start_utc("America/Santiago", now)
    assert start.isoformat() == "2025-12-31T03:00:00+00:00"


@pytest.mark.asyncio
async def test_reserve_succeeds_while_there_is_room(db):
    tracker = LlmBudgetTracker()
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    assert await tracker.try_reserve(db, settings, 0.4) is True
    assert await tracker.try_reserve(db, settings, 0.4) is True


@pytest.mark.asyncio
async def test_reserve_is_rejected_once_the_cap_would_be_exceeded(db):
    tracker = LlmBudgetTracker()
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    assert await tracker.try_reserve(db, settings, 0.6) is True
    assert await tracker.try_reserve(db, settings, 0.6) is False  # 0.6+0.6 > 1.0


@pytest.mark.asyncio
async def test_release_frees_room_for_a_new_reservation(db):
    tracker = LlmBudgetTracker()
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    assert await tracker.try_reserve(db, settings, 0.6) is True
    assert await tracker.try_reserve(db, settings, 0.6) is False
    await tracker.release(0.6)
    assert await tracker.try_reserve(db, settings, 0.6) is True


@pytest.mark.asyncio
async def test_already_spent_cost_from_llm_logs_counts_against_the_cap(db):
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    now = datetime.now(UTC)
    await llm_logs_repo.insert(
        db, signal_group_key="g1", shadow_trade_id=None, fase="MEDICION",
        candle_close_time=now, decision_delay_s=1.0, hour_utc=now.hour, atr_pct=None,
        model="claude-sonnet-5-5", prompt_version="v1", prompt_sha256="x", prompt="p",
        response_raw=None, status="OK", error=None, decision="APROBAR",
        input_tokens=100, output_tokens=50, cost_usd=0.9, latency_ms=10, created_at=now,
    )
    tracker = LlmBudgetTracker()
    assert await tracker.try_reserve(db, settings, 0.2, now=now) is False  # 0.9+0.2 > 1.0
    assert await tracker.try_reserve(db, settings, 0.05, now=now) is True


@pytest.mark.asyncio
async def test_spend_from_yesterday_does_not_count_against_todays_cap(db):
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    await llm_logs_repo.insert(
        db, signal_group_key="g1", shadow_trade_id=None, fase="MEDICION",
        candle_close_time=yesterday, decision_delay_s=1.0, hour_utc=yesterday.hour,
        atr_pct=None, model="claude-sonnet-5-5", prompt_version="v1", prompt_sha256="x",
        prompt="p", response_raw=None, status="OK", error=None, decision="APROBAR",
        input_tokens=100, output_tokens=50, cost_usd=0.9, latency_ms=10, created_at=yesterday,
    )
    tracker = LlmBudgetTracker()
    assert await tracker.try_reserve(db, settings, 0.9, now=now) is True


@pytest.mark.asyncio
async def test_concurrent_reservations_never_exceed_the_daily_cap(db):
    settings = make_settings(LLM_DAILY_BUDGET_USD=1.0)
    tracker = LlmBudgetTracker()

    async def attempt():
        ok = await tracker.try_reserve(db, settings, 0.3)
        await asyncio.sleep(0)  # fuerza el intercalado entre corrutinas
        return ok

    results = await asyncio.gather(*(attempt() for _ in range(10)))
    accepted = sum(results)
    assert accepted == 3  # 0.3 * 3 = 0.9 <= 1.0 < 0.3 * 4
