"""Estado de riesgo para el dashboard (Fase 5): pausa, kill switch, circuit breaker, stop por
drawdown, perdida diaria, cupos de posiciones y rechazos recientes del motor de riesgo. Solo
ESTADOS y conteos de estructura; lo derivado de resultados (racha de perdidas, contador de cruces
del drawdown) va en `numbers`, que es `null` mientras la puerta de resultados este cerrada. Los
detalles de los rechazos nunca salen (pueden traer el equity). Solo lectura; protegido con
`require_auth` a nivel de router."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, get_scheduler_or_none, get_settings
from app.api.routes.health import STALE_AFTER_MINUTES
from app.api.security import require_auth
from app.api.serializers import rejection_to_dict
from app.config import Settings
from app.core.scheduler import Scheduler
from app.llm.budget import local_day_start_utc
from app.persistence.database import Database
from app.persistence.models import Side
from app.persistence.repositories import risk_rejections_repo, trades_repo
from app.trading import control_state, risk_engine
from app.trading.reveal import HIDDEN_RESULTS, reveal_allowed

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.get("/risk")
async def risk(
    limit: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    scheduler: Scheduler | None = Depends(get_scheduler_or_none),
) -> dict:
    """`GET /api/risk`: estado de riesgo, cupos y rechazos recientes.

    Los resultados van detras de la puerta. Solo estados y estructura; la racha de
    perdidas y el contador de cruces del drawdown van en `numbers`, que es `null` con la
    puerta cerrada. Los detalles de los rechazos nunca salen (pueden traer el equity).

    Args:
        limit: cuantos rechazos devolver (1-100).
        db: base (inyectada).
        settings: configuracion (inyectada).
        scheduler: scheduler (inyectado), o `None`.

    Returns:
        Diccionario con pausa, kill switch, circuit breaker, stop por drawdown, perdida
        diaria, cupos, scheduler y rechazos.

    Efectos:
        Lee `system_state`, `trades` y `risk_rejections`, ademas de la puerta de resultados.
    """
    reveal = await reveal_allowed(db)
    now = datetime.now(UTC)
    paused = await control_state.is_scheduler_paused(db)
    kill = await risk_engine.is_kill_switch_active(db)
    breaker_until = await risk_engine.get_active_circuit_breaker_until(db)
    drawdown_active = await risk_engine.is_drawdown_stop_active(db)
    daily_locked = await risk_engine.is_daily_loss_limit_locked(db)
    open_positions = await trades_repo.get_open_positions(db)

    blocked_by = []
    if paused:
        blocked_by.append("SCHEDULER_PAUSED")
    if kill:
        blocked_by.append("KILL_SWITCH_ACTIVE")
    if drawdown_active and settings.drawdown_stop_mode == "duro":
        blocked_by.append("DRAWDOWN_STOP_ACTIVE")
    if breaker_until is not None:
        blocked_by.append("CIRCUIT_BREAKER_ACTIVE")
    if daily_locked:
        blocked_by.append("DAILY_LOSS_LIMIT")

    last_cycle = scheduler.last_cycle_at if scheduler is not None else None
    stale = last_cycle is None or now - last_cycle > timedelta(minutes=STALE_AFTER_MINUTES)
    numbers = None
    if reveal:  # derivados de resultados: solo con la puerta abierta
        numbers = {
            "consecutive_losses": await risk_engine.get_consecutive_losses(db),
            "drawdown_trigger_count": await risk_engine.get_drawdown_stop_trigger_count(db),
        }
    since = local_day_start_utc(settings.report_timezone, now)
    return {
        "reveal_allowed": reveal,
        "message": None if reveal else f"Racha de perdidas y cruces de drawdown {HIDDEN_RESULTS}.",
        "paused": paused,
        "kill_switch_active": kill,
        "measurement_stopped": paused,
        "entries_blocked_by": blocked_by,
        "circuit_breaker": {
            "active": breaker_until is not None,
            "until": breaker_until.isoformat() if breaker_until else None,
            "consecutive_losses_threshold": settings.consecutive_losses_circuit_breaker,
            "cooldown_hours": settings.circuit_breaker_cooldown_hours,
        },
        "drawdown_stop": {
            "active": drawdown_active,
            "mode": settings.drawdown_stop_mode,
            "max_drawdown_fraction": settings.max_drawdown_pct,
        },
        "daily_loss": {
            "locked": daily_locked,
            "max_daily_loss_fraction": settings.max_daily_loss_pct,
        },
        "positions": {
            "open": len(open_positions),
            "max": settings.max_simultaneous_positions,
            "long": sum(1 for p in open_positions if p.side == Side.LONG),
            "short": sum(1 for p in open_positions if p.side == Side.SHORT),
            "max_same_direction": settings.max_same_direction_positions,
        },
        "scheduler": {
            "last_cycle_at": last_cycle.isoformat() if last_cycle else None,
            "stale": stale,
            "stale_after_minutes": STALE_AFTER_MINUTES,
        },
        "numbers": numbers,
        "rejections": [
            rejection_to_dict(r) for r in await risk_rejections_repo.get_rejections(db, limit=limit)
        ],
        "rejections_today_by_reason": await risk_rejections_repo.count_by_reason_since(db, since),
    }
