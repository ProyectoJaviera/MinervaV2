"""`GET /health` (incidente de estabilidad 2026-10-11): `last_beat_at`
(event loop vivo) y `last_cycle_at` (Scheduler, ultima evaluacion completa)
son señales DISTINTAS -- se llama la funcion de la ruta directo, sin
`TestClient` (sin precedente en el repo; ver `app/api/deps.py`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.routes.health import STALE_AFTER_MINUTES, health
from app.core.heartbeat import Heartbeat


class FakeScheduler:
    def __init__(self, last_cycle_at: datetime | None) -> None:
        self.last_cycle_at = last_cycle_at


@pytest.mark.asyncio
async def test_health_is_ok_with_a_recent_cycle():
    heartbeat = Heartbeat()
    now = datetime.now(UTC)
    heartbeat.beat(now=now)
    scheduler = FakeScheduler(last_cycle_at=now)

    result = await health(heartbeat=heartbeat, scheduler=scheduler)

    assert result["status"] == "ok"
    assert result["mode"] == "PAPER_TRADING"
    assert result["last_beat_at"] == now.isoformat()
    assert result["last_cycle_at"] == now.isoformat()
    assert result["log_dropped_count"] == 0


@pytest.mark.asyncio
async def test_health_is_stale_when_last_cycle_at_is_too_old():
    heartbeat = Heartbeat()
    heartbeat.beat()  # el loop sigue vivo...
    old = datetime.now(UTC) - timedelta(minutes=STALE_AFTER_MINUTES + 1)
    scheduler = FakeScheduler(last_cycle_at=old)  # ...pero el ciclo de negocio no avanza

    result = await health(heartbeat=heartbeat, scheduler=scheduler)

    assert result["status"] == "stale"
    assert result["last_beat_at"] is not None
    assert result["last_cycle_at"] == old.isoformat()


@pytest.mark.asyncio
async def test_health_is_stale_when_no_cycle_has_ever_completed():
    heartbeat = Heartbeat()
    scheduler = FakeScheduler(last_cycle_at=None)

    result = await health(heartbeat=heartbeat, scheduler=scheduler)

    assert result["status"] == "stale"
    assert result["last_beat_at"] is None
    assert result["last_cycle_at"] is None


@pytest.mark.asyncio
async def test_health_is_not_stale_just_under_the_threshold():
    heartbeat = Heartbeat()
    recent = datetime.now(UTC) - timedelta(minutes=STALE_AFTER_MINUTES - 1)
    scheduler = FakeScheduler(last_cycle_at=recent)

    result = await health(heartbeat=heartbeat, scheduler=scheduler)

    assert result["status"] == "ok"
