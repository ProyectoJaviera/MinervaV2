"""Operaciones CRUD sobre la tabla `trades`."""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import Side, Trade, TradeStatus


def _row_to_trade(row) -> Trade:
    return Trade(
        id=row["id"],
        symbol=row["symbol"],
        side=Side(row["side"]),
        strategy=row["strategy"],
        status=TradeStatus(row["status"]),
        leverage=row["leverage"],
        margin_usdt=row["margin_usdt"],
        notional_usdt=row["notional_usdt"],
        qty=row["qty"],
        entry_price=row["entry_price"],
        exit_price=row["exit_price"],
        fee_entry_usdt=row["fee_entry_usdt"],
        fee_exit_usdt=row["fee_exit_usdt"],
        funding_paid_usdt=row["funding_paid_usdt"],
        pnl_gross_usdt=row["pnl_gross_usdt"],
        pnl_net_usdt=row["pnl_net_usdt"],
        close_reason=row["close_reason"],
        opened_at=datetime.fromisoformat(row["opened_at"]),
        closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
        decision_json=row["decision_json"],
    )


async def create_trade(db: Database, trade: Trade) -> Trade:
    cursor = await db.execute(
        """
        INSERT INTO trades (
            symbol, side, strategy, status, leverage, margin_usdt, notional_usdt,
            qty, entry_price, fee_entry_usdt, funding_paid_usdt, opened_at, decision_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.symbol,
            trade.side.value,
            trade.strategy,
            trade.status.value,
            trade.leverage,
            trade.margin_usdt,
            trade.notional_usdt,
            trade.qty,
            trade.entry_price,
            trade.fee_entry_usdt,
            trade.funding_paid_usdt,
            trade.opened_at.isoformat(),
            trade.decision_json,
        ),
    )
    trade.id = cursor.lastrowid
    return trade


async def close_trade(
    db: Database,
    trade_id: int,
    exit_price: float,
    fee_exit_usdt: float,
    pnl_gross_usdt: float,
    pnl_net_usdt: float,
    close_reason: str,
    closed_at: datetime | None = None,
) -> None:
    closed_at = closed_at or datetime.now(UTC)
    await db.execute(
        """
        UPDATE trades
        SET status = 'CLOSED', exit_price = ?, fee_exit_usdt = ?, pnl_gross_usdt = ?,
            pnl_net_usdt = ?, close_reason = ?, closed_at = ?
        WHERE id = ?
        """,
        (exit_price, fee_exit_usdt, pnl_gross_usdt, pnl_net_usdt, close_reason,
         closed_at.isoformat(), trade_id),
    )


async def get_open_positions(db: Database, symbol: str | None = None) -> list[Trade]:
    if symbol:
        rows = await db.fetch_all(
            "SELECT * FROM trades WHERE status = 'OPEN' AND symbol = ? ORDER BY opened_at DESC",
            (symbol,),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY opened_at DESC"
        )
    return [_row_to_trade(r) for r in rows]


async def get_trades(db: Database, limit: int = 100) -> list[Trade]:
    rows = await db.fetch_all(
        "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
    )
    return [_row_to_trade(r) for r in rows]


async def get_trade(db: Database, trade_id: int) -> Trade | None:
    row = await db.fetch_one("SELECT * FROM trades WHERE id = ?", (trade_id,))
    return _row_to_trade(row) if row else None


async def committed_margin(db: Database, symbol: str | None = None) -> float:
    """Suma del margen de todas las posiciones abiertas (opcionalmente de un simbolo).

    Usado por la formula de sizing de riesgo (ver config.max_margin_for_new_trade).
    """
    if symbol:
        row = await db.fetch_one(
            "SELECT COALESCE(SUM(margin_usdt), 0) AS total FROM trades "
            "WHERE status = 'OPEN' AND symbol = ?",
            (symbol,),
        )
    else:
        row = await db.fetch_one(
            "SELECT COALESCE(SUM(margin_usdt), 0) AS total FROM trades WHERE status = 'OPEN'"
        )
    return float(row["total"]) if row else 0.0
