"""Operaciones sombra (tabla `shadow_trades`, subfase 3.5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side, TradeStatus


def _row_to_shadow(row) -> ShadowTrade:
    return ShadowTrade(
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
        sl_price=row["sl_price"],
        tp_price=row["tp_price"],
        trailing_distance=row["trailing_distance"],
        effective_stop=row["effective_stop"],
        best_price=row["best_price"],
        liq_price=row["liq_price"],
        sl_margin_loss_pct=row["sl_margin_loss_pct"],
        funding_is_approximated=bool(row["funding_is_approximated"]),
        funding_last_applied_ms=row["funding_last_applied_ms"],
        decision_source="SIN_LLM",
        slippage_entry_usdt=row["slippage_entry_usdt"],
        slippage_exit_usdt=row["slippage_exit_usdt"],
        fill_source=row["fill_source"],
        contributing_strategies=json.loads(row["contributing_strategies"]),
        signal_group_key=row["signal_group_key"],
        candle_close_time=datetime.fromisoformat(row["candle_close_time"]),
        llm_decision=row["llm_decision"],
        executed_in_real_account=bool(row["executed_in_real_account"]),
    )


async def insert_if_new(db: Database, trade: ShadowTrade) -> int | None:
    """`INSERT OR IGNORE` sobre `signal_group_key` UNICA: devuelve el id nuevo o
    `None` si esa senal agrupada ya tenia su operacion sombra."""
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO shadow_trades (
            symbol, side, strategy, contributing_strategies, signal_group_key,
            candle_close_time, status, leverage, margin_usdt, notional_usdt, qty,
            entry_price, fee_entry_usdt, slippage_entry_usdt, opened_at,
            sl_price, tp_price, trailing_distance, effective_stop, best_price, liq_price,
            sl_margin_loss_pct, funding_is_approximated, funding_last_applied_ms,
            llm_decision, executed_in_real_account
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade.symbol, trade.side.value, trade.strategy,
            json.dumps(trade.contributing_strategies), trade.signal_group_key,
            trade.candle_close_time.isoformat(), trade.status.value, trade.leverage,
            trade.margin_usdt, trade.notional_usdt, trade.qty, trade.entry_price,
            trade.fee_entry_usdt, trade.slippage_entry_usdt, trade.opened_at.isoformat(),
            trade.sl_price, trade.tp_price, trade.trailing_distance, trade.effective_stop,
            trade.best_price, trade.liq_price, trade.sl_margin_loss_pct,
            int(trade.funding_is_approximated), trade.funding_last_applied_ms,
            trade.llm_decision, int(trade.executed_in_real_account),
        ),
    )
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid


async def get(db: Database, shadow_id: int) -> ShadowTrade | None:
    row = await db.fetch_one("SELECT * FROM shadow_trades WHERE id = ?", (shadow_id,))
    return _row_to_shadow(row) if row else None


async def get_open(db: Database) -> list[ShadowTrade]:
    rows = await db.fetch_all("SELECT * FROM shadow_trades WHERE status = 'OPEN'")
    return [_row_to_shadow(r) for r in rows]


async def get_closed(db: Database) -> list[ShadowTrade]:
    rows = await db.fetch_all("SELECT * FROM shadow_trades WHERE status = 'CLOSED'")
    return [_row_to_shadow(r) for r in rows]


async def close(
    db: Database, shadow_id: int, exit_price: float, fee_exit_usdt: float,
    slippage_exit_usdt: float, pnl_gross_usdt: float, pnl_net_usdt: float,
    close_reason: str, fill_source: str, closed_at: datetime | None = None,
) -> None:
    await db.execute(
        """
        UPDATE shadow_trades
        SET status = 'CLOSED', exit_price = ?, fee_exit_usdt = ?, slippage_exit_usdt = ?,
            pnl_gross_usdt = ?, pnl_net_usdt = ?, close_reason = ?, fill_source = ?,
            closed_at = ?
        WHERE id = ? AND status = 'OPEN'
        """,
        (exit_price, fee_exit_usdt, slippage_exit_usdt, pnl_gross_usdt, pnl_net_usdt,
         close_reason, fill_source, (closed_at or datetime.now(UTC)).isoformat(), shadow_id),
    )


async def update_risk_state(
    db: Database, shadow_id: int, effective_stop: float | None, best_price: float | None
) -> None:
    await db.execute(
        "UPDATE shadow_trades SET effective_stop = ?, best_price = ? WHERE id = ?",
        (effective_stop, best_price, shadow_id),
    )


async def update_funding(
    db: Database, shadow_id: int, funding_paid_usdt: float, funding_last_applied_ms: int,
) -> None:
    await db.execute(
        "UPDATE shadow_trades SET funding_paid_usdt = ?, funding_last_applied_ms = ? WHERE id = ?",
        (funding_paid_usdt, funding_last_applied_ms, shadow_id),
    )


async def mark_executed(db: Database, shadow_id: int) -> None:
    """La cuenta real abrio esta misma senal: queda marcada, pero la sombra sigue
    siendo la referencia de comparacion (no depende de la ejecucion real)."""
    await db.execute(
        "UPDATE shadow_trades SET executed_in_real_account = 1 WHERE id = ?", (shadow_id,)
    )
