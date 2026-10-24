"""Repositorio de `signals` y `signals_discarded_by_sl_cap` (Fase 3, subfase 3.3).

Cada evaluacion de una estrategia sobre un simbolo, timeframe y vela cerrada es una fila
de `signals` (HOLD incluido: registro auditable completo). Ver
`app/trading/signal_generator.py` para el flujo completo.
"""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database
from app.persistence.models import Side, SignalDiscardedBySLCap, SignalRecord


async def insert_signal(db: Database, signal: SignalRecord) -> int | None:
    """Inserta una evaluacion de estrategia, salvo que esa vela ya se haya evaluado.

    Usa `INSERT OR IGNORE` sobre `UNIQUE(symbol, strategy, timeframe, candle_close_time)`.

    Args:
        db: conexion a la base.
        signal: la evaluacion (LONG, SHORT o HOLD) con su precio y niveles.

    Returns:
        El id nuevo, o `None` si la fila ya existia (vela evaluada en un poll anterior: el
        llamador debe saltarse todo procesamiento posterior de esta senal).

    Efectos:
        Escribe `signals` (INSERT OR IGNORE).
    """
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO signals (
            symbol, strategy, is_experimental, timeframe, candle_close_time,
            evaluated_at, signal, price_at_eval, stop_price, take_profit_price,
            trailing_distance, funding_rate_pct, funding_is_approximated,
            sl_margin_loss_pct, indicators_json, status, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal.symbol, signal.strategy, int(signal.is_experimental), signal.timeframe,
            signal.candle_close_time.isoformat(), signal.evaluated_at.isoformat(), signal.signal,
            signal.price_at_eval, signal.stop_price, signal.take_profit_price,
            signal.trailing_distance, signal.funding_rate_pct,
            int(signal.funding_is_approximated), signal.sl_margin_loss_pct,
            signal.indicators_json, signal.status, signal.reason,
        ),
    )
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid


async def update_status(db: Database, signal_id: int, status: str) -> None:
    """Cambia el estado de una senal (`PENDING`, `DISCARDED_SL_CAP`, ...).

    Args:
        db: conexion a la base.
        signal_id: `signals.id`.
        status: nuevo estado.

    Efectos:
        Escribe `signals` (UPDATE de `status`).
    """
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
        reason=row["reason"],
    )


async def get_by_symbol_strategy_candle(
    db: Database, symbol: str, strategy: str, candle_close_time: datetime
) -> SignalRecord | None:
    """La fila de `signals` de una estrategia para una vela.

    Es la fuente de `indicators_json` para el prompt del LLM (subfase 3.6, fase iii): son
    datos ya calculados por `precompute`, no se recalculan aqui. La UNIQUE real incluye
    `timeframe`; como una estrategia como `donchian_breakout_20` corre en mas de uno, sin
    `timeframe` se toma la mas reciente (simplificacion aceptable para el piloto, documentada).

    Args:
        db: conexion a la base.
        symbol: simbolo, p. ej. `BTCUSDT`.
        strategy: nombre de la estrategia.
        candle_close_time: cierre de la vela evaluada.

    Returns:
        El `SignalRecord`, o `None` si no hay.

    Efectos:
        Lee `signals`.
    """
    row = await db.fetch_one(
        "SELECT * FROM signals WHERE symbol = ? AND strategy = ? AND candle_close_time = ? "
        "ORDER BY id DESC LIMIT 1",
        (symbol, strategy, candle_close_time.isoformat()),
    )
    return _row_to_signal(row) if row else None


async def get_signals(
    db: Database,
    symbol: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[SignalRecord]:
    """Evaluaciones recientes, con filtros opcionales.

    Args:
        db: conexion a la base.
        symbol: si se da, solo ese simbolo.
        status: si se da, solo ese estado.
        limit: maximo de filas (las de `id` mas alto primero).

    Returns:
        Lista de `SignalRecord`.

    Efectos:
        Lee `signals`.
    """
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
    """Registra una senal descartada porque su SL perderia mas del tope sobre el margen.

    Args:
        db: conexion a la base.
        discarded: el descarte, con el `sl_margin_loss_pct` y el tope aplicado.

    Efectos:
        Escribe `signals_discarded_by_sl_cap` (INSERT).
    """
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
    """Los descartes por tope de SL mas recientes.

    Args:
        db: conexion a la base.
        symbol: si se da, solo ese simbolo.
        limit: maximo de filas.

    Returns:
        Lista de `SignalDiscardedBySLCap`, el de `id` mas alto primero.

    Efectos:
        Lee `signals_discarded_by_sl_cap`.
    """
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
