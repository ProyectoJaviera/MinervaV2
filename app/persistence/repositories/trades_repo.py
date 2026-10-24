"""Operaciones de lectura y escritura sobre la tabla `trades` (la cuenta paper).

Lo usan `PaperBackend` (abrir y cerrar), el monitor de posiciones (trailing, funding,
reconciliacion), el motor de riesgo (margen comprometido) y la API. Cada funcion que
escribe hace su propio commit a traves de `Database.execute`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import Side, Trade, TradeStatus


def row_to_trade(row) -> Trade:
    """Convierte una fila de `trades` en un `Trade`.

    Args:
        row: fila `aiosqlite.Row` con TODAS las columnas de `trades` (`SELECT *`).

    Returns:
        El `Trade`, con las fechas como `datetime` y los enteros 0/1 como `bool`.
    """
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
    """Inserta una operacion nueva (abierta) y le asigna su `id`.

    Args:
        db: conexion a la base.
        trade: la operacion; se insertan sus niveles de riesgo, comision y slippage de entrada.

    Returns:
        El mismo `Trade`, ahora con `id`.

    Efectos:
        Escribe `trades` (INSERT). Sin red.
    """
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
    """Cierra una operacion: la pasa a `CLOSED` y fija salida, comision, PnL y motivo.

    Args:
        db: conexion a la base.
        trade_id: `trades.id` de la posicion.
        exit_price: precio de salida.
        fee_exit_usdt: comision de salida en USDT.
        pnl_gross_usdt: PnL bruto en USDT.
        pnl_net_usdt: PnL neto en USDT (despues de comisiones, slippage y funding).
        close_reason: motivo del cierre, texto libre fijado por quien cierra (el monitor
            usa p. ej. `SL`, `TP` o `LIQUIDATION`).
        closed_at: hora de cierre; por defecto, ahora (UTC).
        slippage_exit_usdt: costo de slippage de la salida.
        fill_source: como se lleno el cierre (`TICK`, `CANDLE_RECON`, `REST_MARK` o
            `TICK_UNRECONCILED`).

    Efectos:
        Escribe `trades` (UPDATE por `id`). No comprueba que siguiera abierta: el llamador
        es quien garantiza el cierre unico.
    """
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
    """Guarda el estado del trailing: el mejor precio alcanzado y el stop efectivo que lo sigue.

    Solo se llama cuando cambian (ver el monitor de posiciones).

    Args:
        db: conexion a la base.
        trade_id: `trades.id` de la posicion.
        effective_stop: stop vigente tras el trailing, o `None`.
        best_price: mejor precio visto desde la apertura, o `None`.

    Efectos:
        Escribe `trades` (UPDATE de `effective_stop` y `best_price`).
    """
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
    """Guarda el funding acumulado y hasta que momento se aplico, para no contarlo dos veces.

    Args:
        db: conexion a la base.
        trade_id: `trades.id` de la posicion.
        funding_paid_usdt: funding acumulado total en USDT (positivo = pagado).
        funding_last_applied_ms: ultimo evento de funding aplicado, en ms Unix.
        funding_is_approximated: True si algun evento se estimo en vez de leerse del historico.

    Efectos:
        Escribe `trades` (UPDATE de las tres columnas de funding).
    """
    await db.execute(
        """
        UPDATE trades
        SET funding_paid_usdt = ?, funding_last_applied_ms = ?, funding_is_approximated = ?
        WHERE id = ?
        """,
        (funding_paid_usdt, funding_last_applied_ms, int(funding_is_approximated), trade_id),
    )


async def get_open_positions(db: Database, symbol: str | None = None) -> list[Trade]:
    """Las posiciones abiertas de la cuenta paper, la mas reciente primero.

    Args:
        db: conexion a la base.
        symbol: si se da, solo las de ese simbolo.

    Returns:
        Lista de `Trade` con `status = 'OPEN'`.

    Efectos:
        Lee `trades`.
    """
    if symbol:
        rows = await db.fetch_all(
            "SELECT * FROM trades WHERE status = 'OPEN' AND symbol = ? ORDER BY opened_at DESC",
            (symbol,),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY opened_at DESC"
        )
    return [row_to_trade(r) for r in rows]


async def update_reconciliation_state(
    db: Database, trade_id: int, failed: bool, attempts: int,
    window_start_ms: int | None = None, first_failed_at_ms: int | None = None,
) -> None:
    """Marca si la ultima reconciliacion de la posicion fallo y cuantos intentos van.

    Incidente de estabilidad 2026-10-10, Etapa 2. Las posiciones de la cuenta paper siguen
    vigiladas en vivo con el flag puesto; solo cambia el `fill_source` de un cierre por tick
    (ver `_close` en `app/trading/position_monitor.py`). `window_start_ms` y
    `first_failed_at_ms` se fijan solo en la primera falla de la racha: el llamador decide
    cuando preservarlos.

    Args:
        db: conexion a la base.
        trade_id: `trades.id` de la posicion.
        failed: True si la reconciliacion fallo.
        attempts: cantidad de intentos acumulados.
        window_start_ms: desde donde debe seguir reconciliando cada reintento.
        first_failed_at_ms: cuando empezo la racha de fallos.

    Efectos:
        Escribe `trades` (UPDATE de las cuatro columnas `reconciliation_*`).
    """
    await db.execute(
        "UPDATE trades SET reconciliation_failed = ?, reconciliation_attempts = ?, "
        "reconciliation_window_start_ms = ?, reconciliation_first_failed_at_ms = ? WHERE id = ?",
        (int(failed), attempts, window_start_ms, first_failed_at_ms, trade_id),
    )


async def get_open_positions_as_of(db: Database, as_of: datetime) -> list[Trade]:
    """Posiciones de la cuenta paper abiertas en el instante `as_of`.

    Se abrieron antes o en ese instante y siguen abiertas o se cerraron despues. Lee solo
    `trades`, nunca `shadow_trades`. Sirve para una decision historica (piloto o smoke
    sobre datos ya guardados), no solo para "ahora": por eso no reutiliza
    `get_open_positions`, que solo ve `status = 'OPEN'` en el instante de la consulta.

    Args:
        db: conexion a la base.
        as_of: instante de referencia.

    Returns:
        Lista de `Trade`, la mas reciente primero.

    Efectos:
        Lee `trades`.
    """
    rows = await db.fetch_all(
        "SELECT * FROM trades WHERE opened_at <= ? AND (closed_at IS NULL OR closed_at > ?) "
        "ORDER BY opened_at DESC",
        (as_of.isoformat(), as_of.isoformat()),
    )
    return [row_to_trade(r) for r in rows]


async def get_trades(db: Database, limit: int = 100) -> list[Trade]:
    """Las operaciones mas recientes (abiertas y cerradas), por fecha de apertura descendente.

    Args:
        db: conexion a la base.
        limit: maximo de filas.

    Returns:
        Lista de `Trade`.

    Efectos:
        Lee `trades`.
    """
    rows = await db.fetch_all(
        "SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,)
    )
    return [row_to_trade(r) for r in rows]


async def get_closed_trades(db: Database, limit: int = 100) -> list[Trade]:
    """Las operaciones CERRADAS mas recientes primero (por hora de cierre).

    Args:
        db: conexion a la base.
        limit: maximo de filas.

    Returns:
        Lista de `Trade` con `status = 'CLOSED'`.

    Efectos:
        Lee `trades` (incluye columnas de PnL: quien la llame hacia fuera debe respetar
        `is_outcome_reveal_allowed`).
    """
    rows = await db.fetch_all(
        "SELECT * FROM trades WHERE status = 'CLOSED' "
        "ORDER BY COALESCE(closed_at, opened_at) DESC, id DESC LIMIT ?",
        (limit,),
    )
    return [row_to_trade(r) for r in rows]


async def count_closed(db: Database) -> int:
    """Cuantas operaciones cerradas hay (un conteo: no lee PnL).

    Args:
        db: conexion a la base.

    Returns:
        El numero de filas con `status = 'CLOSED'`.

    Efectos:
        Lee `trades`.
    """
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM trades WHERE status = 'CLOSED'")
    return int(row["n"])


async def get_trade(db: Database, trade_id: int) -> Trade | None:
    """Una operacion por su id.

    Args:
        db: conexion a la base.
        trade_id: `trades.id`.

    Returns:
        El `Trade`, o `None` si no existe.

    Efectos:
        Lee `trades`.
    """
    row = await db.fetch_one("SELECT * FROM trades WHERE id = ?", (trade_id,))
    return row_to_trade(row) if row else None


async def committed_margin(db: Database, symbol: str | None = None) -> float:
    """Suma del margen de las posiciones abiertas (opcionalmente de un simbolo).

    Lo usa la formula de dimensionamiento de riesgo (`Settings.max_margin_for_new_trade`).

    Args:
        db: conexion a la base.
        symbol: si se da, solo las posiciones de ese simbolo.

    Returns:
        Margen comprometido en USDT (0.0 si no hay posiciones).

    Efectos:
        Lee `trades`.
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
    """Completa el precio de liquidacion de una posicion abierta antes de la subfase 3.4.

    Sin ese dato el monitor no podria vigilarla.

    Args:
        db: conexion a la base.
        trade_id: `trades.id` de la posicion.
        liq_price: precio de liquidacion calculado.

    Efectos:
        Escribe `trades` (UPDATE de `liq_price`).
    """
    await db.execute("UPDATE trades SET liq_price = ? WHERE id = ?", (liq_price, trade_id))
