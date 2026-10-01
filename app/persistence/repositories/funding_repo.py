"""Cache local de `funding_rate_history` (misma idea que `ohlcv_repo` para
velas): evita volver a pedir por red el funding ya descargado en una
corrida/reintento anterior del backtest."""

from __future__ import annotations

from app.persistence.database import Database


async def upsert_funding(db: Database, symbol: str, events: list[tuple[int, float]]) -> None:
    if not events:
        return
    await db.execute_many(
        """
        INSERT INTO funding_cache (symbol, funding_time, funding_rate)
        VALUES (?, ?, ?)
        ON CONFLICT (symbol, funding_time) DO UPDATE SET funding_rate = excluded.funding_rate
        """,
        [(symbol, t, r) for t, r in events],
    )


async def get_funding(
    db: Database, symbol: str, start_time: int, end_time: int
) -> list[tuple[int, float]]:
    rows = await db.fetch_all(
        "SELECT funding_time, funding_rate FROM funding_cache "
        "WHERE symbol = ? AND funding_time >= ? AND funding_time <= ? "
        "ORDER BY funding_time ASC",
        (symbol, start_time, end_time),
    )
    return [(int(r["funding_time"]), float(r["funding_rate"])) for r in rows]


async def get_covered_funding_times(db: Database, symbol: str) -> set[int]:
    rows = await db.fetch_all(
        "SELECT funding_time FROM funding_cache WHERE symbol = ?", (symbol,)
    )
    return {int(r["funding_time"]) for r in rows}
