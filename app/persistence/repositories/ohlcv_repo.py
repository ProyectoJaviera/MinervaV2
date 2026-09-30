"""Cache local de velas OHLCV (tabla `ohlcv_cache`).

Permite paginar la descarga historica de Bitunix (max 200 velas/llamada,
10 req/s) sin volver a pedir velas ya guardadas.
"""

from __future__ import annotations

from app.persistence.database import Database
from app.persistence.models import OHLCVBar


async def upsert_bars(db: Database, bars: list[OHLCVBar]) -> None:
    for bar in bars:
        await db.execute(
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
            (
                bar.symbol, bar.interval, bar.price_type, bar.open_time, bar.open,
                bar.high, bar.low, bar.close, bar.base_vol, bar.quote_vol,
            ),
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
