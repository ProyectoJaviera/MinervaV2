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
    """Inicio (00:00) del dia local de `now` en `tz_name`, devuelto en UTC.

    Es el mismo limite de "dia" que usa la perdida diaria (`risk_engine.today_key`), pero
    como instante, para filtrar `llm_logs.created_at`.

    Args:
        tz_name: zona horaria (`REPORT_TIMEZONE`).
        now: instante de referencia.

    Returns:
        La medianoche local convertida a UTC.
    """
    local_now = now.astimezone(ZoneInfo(tz_name))
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(UTC)


class LlmBudgetTracker:
    """Reserva el costo maximo de cada llamada bajo un candado, por proceso.

    `try_reserve` y `release` se usan siempre en par (con `try/finally` en el llamador): se
    reserva el costo MAXIMO antes de llamar y se libera al terminar, haya ido bien o mal.
    El costo real, si hubo llamada, ya esta en `llm_logs` y cuenta en el gasto del dia
    siguiente. Sin el candado, varias llamadas simultaneas podrian ver "presupuesto libre"
    a la vez y superar juntas el tope.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._reserved_usd = 0.0

    async def try_reserve(
        self, db: Database, settings: Settings, cost_max_usd: float, now: datetime | None = None
    ) -> bool:
        """Reserva el costo maximo si cabe en el presupuesto diario (`LLM_DAILY_BUDGET_USD`).

        Args:
            db: conexion a la base.
            settings: configuracion (zona horaria y presupuesto).
            cost_max_usd: costo maximo de la llamada, en USD.
            now: instante de referencia; por defecto, ahora (UTC).

        Returns:
            True si se reservo; False si gasto de hoy mas reservado mas este costo superaria el
            tope.

        Efectos:
            Lee `llm_logs`.
        """
        now = now or datetime.now(UTC)
        since = local_day_start_utc(settings.report_timezone, now)
        async with self._lock:
            spent_today = await llm_logs_repo.get_spend_since(db, since)
            if spent_today + self._reserved_usd + cost_max_usd > settings.llm_daily_budget_usd:
                return False
            self._reserved_usd += cost_max_usd
            return True

    async def release(self, cost_max_usd: float) -> None:
        """Libera una reserva hecha con `try_reserve`.

        Args:
            cost_max_usd: el mismo costo que se reservo.
        """
        async with self._lock:
            self._reserved_usd = max(0.0, self._reserved_usd - cost_max_usd)
