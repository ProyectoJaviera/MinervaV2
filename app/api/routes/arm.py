"""Registro del brazo ejecutado de la cuenta paper para el dashboard (Fase 5): cada decision
APROBADA que el brazo considero, abierta u omitida, con su motivo consultable (tabla
`paper_execution_log`, subfase 3.9). Nunca trae PnL; los detalles de cada fila pasan por una lista
blanca de claves (`app/api/serializers.py::safe_arm_details`) porque algunos rechazos de riesgo
guardan el equity. Solo lectura; protegido con `require_auth` a nivel de router."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, get_paper_arm, get_settings
from app.api.routes.health import NO_PAPER_EXECUTION
from app.api.security import require_auth
from app.api.serializers import arm_entry_to_dict
from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import paper_execution_repo as log_repo
from app.trading.paper_execution import PaperExecutionArm

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

# Que significa cada motivo de omision (sin tildes, como el resto de los textos del proyecto).
REASON_LEGEND = {
    "LATE_DECISION": "La decision del LLM llego despues del retraso maximo permitido.",
    "SCHEDULER_PAUSED": "El scheduler estaba pausado.",
    "POSITION_ALREADY_OPEN": "Ya habia una posicion abierta en ese simbolo.",
    "NO_CONTRACT_SPEC": "No hay especificacion del contrato en la cache.",
    "BELOW_MIN_ORDER": "El tamano no alcanza el minimo operable del contrato con el margen por "
                       "defecto.",
    "LEVELS_INVALID": "Los niveles de SL/TP de la estrategia no eran validos.",
    "OPEN_ERROR": "Error inesperado al abrir (solo se registra el tipo).",
    "ABORTED_RESTART": "El bot se reinicio antes de terminar de abrirla.",
    "RISK_KILL_SWITCH_ACTIVE": "Riesgo: el kill switch estaba activo.",
    "RISK_DRAWDOWN_STOP_ACTIVE": "Riesgo: el stop por drawdown estaba activo.",
    "RISK_CIRCUIT_BREAKER_ACTIVE": "Riesgo: el circuit breaker estaba activo.",
    "RISK_DAILY_LOSS_LIMIT": "Riesgo: se alcanzo el limite de perdida diaria.",
    "RISK_DATA_STALE": "Riesgo: el feed de precios estaba obsoleto.",
    "RISK_STRATEGY_NOT_ELIGIBLE": "Riesgo: la estrategia no esta en la lista de elegibles de la "
                                  "cuenta.",
    "RISK_MAX_SIMULTANEOUS_POSITIONS": "Riesgo: ya estaba el maximo de posiciones abiertas.",
    "RISK_MAX_SAME_DIRECTION_POSITIONS": "Riesgo: ya estaba el maximo de posiciones en la misma "
                                         "direccion.",
    "RISK_SL_CAP_EXCEEDED": "Riesgo: el stop loss planeado supera el tope permitido.",
    "RISK_MARGIN_UNAVAILABLE": "Riesgo: no hay margen disponible para la entrada.",
}


@router.get("/arm")
async def arm(
    limit: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    paper_arm: PaperExecutionArm | None = Depends(get_paper_arm),
) -> dict:
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    totals = await log_repo.count_outcomes_since(db, epoch)
    entries = await log_repo.get_recent(db, limit=limit)
    return {
        "status": await paper_arm.health_block() if paper_arm is not None else NO_PAPER_EXECUTION,
        "config": {
            "margin_usdt": settings.default_margin_usdt,
            "leverage": settings.leverage,
            "max_decision_delay_s": settings.llm_real_max_delay_seconds,
            "eligible_strategies": settings.real_account_eligible_strategies_list,
        },
        "totals": {
            "opened": totals.get(log_repo.OUTCOME_OPENED, 0),
            "skipped": totals.get(log_repo.OUTCOME_SKIPPED, 0),
            "pending": totals.get(log_repo.OUTCOME_PENDING, 0),
        },
        "skipped_by_reason": await log_repo.count_by_reason(db),
        "reason_legend": REASON_LEGEND,
        "entries": [arm_entry_to_dict(e) for e in entries],
    }
