"""Repositorio de `asset_universe` (Fase 2): los snapshots del universo dinamico de activos.

Cada refresco guarda una fila por moneda evaluada (incluidas las excluidas) con la misma
marca `refreshed_at`; el universo vigente es el snapshot mas reciente.
"""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database
from app.persistence.models import AssetUniverseEntry


async def insert_snapshot(db: Database, entries: list[AssetUniverseEntry]) -> None:
    """Guarda un snapshot completo del universo, con un solo commit.

    Args:
        db: conexion a la base.
        entries: una entrada por moneda evaluada (todas con el mismo `refreshed_at`).

    Efectos:
        Escribe `asset_universe` (INSERT en bloque).
    """
    params = [
        (
            e.refreshed_at.isoformat(), e.coingecko_id, e.symbol, e.coingecko_rank,
            e.market_cap_usd, e.excluded_category, int(e.excluded_manual),
            int(e.has_bitunix_perp),
            None if e.price_sanity_ok is None else int(e.price_sanity_ok),
            int(e.included),
        )
        for e in entries
    ]
    await db.execute_many(
        """
        INSERT INTO asset_universe (
            refreshed_at, coingecko_id, symbol, coingecko_rank, market_cap_usd,
            excluded_category, excluded_manual, has_bitunix_perp, price_sanity_ok,
            included
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params,
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
    """Instante del snapshot mas reciente.

    Args:
        db: conexion a la base.

    Returns:
        El mayor `refreshed_at`, o `None` si nunca se refresco el universo.

    Efectos:
        Lee `asset_universe`.
    """
    row = await db.fetch_one("SELECT MAX(refreshed_at) AS latest FROM asset_universe")
    if row is None or row["latest"] is None:
        return None
    return datetime.fromisoformat(row["latest"])


async def get_latest_snapshot(db: Database) -> list[AssetUniverseEntry]:
    """Las entradas del snapshot mas reciente, por rank de CoinGecko.

    Args:
        db: conexion a la base.

    Returns:
        Lista de `AssetUniverseEntry` (vacia si no hay snapshots).

    Efectos:
        Lee `asset_universe`.
    """
    latest = await get_latest_refreshed_at(db)
    if latest is None:
        return []
    rows = await db.fetch_all(
        "SELECT * FROM asset_universe WHERE refreshed_at = ? ORDER BY coingecko_rank ASC",
        (latest.isoformat(),),
    )
    return [_row_to_entry(r) for r in rows]


async def get_included_symbols(db: Database) -> list[str]:
    """Los simbolos incluidos en el universo vigente.

    Args:
        db: conexion a la base.

    Returns:
        Lista de simbolos Bitunix (p. ej. `BTCUSDT`) con `included = 1` en el ultimo snapshot.

    Efectos:
        Lee `asset_universe`.
    """
    return [e.symbol for e in await get_latest_snapshot(db) if e.included]


async def get_included_history(
    db: Database, limit_snapshots: int = 30,
) -> list[tuple[datetime, frozenset[str]]]:
    """Los ultimos snapshots como (instante, simbolos incluidos), del mas viejo al mas nuevo.

    Un snapshot sin ningun incluido aparece con el conjunto vacio.

    Args:
        db: conexion a la base.
        limit_snapshots: cuantos snapshots recientes considerar.

    Returns:
        Lista de `(refreshed_at, frozenset de simbolos)`.

    Efectos:
        Lee `asset_universe`.
    """
    time_rows = await db.fetch_all(
        "SELECT DISTINCT refreshed_at FROM asset_universe ORDER BY refreshed_at DESC LIMIT ?",
        (limit_snapshots,),
    )
    stamps = [r["refreshed_at"] for r in time_rows]
    if not stamps:
        return []
    placeholders = ", ".join("?" for _ in stamps)
    rows = await db.fetch_all(
        f"SELECT refreshed_at, symbol FROM asset_universe WHERE included = 1 "  # noqa: S608
        f"AND refreshed_at IN ({placeholders})",
        tuple(stamps),
    )
    included: dict[str, set[str]] = {stamp: set() for stamp in stamps}
    for row in rows:
        included[row["refreshed_at"]].add(row["symbol"])
    return [
        (datetime.fromisoformat(stamp), frozenset(included[stamp])) for stamp in reversed(stamps)
    ]
