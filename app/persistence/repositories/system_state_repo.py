"""Tabla `system_state`: pares clave/valor con el estado del bot.

Claves usadas en Fase 1: `bot_status` (RUNNING/PAUSED), `consecutive_losses`.
El resto de claves (daily_pnl, circuit_breaker_until, etc.) se agregan en
Fase 3 cuando el motor de riesgo las necesite.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database


async def set_state(db: Database, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, datetime.now(UTC).isoformat()),
    )


async def get_state(db: Database, key: str, default: str | None = None) -> str | None:
    row = await db.fetch_one("SELECT value FROM system_state WHERE key = ?", (key,))
    return row["value"] if row else default
