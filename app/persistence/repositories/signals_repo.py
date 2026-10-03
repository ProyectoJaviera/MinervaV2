"""Repositorio de `signals`/`signals_discarded_by_sl_cap` (Fase 3, subfase
3.3) -- ver `app/trading/signal_generator.py` para el flujo completo."""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database
from app.persistence.models import Side, SignalDiscardedBySLCap, SignalRecord


async def insert_signal(db: Database, signal: SignalRecord) -> int | None:
    """`INSERT OR IGNORE` sobre `UNIQUE(symbol, strategy, timeframe,
    candle_close_time)` -- devuelve el id nuevo, o `None` si la fila ya
    existia (vela ya evaluada en un poll anterior: el llamador debe
    saltarse todo procesamiento posterior para esta senal)."""
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO signals (
            symbol, strategy, is_experimental, timeframe, candle_close_time,
            evaluated_at, signal, price_at_eval, stop_price, take_profit_price,
            trailing_distance, funding_rate_pct, funding_is_approximated,
            sl_margin_loss_pct, indicators_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.symbol, signal.strategy, int(signal.is_experimental), signal.timeframe,
            signal.candle_close_time.isoformat(), signal.evaluated_at.isoformat(), signal.signal,
            signal.price_at_eval, signal.stop_price, signal.take_profit_price,
            signal.trailing_distance, signal.funding_rate_pct,
            int(signal.funding_is_approximated), signal.sl_margin_loss_pct,
            signal.indicators_json, signal.status,
        ),
    )
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid


async def update_status(db: Database, signal_id: int, status: str) -> None:
    await db.execute("UPDATE signals SET status = ? WHERE id = ?", (status, signal_id))


def _row_to_signal(row) -> SignalRecord:
    return SignalRecord(
        id=row["id"],
        symbol=row["symbol"],
        strategy=row["strategy"],
        is_experimental=bool(row["is_experimental"]),
        timeframe=row["timeframe"],
        candle_close_time=datetime.fromisoformat(row["candle_close_time"]),
        evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
        signal=row["signal"],
        price_at_eval=row["price_at_eval"],
        stop_price=row["stop_price"],
        take_profit_price=row["take_profit_price"],
        trailing_distance=row["trailing_distance"],
        funding_rate_pct=row["funding_rate_pct"],
        funding_is_approximated=bool(row["funding_is_approximated"]),
        sl_margin_loss_pct=row["sl_margin_loss_pct"],
        indicators_json=row["indicators_json"],
        status=row["status"],
    )


async def get_signals(
    db: Database,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[SignalRecord]:
    query = "SELECT * FROM signals WHERE 1=1"
    params: list = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = await db.fetch_all(query, tuple(params))
    return [_row_to_signal(r) for r in rows]


async def insert_discarded_by_sl_cap(
    db: Database, discarded: SignalDiscardedBySLCap
) -> None:
    await db.execute(
        """
        INSERT INTO signals_discarded_by_sl_cap (
            signal_id, symbol, strategy, timeframe, candle_close_time, side,
            sl_margin_loss_pct, cap_pct, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            discarded.signal_id, discarded.symbol, discarded.strategy, discarded.timeframe,
            discarded.candle_close_time.isoformat(), discarded.side.value,
            discarded.sl_margin_loss_pct, discarded.cap_pct, discarded.created_at.isoformat(),
        ),
    )


def _row_to_discarded(row) -> SignalDiscardedBySLCap:
    return SignalDiscardedBySLCap(
        id=row["id"],
        signal_id=row["signal_id"],
        symbol=row["symbol"],
        strategy=row["strategy"],
        timeframe=row["timeframe"],
        candle_close_time=datetime.fromisoformat(row["candle_close_time"]),
        side=Side(row["side"]),
        sl_margin_loss_pct=row["sl_margin_loss_pct"],
        cap_pct=row["cap_pct"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


async def get_discarded_by_sl_cap(
    db: Database, symbol: str | None = None, limit: int = 100
) -> list[SignalDiscardedBySLCap]:
    if symbol:
        rows = await db.fetch_all(
            "SELECT * FROM signals_discarded_by_sl_cap WHERE symbol = ? "
            "ORDER BY id DESC LIMIT ?",
            (symbol, limit),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM signals_discarded_by_sl_cap ORDER BY id DESC LIMIT ?", (limit,)
        )
    return [_row_to_discarded(r) for r in rows]
