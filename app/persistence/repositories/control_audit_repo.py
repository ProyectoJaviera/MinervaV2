"""Tabla `control_audit` (Fase 5): rastro de cada accion de control (pausar, reanudar, kill).

Se escribe desde el dashboard y desde Telegram. Solo guarda quien, cuando, la accion y el
estado de pausa y kill switch antes y despues: nunca datos del chat ni resultados de
operaciones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.persistence.database import Database
from app.persistence.repositories.timeutil import iso_utc

CHANNEL_DASHBOARD = "DASHBOARD"
CHANNEL_TELEGRAM = "TELEGRAM"
ACTIONS = ("pause", "resume", "kill")


@dataclass(frozen=True)
class ControlAuditEntry:
    """Una fila de `control_audit`.

    `channel` es `DASHBOARD` o `TELEGRAM`; `action`, `pause`, `resume` o `kill`; los cuatro
    booleanos son el estado de pausa y de kill switch antes y despues de la accion.
    """
    id: int
    created_at: datetime
    channel: str
    actor: str
    action: str
    paused_before: bool
    kill_before: bool
    paused_after: bool
    kill_after: bool

    @property
    def changed(self) -> bool:
        """Si la accion cambio algo (una pausa sobre un scheduler ya pausado no cambia nada)."""
        return (self.paused_before, self.kill_before) != (self.paused_after, self.kill_after)


def _row_to_entry(row) -> ControlAuditEntry:
    return ControlAuditEntry(
        id=row["id"], created_at=datetime.fromisoformat(row["created_at"]),
        channel=row["channel"], actor=row["actor"], action=row["action"],
        paused_before=bool(row["paused_before"]), kill_before=bool(row["kill_before"]),
        paused_after=bool(row["paused_after"]), kill_after=bool(row["kill_after"]),
    )


async def insert(
    db: Database, *, channel: str, actor: str, action: str, paused_before: bool,
    kill_before: bool, paused_after: bool, kill_after: bool, created_at: datetime | None = None,
) -> int:
    """Registra una accion de control ya ejecutada.

    Args:
        db: conexion a la base.
        channel: `DASHBOARD` o `TELEGRAM`.
        actor: quien la pidio (usuario del dashboard o `telegram`).
        action: `pause`, `resume` o `kill`.
        paused_before: scheduler pausado antes de la accion.
        kill_before: kill switch activo antes.
        paused_after: scheduler pausado despues.
        kill_after: kill switch activo despues.
        created_at: hora de la accion; por defecto, ahora (UTC).

    Returns:
        El id de la fila.

    Efectos:
        Escribe `control_audit` (INSERT).
    """
    cursor = await db.execute(
        """
        INSERT INTO control_audit (
            created_at, channel, actor, action, paused_before, kill_before, paused_after,
            kill_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            iso_utc(created_at or datetime.now(UTC)), channel, actor, action, int(paused_before),
            int(kill_before), int(paused_after), int(kill_after),
        ),
    )
    return int(cursor.lastrowid)


async def get_recent(db: Database, limit: int = 20) -> list[ControlAuditEntry]:
    """Las acciones de control mas recientes.

    Args:
        db: conexion a la base.
        limit: maximo de filas.

    Returns:
        Lista de `ControlAuditEntry`, la de `id` mas alto primero.

    Efectos:
        Lee `control_audit`.
    """
    rows = await db.fetch_all("SELECT * FROM control_audit ORDER BY id DESC LIMIT ?", (limit,))
    return [_row_to_entry(r) for r in rows]


async def get_latest(db: Database) -> ControlAuditEntry | None:
    """La ultima accion de control registrada.

    Args:
        db: conexion a la base.

    Returns:
        La `ControlAuditEntry` mas reciente, o `None` si no hay ninguna.

    Efectos:
        Lee `control_audit`.
    """
    recent = await get_recent(db, limit=1)
    return recent[0] if recent else None
