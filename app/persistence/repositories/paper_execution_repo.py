"""Tabla `paper_execution_log` (subfase 3.9): una fila por cada decision APROBADA que el brazo
ejecutado de la cuenta PAPER considero, abra o no (`app/trading/paper_execution.py`).

Solo guarda la decision de ejecucion y sus motivos -- nunca PnL ni ningun resultado. El
reclamo (`claim`) es idempotente: `signal_group_key` es UNICA, asi un grupo nunca abre dos
veces ni siquiera si el gancho se invocara dos veces."""

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
    """Reclama el grupo (`PENDING`). `False` si ya estaba reclamado: el llamador no debe
    hacer nada mas con ese grupo."""
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
    await db.execute(
        "UPDATE paper_execution_log SET outcome = 'SKIPPED', reason = ?, details_json = ?, "
        "resolved_at = ? WHERE signal_group_key = ?",
        (
            reason, json.dumps(details or {}), (resolved_at or datetime.now(UTC)).isoformat(),
            signal_group_key,
        ),
    )


async def get_by_key(db: Database, signal_group_key: str) -> PaperExecutionEntry | None:
    row = await db.fetch_one(
        "SELECT * FROM paper_execution_log WHERE signal_group_key = ?", (signal_group_key,)
    )
    return _row_to_entry(row) if row else None


async def get_pending(db: Database) -> list[PaperExecutionEntry]:
    rows = await db.fetch_all(
        "SELECT * FROM paper_execution_log WHERE outcome = 'PENDING' ORDER BY id"
    )
    return [_row_to_entry(r) for r in rows]


async def get_recent(db: Database, limit: int = 50) -> list[PaperExecutionEntry]:
    rows = await db.fetch_all(
        "SELECT * FROM paper_execution_log ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [_row_to_entry(r) for r in rows]


async def count_outcomes_since(db: Database, since: datetime) -> dict[str, int]:
    """`{"OPENED": n, "SKIPPED": m, "PENDING": k}` de lo reclamado desde `since`."""
    rows = await db.fetch_all(
        "SELECT outcome, COUNT(*) AS n FROM paper_execution_log WHERE created_at >= ? "
        "GROUP BY outcome",
        (since.isoformat(),),
    )
    return {r["outcome"]: r["n"] for r in rows}


async def count_by_reason(db: Database) -> dict[str, int]:
    """Todos los SKIPPED agrupados por motivo (consulta de auditoria)."""
    rows = await db.fetch_all(
        "SELECT reason, COUNT(*) AS n FROM paper_execution_log WHERE outcome = 'SKIPPED' "
        "GROUP BY reason ORDER BY n DESC"
    )
    return {r["reason"]: r["n"] for r in rows}
