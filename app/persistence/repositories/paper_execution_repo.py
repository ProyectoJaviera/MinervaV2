"""Tabla `paper_execution_log` (subfase 3.9): lo que decidio el brazo ejecutado de la paper.

Una fila por cada decision APROBADA que el brazo (`app/trading/paper_execution.py`)
considero, abra o no. Solo guarda la decision de ejecucion y sus motivos, nunca PnL ni
resultados. El reclamo (`claim`) es idempotente: `signal_group_key` es UNICA, asi un
grupo nunca abre dos veces ni siquiera si el gancho se invocara dos veces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from app.persistence.database import Database

OUTCOME_PENDING = "PENDING"
OUTCOME_OPENED = "OPENED"
OUTCOME_SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class PaperExecutionEntry:
    """Una fila de `paper_execution_log`, ya convertida (fechas y `details` decodificados).

    `outcome` es `PENDING` (reclamada, sin resolver), `OPENED` (abrio la posicion
    `trade_id`) o `SKIPPED` (no abrio; `reason` dice por que).
    """
    id: int
    signal_group_key: str
    created_at: datetime
    resolved_at: datetime | None
    shadow_trade_id: int | None
    symbol: str
    side: str
    strategy: str | None
    decision_delay_s: float
    outcome: str
    reason: str | None
    details: dict
    trade_id: int | None


def _row_to_entry(row) -> PaperExecutionEntry:
    return PaperExecutionEntry(
        id=row["id"],
        signal_group_key=row["signal_group_key"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        shadow_trade_id=row["shadow_trade_id"],
        symbol=row["symbol"],
        side=row["side"],
        strategy=row["strategy"],
        decision_delay_s=row["decision_delay_s"],
        outcome=row["outcome"],
        reason=row["reason"],
        details=json.loads(row["details_json"]) if row["details_json"] else {},
        trade_id=row["trade_id"],
    )


async def claim(
    db: Database, *, signal_group_key: str, shadow_trade_id: int | None, symbol: str, side: str,
    strategy: str | None, decision_delay_s: float, created_at: datetime | None = None,
) -> bool:
    """Reclama un grupo para el brazo (`PENDING`): solo el primero que llega lo consigue.

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.
        shadow_trade_id: `shadow_trades.id` de la sombra, si se conoce.
        symbol: simbolo de la senal.
        side: `LONG` o `SHORT`.
        strategy: estrategia representante, si hay.
        decision_delay_s: retraso de la decision del LLM, en segundos.
        created_at: hora del reclamo; por defecto, ahora (UTC).

    Returns:
        True si este llamador reclamo el grupo; False si ya estaba reclamado (no debe hacer
        nada mas con ese grupo).

    Efectos:
        Escribe `paper_execution_log` (INSERT OR IGNORE).
    """
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO paper_execution_log (
            signal_group_key, created_at, shadow_trade_id, symbol, side, strategy,
            decision_delay_s, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """,
        (
            signal_group_key, (created_at or datetime.now(UTC)).isoformat(), shadow_trade_id,
            symbol, side, strategy, decision_delay_s,
        ),
    )
    return cursor.rowcount == 1


async def resolve_opened(
    db: Database, signal_group_key: str, *, trade_id: int, details: dict,
    resolved_at: datetime | None = None,
) -> None:
    """Resuelve un reclamo como `OPENED`, con el id de la posicion abierta y sus detalles.

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.
        trade_id: `trades.id` de la posicion abierta.
        details: detalles para auditoria (retraso de entrada, precios, cantidad...).
        resolved_at: hora de resolucion; por defecto, ahora (UTC).

    Efectos:
        Escribe `paper_execution_log` (UPDATE: `outcome`, `trade_id`, `details_json`,
        `resolved_at`; borra `reason`).
    """
    await db.execute(
        "UPDATE paper_execution_log SET outcome = 'OPENED', reason = NULL, trade_id = ?, "
        "details_json = ?, resolved_at = ? WHERE signal_group_key = ?",
        (
            trade_id, json.dumps(details), (resolved_at or datetime.now(UTC)).isoformat(),
            signal_group_key,
        ),
    )


async def resolve_skipped(
    db: Database, signal_group_key: str, *, reason: str, details: dict | None = None,
    resolved_at: datetime | None = None,
) -> None:
    """Resuelve un reclamo como `SKIPPED` con su motivo.

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.
        reason: motivo (`LATE_DECISION`, `SCHEDULER_PAUSED`, `RISK_<codigo>`, ...).
        details: detalles para auditoria, o `None`.
        resolved_at: hora de resolucion; por defecto, ahora (UTC).

    Efectos:
        Escribe `paper_execution_log` (UPDATE: `outcome`, `reason`, `details_json`,
        `resolved_at`).
    """
    await db.execute(
        "UPDATE paper_execution_log SET outcome = 'SKIPPED', reason = ?, details_json = ?, "
        "resolved_at = ? WHERE signal_group_key = ?",
        (
            reason, json.dumps(details or {}), (resolved_at or datetime.now(UTC)).isoformat(),
            signal_group_key,
        ),
    )


async def get_by_key(db: Database, signal_group_key: str) -> PaperExecutionEntry | None:
    """La fila de un grupo.

    Args:
        db: conexion a la base.
        signal_group_key: clave de la senal agrupada.

    Returns:
        La `PaperExecutionEntry`, o `None` si el brazo nunca reclamo ese grupo.

    Efectos:
        Lee `paper_execution_log`.
    """
    row = await db.fetch_one(
        "SELECT * FROM paper_execution_log WHERE signal_group_key = ?", (signal_group_key,)
    )
    return _row_to_entry(row) if row else None


async def get_pending(db: Database) -> list[PaperExecutionEntry]:
    """Los reclamos sin resolver, del mas antiguo al mas nuevo.

    Al arrancar, el brazo los resuelve (los que quedaron a medias por un reinicio).

    Args:
        db: conexion a la base.

    Returns:
        Lista de `PaperExecutionEntry` con `outcome = 'PENDING'`.

    Efectos:
        Lee `paper_execution_log`.
    """
    rows = await db.fetch_all(
        "SELECT * FROM paper_execution_log WHERE outcome = 'PENDING' ORDER BY id"
    )
    return [_row_to_entry(r) for r in rows]


async def get_recent(db: Database, limit: int = 50) -> list[PaperExecutionEntry]:
    """Las filas mas recientes del registro.

    Args:
        db: conexion a la base.
        limit: maximo de filas.

    Returns:
        Lista de `PaperExecutionEntry`, la de `id` mas alto primero.

    Efectos:
        Lee `paper_execution_log`.
    """
    rows = await db.fetch_all(
        "SELECT * FROM paper_execution_log ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [_row_to_entry(r) for r in rows]


async def count_outcomes_since(db: Database, since: datetime) -> dict[str, int]:
    """Cuenta lo reclamado desde `since`, por resultado.

    Args:
        db: conexion a la base.
        since: inicio del periodo (inclusive).

    Returns:
        Diccionario como `{"OPENED": n, "SKIPPED": m, "PENDING": k}` (solo las claves que
        aparecen).

    Efectos:
        Lee `paper_execution_log`.
    """
    rows = await db.fetch_all(
        "SELECT outcome, COUNT(*) AS n FROM paper_execution_log WHERE created_at >= ? "
        "GROUP BY outcome",
        (since.isoformat(),),
    )
    return {r["outcome"]: r["n"] for r in rows}


async def count_by_reason(db: Database) -> dict[str, int]:
    """Todos los `SKIPPED` agrupados por motivo (consulta de auditoria).

    Args:
        db: conexion a la base.

    Returns:
        Diccionario `{motivo: filas}`, del motivo mas frecuente al menos frecuente.

    Efectos:
        Lee `paper_execution_log`.
    """
    rows = await db.fetch_all(
        "SELECT reason, COUNT(*) AS n FROM paper_execution_log WHERE outcome = 'SKIPPED' "
        "GROUP BY reason ORDER BY n DESC"
    )
    return {r["reason"]: r["n"] for r in rows}
