"""Latido periodico del event loop (incidente de estabilidad 2026-10-11):
confirma que el proceso sigue vivo, independiente de cualquier logica de
negocio (`Scheduler`, `PositionMonitor`) -- si deja de avanzar, algo bloqueo
el event loop (ver `app/core/logging.py` para la causa raiz que esto detecta).

Distinto de `Scheduler.last_cycle_at` (ultima evaluacion COMPLETA del
generador de senales): el heartbeat solo prueba que el loop sigue corriendo,
no que el trabajo de negocio avanza -- las dos cosas se exponen por separado
en `GET /health` (`app/api/routes/health.py`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.core.logging import get_dropped_log_count, get_logger

logger = get_logger(__name__)

HEARTBEAT_SECONDS_DEFAULT = 300.0


class Heartbeat:
    def __init__(self, interval_seconds: float = HEARTBEAT_SECONDS_DEFAULT) -> None:
        self.interval_seconds = interval_seconds
        self.last_beat_at: datetime | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def beat(self, now: datetime | None = None) -> datetime:
        self.last_beat_at = now or datetime.now(UTC)
        logger.info(
            "heartbeat: proceso vivo (last_beat_at=%s, log_dropped_count=%d)",
            self.last_beat_at.isoformat(), get_dropped_log_count(),
        )
        return self.last_beat_at

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            self.beat()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
