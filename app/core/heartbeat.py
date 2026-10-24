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
    """Tarea que escribe en el log "proceso vivo" cada `interval_seconds`.

    Prueba que el event loop sigue girando, sin depender de ninguna logica de negocio. El
    ultimo latido (`last_beat_at`) se expone en `GET /health`.

    Attributes:
        interval_seconds: segundos entre latidos.
        last_beat_at: instante del ultimo latido, o `None` si aun no hubo ninguno.
    """
    def __init__(self, interval_seconds: float = HEARTBEAT_SECONDS_DEFAULT) -> None:
        self.interval_seconds = interval_seconds
        self.last_beat_at: datetime | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        """Pide que `run_forever` termine despues de su espera actual."""
        self._stop.set()

    def beat(self, now: datetime | None = None) -> datetime:
        """Registra un latido: fija `last_beat_at` y lo escribe en el log.

        Junto al instante se escribe el contador de registros de log descartados.

        Args:
            now: instante del latido; por defecto, ahora (UTC). Existe para las pruebas.

        Returns:
            El instante registrado.

        Efectos:
            Escribe una linea INFO en el log (con `log_dropped_count`). No toca la base.
        """
        self.last_beat_at = now or datetime.now(UTC)
        logger.info(
            "heartbeat: proceso vivo (last_beat_at=%s, log_dropped_count=%d)",
            self.last_beat_at.isoformat(), get_dropped_log_count(),
        )
        return self.last_beat_at

    async def run_forever(self) -> None:
        """Bucle de la tarea: un latido ahora y otro cada `interval_seconds` hasta `stop()`.

        Efectos:
            Escribe en el log. Se cancela al apagar (ver `lifespan` en `app/main.py`).
        """
        while not self._stop.is_set():
            self.beat()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
