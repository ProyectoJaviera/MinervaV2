"""Salud de fuentes de datos en vivo (tabla `data_source_health`, subfase 3.4)."""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database

WS_FEED_SOURCE = "ws_feed"


async def record_success(db: Database, source: str, at: datetime) -> None:
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


async def get_last_success(db: Database, source: str) -> datetime | None:
    row = await db.fetch_one(
        "SELECT last_success_at FROM data_source_health WHERE source = ?", (source,)
    )
    if row is None or row["last_success_at"] is None:
        return None
    return datetime.fromisoformat(row["last_success_at"])
