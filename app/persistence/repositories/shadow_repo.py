"""Operaciones sombra (tabla `shadow_trades`, subfase 3.5).

Una sombra es la operacion simulada de UNA senal agrupada (simbolo, direccion, vela) con
margen ilimitado; su etiqueta `llm_decision` (`SIN_LLM` al nacer, luego `APROBADA` o
`RECHAZADA`) es inmutable: la garantizan `set_llm_decision` y el disparador
`llm_decision_inmutable`. Es la tabla de la que sale la medicion del valor del LLM.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side, TradeStatus


def row_to_shadow(row) -> ShadowTrade:
    """Convierte una fila de `shadow_trades` en un `ShadowTrade`.

    Args:
        row: fila `aiosqlite.Row` con todas las columnas de `shadow_trades` (`SELECT *`).

    Returns:
        El `ShadowTrade`; `decision_source` siempre es `"SIN_LLM"` (la etiqueta real esta en
        `llm_decision`).
    """
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
        reconciliation_failed=bool(row["reconciliation_failed"]),
        reconciliation_attempts=row["reconciliation_attempts"],
        reconciliation_window_start_ms=row["reconciliation_window_start_ms"],
        reconciliation_first_failed_at_ms=row["reconciliation_first_failed_at_ms"],
    )


async def insert_if_new(db: Database, trade: ShadowTrade) -> int | None:
    """Inserta la sombra de una senal agrupada, salvo que ya exista.

    Usa `INSERT OR IGNORE` sobre `signal_group_key` UNICA: la misma senal agrupada nunca
    genera dos sombras.

    Args:
        db: conexion a la base.
        trade: la sombra a insertar.

    Returns:
        El id nuevo, o `None` si esa senal agrupada ya tenia su sombra.

    Efectos:
        Escribe `shadow_trades` (INSERT OR IGNORE).
    """
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
    """Una sombra por su id.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.

    Returns:
        El `ShadowTrade`, o `None` si no existe.

    Efectos:
        Lee `shadow_trades`.
    """
    row = await db.fetch_one("SELECT * FROM shadow_trades WHERE id = ?", (shadow_id,))
    return row_to_shadow(row) if row else None


async def get_open(db: Database) -> list[ShadowTrade]:
    """Todas las sombras abiertas.

    Args:
        db: conexion a la base.

    Returns:
        Lista de `ShadowTrade` con `status = 'OPEN'`, sin orden garantizado.

    Efectos:
        Lee `shadow_trades`.
    """
    rows = await db.fetch_all("SELECT * FROM shadow_trades WHERE status = 'OPEN'")
    return [row_to_shadow(r) for r in rows]


async def get_closed(db: Database) -> list[ShadowTrade]:
    """Todas las sombras cerradas, sin limite ni orden.

    Las usa el veredicto (`ai_value_verdict`). Incluye PnL: no exponerlas hacia fuera sin
    `is_outcome_reveal_allowed`.

    Args:
        db: conexion a la base.

    Returns:
        Lista de `ShadowTrade` con `status = 'CLOSED'`.

    Efectos:
        Lee `shadow_trades`.
    """
    rows = await db.fetch_all("SELECT * FROM shadow_trades WHERE status = 'CLOSED'")
    return [row_to_shadow(r) for r in rows]


async def get_recent(db: Database, limit: int = 50) -> list[ShadowTrade]:
    """Las `limit` sombras mas recientes (abiertas o cerradas), por apertura descendente.

    Subfase C del plan pre-Fase 5 (`GET /api/shadow-trades/recent`). Es aditiva: no
    reemplaza a `get_open` ni `get_closed`, que `ai_value_verdict` sigue usando sin limite.

    Args:
        db: conexion a la base.
        limit: maximo de filas.

    Returns:
        Lista de `ShadowTrade`.

    Efectos:
        Lee `shadow_trades`.
    """
    rows = await db.fetch_all(
        "SELECT * FROM shadow_trades ORDER BY opened_at DESC LIMIT ?", (limit,)
    )
    return [row_to_shadow(r) for r in rows]


async def close(
    db: Database, shadow_id: int, exit_price: float, fee_exit_usdt: float,
    slippage_exit_usdt: float, pnl_gross_usdt: float, pnl_net_usdt: float,
    close_reason: str, fill_source: str, closed_at: datetime | None = None,
) -> None:
    """Cierra una sombra abierta con su salida, comisiones, PnL y motivo.

    El `WHERE ... AND status = 'OPEN'` hace el cierre idempotente: cerrar dos veces no
    cambia nada.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.
        exit_price: precio de salida.
        fee_exit_usdt: comision de salida.
        slippage_exit_usdt: slippage de salida.
        pnl_gross_usdt: PnL bruto.
        pnl_net_usdt: PnL neto.
        close_reason: motivo del cierre (el monitor usa p. ej. `SL`, `TP`, `LIQUIDATION`;
            `RECONCILE_FAILED` si se rindio de reconciliar, sin PnL fiable).
        fill_source: como se lleno el cierre (`TICK`, `CANDLE_RECON`, `REST_MARK`).
        closed_at: hora de cierre; por defecto, ahora (UTC).

    Efectos:
        Escribe `shadow_trades` (UPDATE condicionado a `status = 'OPEN'`).
    """
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
    """Guarda el mejor precio y el stop efectivo del trailing de una sombra.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.
        effective_stop: stop vigente, o `None`.
        best_price: mejor precio visto, o `None`.

    Efectos:
        Escribe `shadow_trades` (UPDATE de `effective_stop` y `best_price`).
    """
    await db.execute(
        "UPDATE shadow_trades SET effective_stop = ?, best_price = ? WHERE id = ?",
        (effective_stop, best_price, shadow_id),
    )


async def update_funding(
    db: Database, shadow_id: int, funding_paid_usdt: float, funding_last_applied_ms: int,
) -> None:
    """Guarda el funding acumulado de una sombra y hasta que momento se aplico.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.
        funding_paid_usdt: funding acumulado total en USDT.
        funding_last_applied_ms: ultimo evento de funding aplicado, en ms Unix.

    Efectos:
        Escribe `shadow_trades` (UPDATE de `funding_paid_usdt` y `funding_last_applied_ms`).
    """
    await db.execute(
        "UPDATE shadow_trades SET funding_paid_usdt = ?, funding_last_applied_ms = ? WHERE id = ?",
        (funding_paid_usdt, funding_last_applied_ms, shadow_id),
    )


async def set_llm_decision(db: Database, shadow_id: int, label: str) -> bool:
    """Escribe la etiqueta del LLM UNA sola vez: `SIN_LLM` -> `APROBADA` o `RECHAZADA`.

    El disparador `llm_decision_inmutable` tambien lo impide a nivel de base; el `WHERE
    llm_decision = 'SIN_LLM'` evita depender solo de el. Es el unico punto de escritura de
    la etiqueta que mide el experimento.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.
        label: `APROBADA` o `RECHAZADA`.

    Returns:
        True si escribio; False si la fila ya tenia una etiqueta (no se toco).

    Efectos:
        Escribe `shadow_trades` (UPDATE condicionado a `llm_decision = 'SIN_LLM'`).
    """
    cursor = await db.execute(
        "UPDATE shadow_trades SET llm_decision = ? WHERE id = ? AND llm_decision = 'SIN_LLM'",
        (label, shadow_id),
    )
    return cursor.rowcount > 0


async def update_reconciliation_state(
    db: Database, shadow_id: int, failed: bool, attempts: int,
    window_start_ms: int | None = None, first_failed_at_ms: int | None = None,
) -> None:
    """Marca si la ultima reconciliacion de la sombra fallo y cuantos intentos van.

    Incidente de estabilidad 2026-10-10, Etapa 2. Mientras el flag este puesto el monitor
    CONGELA la sombra (no la evalua por tick; ver `_evaluate` en
    `app/trading/position_monitor.py`). `window_start_ms` y `first_failed_at_ms` se fijan
    solo en la primera falla de la racha: el llamador decide cuando preservarlos.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.
        failed: True si la reconciliacion fallo.
        attempts: intentos acumulados.
        window_start_ms: desde donde debe seguir reconciliando cada reintento.
        first_failed_at_ms: cuando empezo la racha de fallos.

    Efectos:
        Escribe `shadow_trades` (UPDATE de las cuatro columnas `reconciliation_*`).
    """
    await db.execute(
        "UPDATE shadow_trades SET reconciliation_failed = ?, reconciliation_attempts = ?, "
        "reconciliation_window_start_ms = ?, reconciliation_first_failed_at_ms = ? WHERE id = ?",
        (int(failed), attempts, window_start_ms, first_failed_at_ms, shadow_id),
    )


async def get_unreliable_signal_group_keys(db: Database) -> set[str]:
    """Claves de los grupos cuya reconciliacion esta (o estuvo) marcada como fallida.

    Incluye las sombras con `reconciliation_failed = 1` y las cerradas sin PnL fiable
    (`close_reason = 'RECONCILE_FAILED'`). Se excluyen de la medicion con
    `ai_value_verdict(unreliable_keys=...)`, aparte de `piloto_keys`.

    Args:
        db: conexion a la base.

    Returns:
        Conjunto de `signal_group_key`.

    Efectos:
        Lee `shadow_trades`.
    """
    rows = await db.fetch_all(
        "SELECT signal_group_key FROM shadow_trades "
        "WHERE reconciliation_failed = 1 OR close_reason = 'RECONCILE_FAILED'"
    )
    return {row["signal_group_key"] for row in rows}


async def mark_executed(db: Database, shadow_id: int) -> None:
    """Marca que la cuenta paper abrio esta misma senal.

    La sombra sigue siendo la referencia de comparacion: no depende de la ejecucion. Solo la
    llama el scheduler cuando la apertura automatica sin LLM (`AUTO_OPEN_WITHOUT_LLM`, apagada)
    abrio la misma senal; el brazo ejecutado NO la usa (su vinculo vive en
    `paper_execution_log`), asi que con la configuracion real `executed_in_real_account`
    queda en 0.

    Args:
        db: conexion a la base.
        shadow_id: `shadow_trades.id`.

    Efectos:
        Escribe `shadow_trades` (UPDATE de `executed_in_real_account`).
    """
    await db.execute(
        "UPDATE shadow_trades SET executed_in_real_account = 1 WHERE id = ?", (shadow_id,)
    )
