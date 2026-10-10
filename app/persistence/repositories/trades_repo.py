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
        sl_price=row["sl_price"],
        tp_price=row["tp_price"],
        trailing_distance=row["trailing_distance"],
        effective_stop=row["effective_stop"],
        best_price=row["best_price"],
        liq_price=row["liq_price"],
        sl_margin_loss_pct=row["sl_margin_loss_pct"],
        funding_is_approximated=bool(row["funding_is_approximated"]),
        funding_last_applied_ms=row["funding_last_applied_ms"],
        decision_source=row["decision_source"],
        slippage_entry_usdt=row["slippage_entry_usdt"],
        slippage_exit_usdt=row["slippage_exit_usdt"],
        fill_source=row["fill_source"],
        reconciliation_failed=bool(row["reconciliation_failed"]),
        reconciliation_attempts=row["reconciliation_attempts"],
        reconciliation_window_start_ms=row["reconciliation_window_start_ms"],
        reconciliation_first_failed_at_ms=row["reconciliation_first_failed_at_ms"],
    )


async def create_trade(db: Database, trade: Trade) -> Trade:
    cursor = await db.execute(
        """
        INSERT INTO trades (
            symbol, side, strategy, status, leverage, margin_usdt, notional_usdt,
            qty, entry_price, fee_entry_usdt, funding_paid_usdt, opened_at, decision_json,
            sl_price, tp_price, trailing_distance, effective_stop, best_price, liq_price,
            sl_margin_loss_pct, funding_is_approximated, funding_last_applied_ms,
            decision_source, slippage_entry_usdt
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            trade.sl_price,
            trade.tp_price,
            trade.trailing_distance,
            trade.effective_stop,
            trade.best_price,
            trade.liq_price,
            trade.sl_margin_loss_pct,
            int(trade.funding_is_approximated),
            trade.funding_last_applied_ms,
            trade.decision_source,
            trade.slippage_entry_usdt,
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
    slippage_exit_usdt: float = 0.0,
    fill_source: str | None = None,
) -> None:
    closed_at = closed_at or datetime.now(UTC)
    await db.execute(
        """
        UPDATE trades
        SET status = 'CLOSED', exit_price = ?, fee_exit_usdt = ?, pnl_gross_usdt = ?,
            pnl_net_usdt = ?, close_reason = ?, closed_at = ?,
            slippage_exit_usdt = ?, fill_source = ?
        WHERE id = ?
        """,
        (exit_price, fee_exit_usdt, pnl_gross_usdt, pnl_net_usdt, close_reason,
         closed_at.isoformat(), slippage_exit_usdt, fill_source, trade_id),
    )


async def update_risk_state(
    db: Database, trade_id: int, effective_stop: float | None, best_price: float | None
) -> None:
    """Trailing: el mejor precio alcanzado y el stop efectivo que se mueve con
    el. Solo se llama cuando cambian (ver el monitor de posiciones)."""
    await db.execute(
        "UPDATE trades SET effective_stop = ?, best_price = ? WHERE id = ?",
        (effective_stop, best_price, trade_id),
    )


async def update_funding(
    db: Database,
    trade_id: int,
    funding_paid_usdt: float,
    funding_last_applied_ms: int,
    funding_is_approximated: bool,
) -> None:
    await db.execute(
        """
        UPDATE trades
        SET funding_paid_usdt = ?, funding_last_applied_ms = ?, funding_is_approximated = ?
        WHERE id = ?
        """,
        (funding_paid_usdt, funding_last_applied_ms, int(funding_is_approximated), trade_id),
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


async def update_reconciliation_state(
    db: Database, trade_id: int, failed: bool, attempts: int,
    window_start_ms: int | None = None, first_failed_at_ms: int | None = None,
) -> None:
    """Incidente de estabilidad 2026-10-10, Etapa 2: marca si la ultima
    reconciliacion fallo y cuantos intentos van. Las posiciones REALES siguen
    vigiladas en vivo igual con el flag puesto -- solo cambia el
    `fill_source` de un cierre por tick (ver `_close` en
    `app/trading/position_monitor.py`). `window_start_ms`/`first_failed_at_ms`
    (revision de Etapa 2, correcciones 1 y 2) se fijan solo en la primera
    falla de la racha -- el llamador decide cuando preservarlos."""
    await db.execute(
        "UPDATE trades SET reconciliation_failed = ?, reconciliation_attempts = ?, "
        "reconciliation_window_start_ms = ?, reconciliation_first_failed_at_ms = ? WHERE id = ?",
        (int(failed), attempts, window_start_ms, first_failed_at_ms, trade_id),
    )


async def get_open_positions_as_of(db: Database, as_of: datetime) -> list[Trade]:
    """Posiciones REALES (tabla `trades`, nunca `shadow_trades`) abiertas en el
    instante `as_of`: se abrieron antes o en ese instante, y siguen abiertas o
    se cerraron despues (subfase 3.6, fase iv -- features del prompt, seccion
    a punto 7). Sirve para una decision historica (piloto/smoke sobre datos ya
    guardados), no solo para "ahora": por eso no reusa `get_open_positions`,
    que solo ve `status = 'OPEN'` en el instante de la consulta."""
    rows = await db.fetch_all(
        "SELECT * FROM trades WHERE opened_at <= ? AND (closed_at IS NULL OR closed_at > ?) "
        "ORDER BY opened_at DESC",
        (as_of.isoformat(), as_of.isoformat()),
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


async def update_liq_price(db: Database, trade_id: int, liq_price: float) -> None:
    """Completa la liquidacion de posiciones abiertas antes de la subfase 3.4
    (sin este dato el monitor no podria vigilarlas)."""
    await db.execute("UPDATE trades SET liq_price = ? WHERE id = ?", (liq_price, trade_id))
