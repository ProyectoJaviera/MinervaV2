"""`GET /positions` (sin login, preexistente): posiciones abiertas de la cuenta paper. Sin el
registro oficial del veredicto no trae PnL, costos ni trailing de las operaciones (subfase 3.9,
ver `app/api/serializers.py`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.api.serializers import trade_to_public_dict
from app.persistence.database import Database
from app.persistence.repositories import trades_repo
from app.trading.measurement_status import compute_status_async, is_outcome_reveal_allowed

router = APIRouter()


@router.get("/positions")
async def list_positions(db: Database = Depends(get_db)) -> list[dict]:
    """`GET /positions` (sin login): posiciones abiertas de la cuenta paper.

    Sin el registro oficial del veredicto no trae PnL, costos ni trailing.

    Args:
        db: base (inyectada).

    Returns:
        Lista de posiciones por lista blanca.

    Efectos:
        Lee `trades` y la puerta de resultados.
    """
    reveal = is_outcome_reveal_allowed(await compute_status_async(db))
    positions = await trades_repo.get_open_positions(db)
    return [trade_to_public_dict(t, reveal_outcome=reveal) for t in positions]
