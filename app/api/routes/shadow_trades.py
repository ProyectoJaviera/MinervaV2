"""Sombras recientes, de solo lectura (Subfase C del plan pre-Fase 5
aprobado 2026-10-14). NUNCA revela PnL/resultado de una sombra etiquetada
hasta que la medicion junte `MIN_EFFECTIVE_N` conglomerados efectivos por
lado -- el unico punto de paso es
`app.api.serializers.shadow_trade_to_public_dict`. Protegido con
`require_auth` a nivel de router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.api.security import require_auth
from app.api.serializers import shadow_trade_to_public_dict
from app.persistence.database import Database
from app.persistence.repositories import shadow_repo
from app.trading.measurement_status import compute_status_async, is_measurement_gate_open

router = APIRouter(prefix="/api/shadow-trades", dependencies=[Depends(require_auth)])


@router.get("/recent")
async def recent(
    limit: int = Query(default=50, ge=1, le=200), db: Database = Depends(get_db),
) -> list[dict]:
    status = await compute_status_async(db)
    reveal_outcome = is_measurement_gate_open(status)
    trades = await shadow_repo.get_recent(db, limit=limit)
    return [shadow_trade_to_public_dict(t, reveal_outcome=reveal_outcome) for t in trades]
