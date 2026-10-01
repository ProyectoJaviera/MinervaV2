"""Descarga historica de velas con paginacion y cache local.

Respeta el limite de Bitunix (200 velas/llamada, 10 req/s -- este ultimo ya
lo aplica `BitunixRestClient`) y evita volver a pedir velas que ya estan en
`ohlcv_cache`.

Formato de cada vela devuelto por `GET /market/kline` (verificado):
`{"open": "60000", "high": "60001", "close": "60000", "low": "59989.2",
"time": 111111, "quoteVol": "1", "baseVol": "60000", "type": "LAST_PRICE"}`.

**Correccion de Fase 2** (ver docs/FASE2_PLAN.md): se verifico empiricamente
contra la API real que `/market/kline` NO pagina hacia adelante desde
`start_time` -- pagina hacia ATRAS desde `end_time` (devuelve las `limit`
velas mas recientes en o antes de `end_time`). La version de Fase 1 asumia
paginacion hacia adelante y nunca avanzaba en un rango historico amplio.
Esta version pagina hacia atras: parte de `end_time`, cada pagina retrocede
el cursor al `open_time` mas antiguo recibido menos 1, hasta cubrir
`start_time` o agotar el historial disponible (la API devuelve menos de
`limit` velas, o ninguna).
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


def interval_to_ms(interval: str) -> int | None:
    """Duracion de un intervalo de vela en ms, o None si es desconocido
    (p. ej. '1M' -- mes calendario, duracion variable)."""
    return _INTERVAL_MS.get(interval)


def _bar_from_raw(symbol: str, interval: str, price_type: str, raw: dict) -> OHLCVBar:
    """`price_type` se toma del parametro SOLICITADO a la API, no del campo
    `type` de la respuesta -- verificado empiricamente que la API no
    siempre lo incluye (p.ej. ausente en varias respuestas de MARK_PRICE),
    lo que etiquetaba mal las velas y las mezclaba con LAST_PRICE en cache
    (bug encontrado durante la corrida real de Fase 2)."""
    return OHLCVBar(
        symbol=symbol,
        interval=interval,
        price_type=price_type,
        open_time=int(raw["time"]),
        open=float(raw["open"]),
        high=float(raw["high"]),
        low=float(raw["low"]),
        close=float(raw["close"]),
        base_vol=float(raw["baseVol"]) if raw.get("baseVol") is not None else None,
        quote_vol=float(raw["quoteVol"]) if raw.get("quoteVol") is not None else None,
    )


def drop_incomplete_last_bar(
    bars: list[OHLCVBar], interval: str, now_ms: int
) -> list[OHLCVBar]:
    """Descarta la ultima vela si todavia no ha cerrado (su open_time + la
    duracion del intervalo es posterior a `now_ms`). Usado por el motor de
    backtest (Fase 2, punto 6 de los ajustes) para nunca operar sobre una
    vela en formacion."""
    step_ms = interval_to_ms(interval)
    if step_ms is None or not bars:
        return bars
    return [b for b in bars if b.open_time + step_ms <= now_ms]


async def get_or_fetch(
    client: BitunixRestClient,
    db: Database,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    price_type: str = "LAST_PRICE",
) -> list[OHLCVBar]:
    """Devuelve las velas de [start_time, end_time] (ms), descargando de
    Bitunix solo lo que falte en el cache local. Pagina hacia atras desde
    `end_time` (ver docstring del modulo)."""
    step_ms = interval_to_ms(interval)

    already_covered = False
    if step_ms:
        covered = await ohlcv_repo.get_covered_open_times(db, symbol, interval, price_type)
        floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)
        # No se compara contra una grilla aritmetica exacta desde
        # `start_time` -- un `start_time`/`end_time` arbitrario (p.ej. un
        # corte IS/OOS que no cae justo en un borde de vela real) nunca
        # coincidiria con los `open_time` reales de Bitunix, forzando una
        # redescarga completa innecesaria cada vez (encontrado durante la
        # corrida real de Fase 2). En cambio, se verifica que el rango ya
        # cacheado "abarque" el pedido: hay una vela en o antes de
        # `start_time` (o, si `start_time` es anterior al piso real del
        # historial ya detectado, no hace falta ninguna mas antigua) y una
        # vela en o despues de `end_time - 2*step_ms`: la ultima vela
        # CERRADA puede estar hasta casi 2 intervalos detras de "ahora"
        # (la vela en curso todavia no cerro) -- exigir `end_time -
        # step_ms` como se intento primero es demasiado estricto y
        # dispara una redescarga completa espuria en cada corrida real
        # (encontrado en la corrida de Fase 2).
        if covered:
            min_ok = min(covered) <= start_time or (floor is not None and floor <= min(covered))
            already_covered = min_ok and max(covered) >= end_time - 2 * step_ms

    if not already_covered:
        cursor = end_time
        while cursor >= start_time:
            raw_bars = await client.get_kline(
                symbol=symbol,
                interval=interval,
                end_time=cursor,
                limit=PAGE_LIMIT,
                price_type=price_type,
            )
            if not raw_bars:
                logger.info(
                    "%s %s: historial agotado en Bitunix antes de alcanzar start_time=%d",
                    symbol, interval, start_time,
                )
                # El piso real quedo en el `cursor` actual (no hay nada en o
                # antes de este punto): se recuerda para no reintentar.
                if step_ms:
                    await ohlcv_repo.set_floor(db, symbol, interval, price_type, cursor)
                break
            bars = [_bar_from_raw(symbol, interval, price_type, r) for r in raw_bars]
            await ohlcv_repo.upsert_bars(db, bars)
            oldest_time = min(b.open_time for b in bars)
            if len(raw_bars) < PAGE_LIMIT:
                # Pagina parcial: la API ya no tiene mas historial antes de
                # `oldest_time` -- ese es el piso real, se recuerda.
                if step_ms:
                    await ohlcv_repo.set_floor(db, symbol, interval, price_type, oldest_time)
                break
            if oldest_time <= start_time:
                break
            cursor = oldest_time - 1

    return await ohlcv_repo.get_bars(db, symbol, interval, price_type, start_time, end_time)
