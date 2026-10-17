"""Avance de la medicion en vivo, de solo lectura (Subfase C del plan
pre-Fase 5 aprobado 2026-10-14). Reusa `app.trading.measurement_status`
(compartido con `scripts/measurement_status.py`) sin duplicar su logica.
Nunca incluye PnL/resultados -- `MeasurementStatus` no tiene esos campos por
diseno del propio modulo. Protegido con `require_auth` a nivel de router."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.security import require_auth
from app.persistence.database import Database
from app.trading.measurement_status import (
    OFFLINE_GAP_HOURS,
    compute_status_async,
    is_measurement_gate_open,
    is_outcome_reveal_allowed,
)

router = APIRouter(prefix="/api/measurement", dependencies=[Depends(require_auth)])


@router.get("/status")
async def status(db: Database = Depends(get_db)) -> dict:
    s = await compute_status_async(db)
    return {
        "frozen_hash": s.frozen_hash,
        "total_medicion_rows": s.total_medicion_rows,
        "ok_by_label": s.ok_by_label,
        "total_ok": s.total_ok,
        "approval_rate": s.approval_rate,
        "within_band": s.within_band,
        "non_ok_counts": s.non_ok_counts,
        "json_repaired_count": s.json_repaired_count,
        "total_cost_usd": s.total_cost_usd,
        "cost_per_row_usd": s.cost_per_row_usd(),
        "omitted_without_row": s.omitted_without_row,
        "first_medicion_row_at": (
            s.first_medicion_row_at.isoformat() if s.first_medicion_row_at else None
        ),
        "data_now": s.data_now.isoformat() if s.data_now else None,
        "n_eff_approved": s.n_eff_approved,
        "n_eff_rejected": s.n_eff_rejected,
        "shortfall_approved": s.shortfall("APROBADA"),
        "shortfall_rejected": s.shortfall("RECHAZADA"),
        "window_days": s.window_days,
        "rate_approved_per_day": s.rate_approved_per_day,
        "rate_rejected_per_day": s.rate_rejected_per_day,
        "days_needed_approved": s.days_needed("APROBADA"),
        "days_needed_rejected": s.days_needed("RECHAZADA"),
        # Ritmo sobre tiempo EN MARCHA (solo el tiempo evaluando senales; un hueco > 2 h
        # sin ciclos cuenta como apagado). Los anteriores son de CALENDARIO. null = sin
        # marcas de evaluacion. Los porcentajes son fracciones 0-1. Sin PnL.
        "offline_gap_hours": OFFLINE_GAP_HOURS,
        "running_window_days": s.running_window_days,
        "uptime_pct_window": s.uptime_pct_window,
        "uptime_pct_total": s.uptime_pct_total,
        "rate_approved_running_per_day": s.rate_approved_running_per_day,
        "rate_rejected_running_per_day": s.rate_rejected_running_per_day,
        "days_needed_running_approved": s.days_needed_running("APROBADA"),
        "days_needed_running_rejected": s.days_needed_running("RECHAZADA"),
        "gate_open": is_measurement_gate_open(s),
        # Gate abierto Y registro oficial del veredicto: lo unico que habilita mostrar
        # resultados. Con gate_open=true y esto en false el dashboard dice
        # "gate abierto: seguir docs/VEREDICTO_3_6.md".
        "outcome_reveal_allowed": is_outcome_reveal_allowed(s),
    }
