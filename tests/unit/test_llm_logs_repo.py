"""Repo de `llm_logs` (subfase 3.6): grupos del piloto y de la medicion, sin
mezclar dos versiones del prompt (ajuste de la fase ii)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.repositories import llm_logs_repo

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _insert(db, *, key, fase, prompt_sha256, created_at):
    await llm_logs_repo.insert(
        db, signal_group_key=key, shadow_trade_id=None, fase=fase, candle_close_time=created_at,
        decision_delay_s=1.0, hour_utc=created_at.hour, atr_pct=None, model="claude-sonnet-5-5",
        prompt_version="v1", prompt_sha256=prompt_sha256, prompt="p", response_raw=None,
        status="OK", error=None, decision="APROBAR", input_tokens=10, output_tokens=5,
        cost_usd=0.001, latency_ms=10, created_at=created_at,
    )


@pytest.mark.asyncio
async def test_get_piloto_signal_group_keys_only_returns_piloto_rows(db):
    await _insert(db, key="g1", fase="PILOTO", prompt_sha256="hash_v1", created_at=NOW)
    await _insert(db, key="g2", fase="MEDICION", prompt_sha256="hash_v1",
                  created_at=NOW + timedelta(minutes=1))
    assert await llm_logs_repo.get_piloto_signal_group_keys(db) == {"g1"}


@pytest.mark.asyncio
async def test_measurement_keys_defaults_to_the_most_recent_prompt_version(db):
    # Version vieja del prompt (congelacion con un error, se corrige y se repite).
    await _insert(db, key="old1", fase="MEDICION", prompt_sha256="hash_old",
                  created_at=NOW)
    await _insert(db, key="old2", fase="MEDICION", prompt_sha256="hash_old",
                  created_at=NOW + timedelta(minutes=1))
    # Version nueva, mas reciente.
    await _insert(db, key="new1", fase="MEDICION", prompt_sha256="hash_new",
                  created_at=NOW + timedelta(hours=1))

    keys = await llm_logs_repo.get_measurement_signal_group_keys(db)

    assert keys == {"new1"}  # no se mezcla con hash_old


@pytest.mark.asyncio
async def test_measurement_keys_can_be_pinned_to_an_explicit_prompt_version(db):
    await _insert(db, key="old1", fase="MEDICION", prompt_sha256="hash_old", created_at=NOW)
    await _insert(db, key="new1", fase="MEDICION", prompt_sha256="hash_new",
                  created_at=NOW + timedelta(hours=1))

    keys = await llm_logs_repo.get_measurement_signal_group_keys(db, prompt_sha256="hash_old")

    assert keys == {"old1"}


@pytest.mark.asyncio
async def test_measurement_keys_is_empty_when_there_is_no_medicion_row_yet(db):
    await _insert(db, key="g1", fase="PILOTO", prompt_sha256="hash_v1", created_at=NOW)
    assert await llm_logs_repo.get_measurement_signal_group_keys(db) == set()


@pytest.mark.asyncio
async def test_a_failed_call_still_counts_as_a_measurement_row(db):
    # TIMEOUT/ERROR_HTTP/BUDGET_EXCEEDED dejan fila igual (status != 'OK'); su
    # grupo entra en measurement_keys lo mismo que uno exitoso.
    await _insert(db, key="ok1", fase="MEDICION", prompt_sha256="hash_v1", created_at=NOW)
    await llm_logs_repo.insert(
        db, signal_group_key="failed1", shadow_trade_id=None, fase="MEDICION",
        candle_close_time=NOW, decision_delay_s=20.0, hour_utc=NOW.hour, atr_pct=None,
        model="claude-sonnet-5-5", prompt_version="v1", prompt_sha256="hash_v1",
        prompt="p", response_raw=None, status="TIMEOUT", error="se agoto el tiempo",
        decision=None, input_tokens=None, output_tokens=None, cost_usd=0.002, latency_ms=None,
        created_at=NOW + timedelta(seconds=1),
    )

    keys = await llm_logs_repo.get_measurement_signal_group_keys(db, prompt_sha256="hash_v1")

    assert keys == {"ok1", "failed1"}
