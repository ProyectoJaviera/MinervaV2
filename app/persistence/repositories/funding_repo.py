"""Cache local del historial de funding (tabla `funding_cache`).

Misma idea que `ohlcv_repo` para las velas: evita volver a pedir por red el funding ya
descargado en una corrida o reintento anterior del backtest. El piso del historial de
funding vive en `ohlcv_floor` con `interval = price_type = '__funding__'`.
"""

from __future__ import annotations

from app.persistence.database import Database


async def upsert_funding(db: Database, symbol: str, events: list[tuple[int, float]]) -> None:
    """Guarda eventos de funding (inserta o actualiza la tasa).

    Args:
        db: conexion a la base.
        symbol: simbolo.
        events: lista de `(funding_time en ms Unix, funding_rate)`. Con la lista vacia no hace
            nada.

    Efectos:
        Escribe `funding_cache` (UPSERT en bloque).
    """
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
    """Los eventos de funding guardados de un simbolo en un rango.

    Args:
        db: conexion a la base.
        symbol: simbolo.
        start_time: inicio del rango (ms Unix, inclusive).
        end_time: fin del rango (ms Unix, inclusive).

    Returns:
        Lista de `(funding_time, funding_rate)` en orden cronologico.

    Efectos:
        Lee `funding_cache`.
    """
    rows = await db.fetch_all(
        "SELECT funding_time, funding_rate FROM funding_cache "
        "WHERE symbol = ? AND funding_time >= ? AND funding_time <= ? "
        "ORDER BY funding_time ASC",
        (symbol, start_time, end_time),
    )
    return [(int(r["funding_time"]), float(r["funding_rate"])) for r in rows]


async def get_covered_range(db: Database, symbol: str) -> tuple[int, int] | None:
    """El evento de funding mas antiguo y el mas nuevo ya guardados de un simbolo.

    Misma idea que `ohlcv_repo.get_covered_range`.

    Args:
        db: conexion a la base.
        symbol: simbolo.

    Returns:
        La tupla `(mas antiguo, mas nuevo)` en ms Unix, o `None` si no hay eventos.

    Efectos:
        Lee `funding_cache`.
    """
    row = await db.fetch_one(
        "SELECT MIN(funding_time) AS lo, MAX(funding_time) AS hi FROM funding_cache "
        "WHERE symbol = ?",
        (symbol,),
    )
    if row is None or row["lo"] is None:
        return None
    return int(row["lo"]), int(row["hi"])
