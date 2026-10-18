"""Cuenta paper para el dashboard (Fase 5): posiciones abiertas y cerradas con simbolo, lado,
estrategia, niveles y horas. **Sin PnL, precio de salida ni motivo de cierre mientras
`is_outcome_reveal_allowed` sea falso** (la puerta falla cerrada, ver `app/trading/reveal.py`): esos
campos ni siquiera viajan en el JSON, y la consulta que lee el PnL ni se ejecuta. Lista BLANCA en
`app/api/serializers.py::account_trade_to_dict`. Solo lectura; protegido con `require_auth` a nivel
de router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db, get_monitor, get_paper_backend, get_settings
from app.api.security import require_auth
from app.api.serializers import account_trade_to_dict
from app.config import Settings
from app.persistence.database import Database
from app.persistence.models import Trade
from app.persistence.repositories import trades_repo
from app.trading.reveal import HIDDEN_RESULTS, reveal_allowed

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


async def results_summary(
    db: Database, settings: Settings, paper_backend, marks: dict[str, float],
    open_positions: list[Trade],
) -> dict:
    """Saldo, PnL realizado y flotante, equity y rendimiento de las operaciones cerradas. **Solo
    se llama con la puerta abierta**: lee `pnl_net_usdt`."""
    row = await db.fetch_one(
        "SELECT COUNT(*) AS closed, COALESCE(SUM(pnl_net_usdt), 0) AS realized, "
        "COALESCE(SUM(CASE WHEN pnl_net_usdt > 0 THEN 1 ELSE 0 END), 0) AS wins, "
        "COALESCE(SUM(CASE WHEN pnl_net_usdt < 0 THEN 1 ELSE 0 END), 0) AS losses, "
        "COALESCE(SUM(CASE WHEN pnl_net_usdt > 0 THEN pnl_net_usdt END), 0) AS gross_win, "
        "COALESCE(SUM(CASE WHEN pnl_net_usdt < 0 THEN -pnl_net_usdt END), 0) AS gross_loss "
        "FROM trades WHERE status = 'CLOSED'"
    )
    realized = float(row["realized"])
    balance = settings.initial_capital_usdt + realized
    unrealized: float | None = None
    missing: list[str] = []
    if paper_backend is not None:
        unrealized = 0.0
        for position in open_positions:
            if position.symbol in marks:
                unrealized += paper_backend.unrealized_pnl_usdt(position, marks[position.symbol])
            else:
                missing.append(position.symbol)
    gross_loss = float(row["gross_loss"])
    return {
        "balance_usdt": balance,
        "realized_pnl_usdt": realized,
        "unrealized_pnl_usdt": unrealized,
        "equity_usdt": None if unrealized is None else balance + unrealized,
        "marks_missing": missing,
        "closed": int(row["closed"]),
        "wins": int(row["wins"]),
        "losses": int(row["losses"]),
        "profit_factor": float(row["gross_win"]) / gross_loss if gross_loss > 0 else None,
    }


@router.get("/account")
async def account(
    limit: int = Query(default=100, ge=1, le=500),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings),
    paper_backend=Depends(get_paper_backend),
    monitor=Depends(get_monitor),
) -> dict:
    reveal = await reveal_allowed(db)
    open_positions = await trades_repo.get_open_positions(db)
    closed = await trades_repo.get_closed_trades(db, limit=limit)
    results = None
    if reveal:  # estructural: con la puerta cerrada esta consulta no se ejecuta
        marks = dict(getattr(monitor, "marks", {}) or {})
        results = await results_summary(db, settings, paper_backend, marks, open_positions)
    return {
        "reveal_allowed": reveal,
        "message": None if reveal else f"Resultados de la cuenta paper {HIDDEN_RESULTS}.",
        "initial_capital_usdt": settings.initial_capital_usdt,
        "summary": {
            "open": len(open_positions),
            "closed": await trades_repo.count_closed(db),
            "max_simultaneous_positions": settings.max_simultaneous_positions,
            "margin_in_use_usdt": sum(p.margin_usdt for p in open_positions),
        },
        "open": [account_trade_to_dict(t, reveal_outcome=reveal) for t in open_positions],
        "closed": [account_trade_to_dict(t, reveal_outcome=reveal) for t in closed],
        "results": results,
    }
