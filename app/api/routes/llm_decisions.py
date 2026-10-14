"""Decisiones recientes del LLM con su razonamiento, de solo lectura
(Subfase C del plan pre-Fase 5 aprobado 2026-10-14). Nunca devuelve el
`prompt` completo ni el `response_raw` crudo (pesado, redundante una vez
parseado). Sin regla de velado: la decision APROBADA/RECHAZADA + su
razonamiento no es un "resultado de la operacion" bajo la regla del usuario
(esa es especificamente sobre PnL/cierre de sombras etiquetadas, ver
`app/api/serializers.py`). Protegido con `require_auth` a nivel de router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.api.security import require_auth
from app.llm.schemas import parse_decision
from app.persistence.database import Database
from app.persistence.repositories import llm_logs_repo, shadow_repo

router = APIRouter(prefix="/api/llm", dependencies=[Depends(require_auth)])


@router.get("/decisions/recent")
async def recent_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    fase: str | None = None,
    db: Database = Depends(get_db),
) -> list[dict]:
    rows = await llm_logs_repo.get_recent(db, limit=limit, fase=fase)
    result = []
    for row in rows:
        shadow = (
            await shadow_repo.get(db, row["shadow_trade_id"])
            if row["shadow_trade_id"] else None
        )
        confianza = None
        razonamiento = None
        if row["status"] == "OK" and row["response_raw"]:
            # Ya se valido una vez al insertar la fila (solo llega a status
            # OK si parse_decision ya tuvo exito) -- volver a parsear el
            # mismo texto es deterministico, no deberia fallar nunca.
            parsed = parse_decision(row["response_raw"])
            confianza = parsed.decision.confianza
            razonamiento = parsed.decision.razonamiento
        result.append({
            "signal_group_key": row["signal_group_key"],
            "fase": row["fase"],
            "status": row["status"],
            "decision": row["decision"],
            "confianza": confianza,
            "razonamiento": razonamiento,
            "created_at": row["created_at"],
            "candle_close_time": row["candle_close_time"],
            "decision_delay_s": row["decision_delay_s"],
            "cost_usd": row["cost_usd"],
            "latency_ms": row["latency_ms"],
            "json_repaired": bool(row["json_repaired"]),
            "error": row["error"],
            "symbol": shadow.symbol if shadow else None,
            "side": shadow.side.value if shadow else None,
        })
    return result
