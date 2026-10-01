"""Descarga incremental y reanudable de `funding_rate_history`, con cache
local en `funding_cache` -- misma idea que `ohlcv_history.py` para velas
(ver ese modulo para el razonamiento completo del diseno incremental).

A diferencia de las velas, el funding no es indispensable para que el
motor de backtest corra: cuando faltan eventos reales para un tramo,
`app/backtesting/funding.py::build_funding_series` ya aproxima con la
mediana del funding real observado (o 0.0 si no hay ninguno) y marca esas
operaciones como `funding_is_approximated=True`. Por eso el motor NUNCA
lanza un error por funding incompleto -- solo las velas son obligatorias
(`ohlcv_history.get_cached_or_raise`). Esta descarga es, aun asi, la forma
de maximizar cuanto funding REAL (no aproximado) tiene el backtest, y debe
correrse antes via `scripts/download_history.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.market.ohlcv_history import EMPTY_PAGE_RETRIES
from app.persistence.database import Database
from app.persistence.repositories import funding_repo, ohlcv_repo

logger = get_logger(__name__)

PAGE_LIMIT = 200
_FLOOR_INTERVAL = "__funding__"
_FLOOR_PRICE_TYPE = "__funding__"
# El funding se repone cada ~8h; 2 dias de tolerancia evita una descarga
# espuria por "falta el ultimo evento" cuando en realidad ya esta todo al
# dia (mismo criterio que antes vivia en app/backtesting/engine.py).
FRESHNESS_TOLERANCE_MS = 2 * 24 * 60 * 60 * 1000


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


async def _fetch_funding_page(
    client: BitunixRestClient, symbol: str, cursor: int
) -> list[dict]:
    for attempt in range(EMPTY_PAGE_RETRIES + 1):
        rows = await client.get_funding_rate_history(symbol, end_time=cursor, limit=PAGE_LIMIT)
        if rows:
            return rows
        if attempt < EMPTY_PAGE_RETRIES:
            logger.warning(
                "%s funding: pagina vacia en end_time=%d (reintento %d/%d)",
                symbol, cursor, attempt + 1, EMPTY_PAGE_RETRIES,
            )
    return []


async def _download_funding_range(
    client: BitunixRestClient, db: Database, symbol: str,
    range_start: int, range_end: int, floor: int | None,
) -> None:
    if range_start > range_end:
        return
    if floor is not None and range_end < floor:
        return

    cursor = range_end
    page_num = 0
    while cursor >= range_start:
        page_num += 1
        rows = await _fetch_funding_page(client, symbol, cursor)
        if not rows:
            logger.info(
                "%s funding: historial agotado antes de %s (pagina %d)",
                symbol, _fmt(range_start), page_num,
            )
            await ohlcv_repo.set_floor(db, symbol, _FLOOR_INTERVAL, _FLOOR_PRICE_TYPE, cursor)
            return

        batch = [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        await funding_repo.upsert_funding(db, symbol, batch)
        oldest = min(t for t, _ in batch)
        logger.info(
            "%s funding: pagina %d guardada (%d eventos, hasta %s)",
            symbol, page_num, len(batch), _fmt(oldest),
        )

        if len(rows) < PAGE_LIMIT:
            await ohlcv_repo.set_floor(db, symbol, _FLOOR_INTERVAL, _FLOOR_PRICE_TYPE, oldest)
            return
        if oldest <= range_start:
            return
        cursor = oldest - 1


async def download_missing_funding(
    client: BitunixRestClient, db: Database, symbol: str, start_time_ms: int, end_time_ms: int,
) -> None:
    """Descarga por red SOLO la cola/cabeza de `funding_rate_history` que
    falte -- misma logica que `ohlcv_history.download_missing`. Usada
    exclusivamente por `scripts/download_history.py`."""
    floor = await ohlcv_repo.get_floor(db, symbol, _FLOOR_INTERVAL, _FLOOR_PRICE_TYPE)
    covered = await funding_repo.get_covered_range(db, symbol)

    if covered is None:
        await _download_funding_range(client, db, symbol, start_time_ms, end_time_ms, floor)
        return

    min_cached, max_cached = covered

    if max_cached < end_time_ms - FRESHNESS_TOLERANCE_MS:
        await _download_funding_range(client, db, symbol, max_cached + 1, end_time_ms, floor)
        floor = await ohlcv_repo.get_floor(db, symbol, _FLOOR_INTERVAL, _FLOOR_PRICE_TYPE)

    head_target = start_time_ms if floor is None else max(start_time_ms, floor)
    if min_cached > head_target:
        await _download_funding_range(client, db, symbol, start_time_ms, min_cached - 1, floor)
