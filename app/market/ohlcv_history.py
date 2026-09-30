"""Descarga historica de velas con paginacion y cache local.

Respeta el limite de Bitunix (200 velas/llamada, 10 req/s -- este ultimo ya
lo aplica `BitunixRestClient`) y evita volver a pedir velas que ya estan en
`ohlcv_cache`.

Formato de cada vela devuelto por `GET /market/kline` (verificado):
`{"open": "60000", "high": "60001", "close": "60000", "low": "59989.2",
"time": 111111, "quoteVol": "1", "baseVol": "60000", "type": "LAST_PRICE"}`.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo

logger = get_logger(__name__)

PAGE_LIMIT = 200

_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def _bar_from_raw(symbol: str, interval: str, raw: dict) -> OHLCVBar:
    return OHLCVBar(
        symbol=symbol,
        interval=interval,
        price_type=raw.get("type", "LAST_PRICE"),
        open_time=int(raw["time"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        base_vol=float(raw["baseVol"]) if raw.get("baseVol") is not None else None,
        quote_vol=float(raw["quoteVol"]) if raw.get("quoteVol") is not None else None,
    )


async def get_or_fetch(
    client: BitunixRestClient,
    db: Database,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    price_type: str = "LAST_PRICE",
) -> list[OHLCVBar]:
    """Devuelve las velas de [start_time, end_time] (ms), descargando de Bitunix
    solo lo que falte en el cache local."""
    covered = await ohlcv_repo.get_covered_open_times(db, symbol, interval, price_type)
    step_ms = _INTERVAL_MS.get(interval)

    if step_ms and covered:
        expected = set(range(start_time, end_time + 1, step_ms))
        missing = sorted(expected - covered)
    else:
        # Intervalo desconocido o cache vacio: se descarga el rango completo y se
        # deja que la cache (PRIMARY KEY) deduplique en el proximo upsert.
        missing = [start_time] if not covered else []

    if missing or not covered:
        cursor = start_time
        while cursor <= end_time:
            raw_bars = await client.get_kline(
                symbol=symbol,
                interval=interval,
                start_time=cursor,
                end_time=end_time,
                limit=PAGE_LIMIT,
                price_type=price_type,
            )
            if not raw_bars:
                break
            bars = [_bar_from_raw(symbol, interval, r) for r in raw_bars]
            await ohlcv_repo.upsert_bars(db, bars)
            latest_time = max(b.open_time for b in bars)
            if step_ms is None or latest_time <= cursor:
                break
            cursor = latest_time + step_ms
            if len(raw_bars) < PAGE_LIMIT:
                break

    return await ohlcv_repo.get_bars(db, symbol, interval, price_type, start_time, end_time)
