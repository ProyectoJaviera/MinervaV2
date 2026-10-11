"""`GET /health` (incidente de estabilidad 2026-10-11): expone dos senales
DISTINTAS para detectar un cuelgue rapido --

- `last_beat_at`: el event loop sigue vivo (`app/core/heartbeat.py`, cada
  `settings.heartbeat_seconds`).
- `last_cycle_at`: la ULTIMA evaluacion completa del generador de senales
  (`Scheduler.last_cycle_at`, no el heartbeat -- el loop puede estar vivo y
  el ciclo de negocio seguir atascado, p.ej. con la API de Bitunix caida).

`status` es "stale" si `last_cycle_at` es `None` o tiene mas de
`STALE_AFTER_MINUTES` minutos; ese umbral es una constante del modulo (bien
por encima de `scheduler_poll_seconds`, que por defecto corre cada 60 s)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends

from app.api.deps import get_heartbeat, get_scheduler
from app.core.heartbeat import Heartbeat
from app.core.logging import get_dropped_log_count
from app.core.scheduler import Scheduler

router = APIRouter()

STALE_AFTER_MINUTES = 90


@router.get("/health")
async def health(
    heartbeat: Heartbeat = Depends(get_heartbeat),
    scheduler: Scheduler = Depends(get_scheduler),
) -> dict:
    last_cycle_at = scheduler.last_cycle_at
    stale = (
        last_cycle_at is None
        or datetime.now(UTC) - last_cycle_at > timedelta(minutes=STALE_AFTER_MINUTES)
    )
    return {
        "status": "stale" if stale else "ok",
        "mode": "PAPER_TRADING",
        "last_beat_at": heartbeat.last_beat_at.isoformat() if heartbeat.last_beat_at else None,
        "last_cycle_at": last_cycle_at.isoformat() if last_cycle_at else None,
        "log_dropped_count": get_dropped_log_count(),
    }
