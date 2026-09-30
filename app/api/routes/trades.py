from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.persistence.database import Database
from app.persistence.models import Trade
from app.persistence.repositories import trades_repo

router = APIRouter()


@router.get("/trades", response_model=list[Trade])
async def list_trades(limit: int = 100, db: Database = Depends(get_db)) -> list[Trade]:
    return await trades_repo.get_trades(db, limit=limit)
