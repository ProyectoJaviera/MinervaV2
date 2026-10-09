"""Presupuesto diario del LLM con reserva bajo lock (subfase 3.6, fase i).

Las decisiones corren en paralelo (semaforo en `decision_service.py`), asi que
comprobar y reservar el coste maximo tienen que pasar por una seccion critica:
sin ella, varias llamadas simultaneas podrian ver cada una "presupuesto libre"
antes de que ninguna se registrara, y juntas superar el tope. Ver
docs/FASE3_6_LLM.md, seccion (e).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import llm_logs_repo


def local_day_start_utc(tz_name: str, now: datetime) -> datetime:
    """Inicio (00:00) del dia local de `now` en `tz_name`, devuelto en UTC --
    mismo limite de "dia" que usa la perdida diaria (`app.trading.risk_engine.
    today_key`), pero como instante para poder filtrar `llm_logs.created_at`."""
    local_now = now.astimezone(ZoneInfo(tz_name))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


class LlmBudgetTracker:
    """Reserva en memoria, por proceso. `try_reserve`/`release` se usan siempre
    en par (con `try/finally` en el llamador): se reserva el coste MAXIMO antes
    de llamar a la API y se libera al terminar, haya ido bien o mal -- el coste
    real, si hubo llamada, ya quedo escrito en `llm_logs` y por eso cuenta en el
    siguiente `gasto_hoy`."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved_usd = 0.0

    async def try_reserve(
        self, db: Database, settings: Settings, cost_max_usd: float, now: datetime | None = None
    ) -> bool:
        now = now or datetime.now(UTC)
        since = local_day_start_utc(settings.report_timezone, now)
        async with self._lock:
            spent_today = await llm_logs_repo.get_spend_since(db, since)
            if spent_today + self._reserved_usd + cost_max_usd > settings.llm_daily_budget_usd:
                return False
            self._reserved_usd += cost_max_usd
            return True

    async def release(self, cost_max_usd: float) -> None:
        async with self._lock:
            self._reserved_usd = max(0.0, self._reserved_usd - cost_max_usd)
