"""Tabla `system_state`: pares clave/valor con el estado del bot.

Guarda, entre otras, la pausa del scheduler, el kill switch, el circuit breaker, el stop
por drawdown y su contador, las marcas `real_arm_*` del brazo ejecutado y el estado del
push de Telegram. Las claves las definen quienes las usan (`app/trading/control_state.py`,
`app/trading/risk_engine.py`, ...), no esta capa.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.persistence.database import Database


async def set_state(db: Database, key: str, value: str) -> None:
    """Escribe un valor (inserta o reemplaza) y actualiza `updated_at`.

    Args:
        db: conexion a la base.
        key: clave.
        value: valor como texto.

    Efectos:
        Escribe `system_state` (UPSERT).
    """
    await db.execute(
        """
        INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, datetime.now(UTC).isoformat()),
    )


async def get_state(db: Database, key: str, default: str | None = None) -> str | None:
    """Lee un valor.

    Args:
        db: conexion a la base.
        key: clave.
        default: valor a devolver si la clave no existe.

    Returns:
        El valor como texto, o `default`.

    Efectos:
        Lee `system_state`.
    """
    row = await db.fetch_one("SELECT value FROM system_state WHERE key = ?", (key,))
    return row["value"] if row else default
