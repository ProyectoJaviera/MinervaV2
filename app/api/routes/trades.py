"""`GET /trades` (sin login, preexistente). Desde la subfase 3.9 las operaciones de la cuenta
paper incluyen las del brazo ejecutado (senales APROBADA): sus resultados (PnL, costos,
motivo de cierre) solo se ven con `is_outcome_reveal_allowed`, igual que los de las sombras --
ver `app/api/serializers.py`."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.serializers import trade_to_public_dict
from app.persistence.database import Database
from app.persistence.repositories import trades_repo
from app.trading.measurement_status import compute_status_async, is_outcome_reveal_allowed

router = APIRouter()


@router.get("/trades")
async def list_trades(limit: int = 100, db: Database = Depends(get_db)) -> list[dict]:
    """`GET /trades` (sin login): operaciones de la cuenta paper.

    Los resultados solo salen con la puerta abierta.

    Args:
        limit: cuantas devolver.
        db: base (inyectada).

    Returns:
        Lista de operaciones por lista blanca.

    Efectos:
        Lee `trades` y la puerta de resultados.
    """
    reveal = is_outcome_reveal_allowed(await compute_status_async(db))
    trades = await trades_repo.get_trades(db, limit=limit)
    return [trade_to_public_dict(t, reveal_outcome=reveal) for t in trades]
