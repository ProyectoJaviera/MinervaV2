"""Cache local de velas OHLCV (tabla `ohlcv_cache`).

Permite paginar la descarga historica de Bitunix (max 200 velas/llamada,
10 req/s) sin volver a pedir velas ya guardadas.
"""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.models import OHLCVBar


async def upsert_bars(db: Database, bars: list[OHLCVBar]) -> None:
    """Una sola transaccion para todo el lote (p.ej. una pagina de 200
    velas) -- un commit por fila era el cuello de botella real de la
    descarga historica (ver Database.execute_many)."""
    params = [
        (
            bar.symbol, bar.interval, bar.price_type, bar.open_time, bar.open,
            bar.high, bar.low, bar.close, bar.base_vol, bar.quote_vol,
        )
        for bar in bars
    ]
    await db.execute_many(
        """
        INSERT INTO ohlcv_cache (
            symbol, interval, price_type, open_time, open, high, low, close,
            base_vol, quote_vol
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, interval, price_type, open_time) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low,
            close = excluded.close, base_vol = excluded.base_vol,
            quote_vol = excluded.quote_vol
        """,
        params,
    )


async def get_bars(
    db: Database,
    symbol: str,
    interval: str,
    price_type: str,
    start_time: int | None = None,
    end_time: int | None = None,
) -> list[OHLCVBar]:
    query = (
        "SELECT * FROM ohlcv_cache WHERE symbol = ? AND interval = ? AND price_type = ?"
    )
    params: list = [symbol, interval, price_type]
    if start_time is not None:
        query += " AND open_time >= ?"
        params.append(start_time)
    if end_time is not None:
        query += " AND open_time <= ?"
        params.append(end_time)
    query += " ORDER BY open_time ASC"
    rows = await db.fetch_all(query, tuple(params))
    return [
        OHLCVBar(
            symbol=r["symbol"], interval=r["interval"], price_type=r["price_type"],
            open_time=r["open_time"], open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], base_vol=r["base_vol"], quote_vol=r["quote_vol"],
        )
        for r in rows
    ]


async def get_covered_open_times(
    db: Database, symbol: str, interval: str, price_type: str
) -> set[int]:
    rows = await db.fetch_all(
        "SELECT open_time FROM ohlcv_cache WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return {r["open_time"] for r in rows}


async def get_floor(db: Database, symbol: str, interval: str, price_type: str) -> int | None:
    """Timestamp de la vela mas antigua que existe en Bitunix para este
    (symbol, interval, price_type), si ya se detecto (ver `set_floor`)."""
    row = await db.fetch_one(
        "SELECT floor_open_time FROM ohlcv_floor "
        "WHERE symbol = ? AND interval = ? AND price_type = ?",
        (symbol, interval, price_type),
    )
    return int(row["floor_open_time"]) if row else None


async def set_floor(
    db: Database, symbol: str, interval: str, price_type: str, floor_open_time: int
) -> None:
    await db.execute(
        """
        INSERT INTO ohlcv_floor (symbol, interval, price_type, floor_open_time)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (symbol, interval, price_type) DO UPDATE SET
            floor_open_time = MIN(floor_open_time, excluded.floor_open_time)
        """,
        (symbol, interval, price_type, floor_open_time),
    )
