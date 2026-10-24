"""Salud de las fuentes de datos en vivo (tabla `data_source_health`, subfase 3.4).

Una fila por fuente (`ws_feed` es el latido del WebSocket publico; las noticias usan
`news:<id>`): ultimo exito, ultimo error y fallos seguidos.
"""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database

WS_FEED_SOURCE = "ws_feed"


async def record_success(db: Database, source: str, at: datetime) -> None:
    """Registra un exito de la fuente y reinicia su racha de fallos.

    Args:
        db: conexion a la base.
        source: nombre de la fuente (p. ej. `ws_feed`).
        at: instante del exito.

    Efectos:
        Escribe `data_source_health` (UPSERT: `last_success_at`, `consecutive_failures = 0`).
    """
    await db.execute(
        """
        INSERT INTO data_source_health (source, last_success_at, consecutive_failures)
        VALUES (?, ?, 0)
        ON CONFLICT (source) DO UPDATE SET
            last_success_at = excluded.last_success_at, consecutive_failures = 0
        """,
        (source, at.isoformat()),
    )


async def record_failure(db: Database, source: str, error: str) -> None:
    """Registra un fallo de la fuente y suma uno a su racha.

    Args:
        db: conexion a la base.
        source: nombre de la fuente.
        error: texto del error (se recorta a 500 caracteres).

    Efectos:
        Escribe `data_source_health` (UPSERT: `last_error`, `consecutive_failures + 1`).
    """
    await db.execute(
        """
        INSERT INTO data_source_health (source, last_error, consecutive_failures)
        VALUES (?, ?, 1)
        ON CONFLICT (source) DO UPDATE SET
            last_error = excluded.last_error,
            consecutive_failures = data_source_health.consecutive_failures + 1
        """,
        (source, error[:500]),
    )


async def get_health_rows(db: Database, prefix: str) -> list[dict]:
    """Las filas de salud cuya fuente empieza por `prefix` (p. ej. `news:`).

    Args:
        db: conexion a la base.
        prefix: prefijo del nombre de la fuente.

    Returns:
        Lista de diccionarios con `source`, `last_success_at`, `last_error` y
        `consecutive_failures`, ordenada por fuente.

    Efectos:
        Lee `data_source_health`.
    """
    rows = await db.fetch_all(
        "SELECT source, last_success_at, last_error, consecutive_failures "
        "FROM data_source_health WHERE source LIKE ? ORDER BY source",
        (prefix + "%",),
    )
    return [
        {
            "source": r["source"],
            "last_success_at": r["last_success_at"],
            "last_error": r["last_error"],
            "consecutive_failures": r["consecutive_failures"],
        }
        for r in rows
    ]


async def get_consecutive_failures(db: Database, source: str) -> int:
    """Fallos seguidos de una fuente.

    Args:
        db: conexion a la base.
        source: nombre de la fuente.

    Returns:
        La racha actual (0 si la fuente nunca se registro).

    Efectos:
        Lee `data_source_health`.
    """
    row = await db.fetch_one(
        "SELECT consecutive_failures FROM data_source_health WHERE source = ?", (source,)
    )
    return row["consecutive_failures"] if row else 0


async def get_last_success(db: Database, source: str) -> datetime | None:
    """Ultimo exito de una fuente.

    Args:
        db: conexion a la base.
        source: nombre de la fuente.

    Returns:
        El instante del ultimo exito, o `None` si nunca lo hubo.

    Efectos:
        Lee `data_source_health`.
    """
    row = await db.fetch_one(
        "SELECT last_success_at FROM data_source_health WHERE source = ?", (source,)
    )
    if row is None or row["last_success_at"] is None:
        return None
    return datetime.fromisoformat(row["last_success_at"])
