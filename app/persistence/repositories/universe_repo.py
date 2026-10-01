"""Repositorio de `asset_universe` (Fase 2)."""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry


async def insert_snapshot(db: Database, entries: list[AssetUniverseEntry]) -> None:
    for e in entries:
        await db.execute(
            """
            INSERT INTO asset_universe (
                refreshed_at, coingecko_id, symbol, coingecko_rank, market_cap_usd,
                excluded_category, excluded_manual, has_bitunix_perp, price_sanity_ok,
                included
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                e.refreshed_at.isoformat(), e.coingecko_id, e.symbol, e.coingecko_rank,
                e.market_cap_usd, e.excluded_category, int(e.excluded_manual),
                int(e.has_bitunix_perp),
                None if e.price_sanity_ok is None else int(e.price_sanity_ok),
                int(e.included),
            ),
        )


def _row_to_entry(row) -> AssetUniverseEntry:
    return AssetUniverseEntry(
        id=row["id"],
        refreshed_at=datetime.fromisoformat(row["refreshed_at"]),
        coingecko_id=row["coingecko_id"],
        symbol=row["symbol"],
        coingecko_rank=row["coingecko_rank"],
        market_cap_usd=row["market_cap_usd"],
        excluded_category=row["excluded_category"],
        excluded_manual=bool(row["excluded_manual"]),
        has_bitunix_perp=bool(row["has_bitunix_perp"]),
        price_sanity_ok=None if row["price_sanity_ok"] is None else bool(row["price_sanity_ok"]),
        included=bool(row["included"]),
    )


async def get_latest_refreshed_at(db: Database) -> datetime | None:
    row = await db.fetch_one("SELECT MAX(refreshed_at) AS latest FROM asset_universe")
    if row is None or row["latest"] is None:
        return None
    return datetime.fromisoformat(row["latest"])


async def get_latest_snapshot(db: Database) -> list[AssetUniverseEntry]:
    latest = await get_latest_refreshed_at(db)
    if latest is None:
        return []
    rows = await db.fetch_all(
        "SELECT * FROM asset_universe WHERE refreshed_at = ? ORDER BY coingecko_rank ASC",
        (latest.isoformat(),),
    )
    return [_row_to_entry(r) for r in rows]


async def get_included_symbols(db: Database) -> list[str]:
    return [e.symbol for e in await get_latest_snapshot(db) if e.included]
