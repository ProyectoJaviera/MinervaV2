"""Descarga incremental y reanudable de velas, con cache local.

Respeta el limite de Bitunix (200 velas/llamada, paginacion hacia ATRAS
desde `end_time` -- verificado empiricamente, ver docs/FASE2_PLAN.md) y
descarga SOLO lo que falta en `ohlcv_cache`, nunca el rango completo de
nuevo.

Formato de cada vela devuelto por `GET /market/kline` (verificado):
`{"open": "60000", "high": "60001", "close": "60000", "low": "59989.2",
"time": 111111, "quoteVol": "1", "baseVol": "60000", "type": "LAST_PRICE"}`.

**Tarea 2/3 (correccion post-Fase-2)**: la version anterior de
`get_or_fetch` mezclaba descarga de red y lectura de cache en una sola
funcion, y su chequeo de "ya cubierto" era todo-o-nada: si el rango pedido
no estaba 100% cubierto, re-pedia por red el RANGO COMPLETO otra vez
(incluida la parte ya cacheada), desperdiciando cientos de solicitudes en
cada reintento. Se separa en dos funciones con responsabilidades distintas:

- `download_missing`: descarga por red SOLO la cola reciente (mas nuevo
  que lo cacheado) y la cabeza vieja (mas viejo que lo cacheado) que
  realmente falten. Cada pagina se guarda de inmediato -> interrumpir esta
  funcion a mitad de camino no pierde progreso, la siguiente llamada
  retoma desde donde quedo. Usada SOLO por `scripts/download_history.py`.
- `get_cached_or_raise`: lectura PURA de `ohlcv_cache`, sin red. Usada por
  el motor de backtest (`app/backtesting/engine.py`), que ya no debe tocar
  la red (ver docs/FASE2_BLOQUEO_RED.md) -- si el rango pedido no esta
  completo en cache, lanza `MissingHistoricalDataError` con instrucciones
  claras en vez de descargar nada silenciosamente.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.market.bitunix_rest import BitunixRestClient
from app.persistence.database import Database
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo

logger = get_logger(__name__)

PAGE_LIMIT = 200

# Una pagina vacia aislada no se acepta de inmediato como "fin real del
# historial": se reintenta la MISMA peticion hasta este numero de veces
# extra antes de asumirlo (evita marcar un piso falso por una respuesta
# vacia puntual -- no hay evidencia de que esto haya ocurrido nunca, pero
# es una salvaguarda barata; ver docs/FASE2_BLOQUEO_RED.md).
EMPTY_PAGE_RETRIES = 2

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


class MissingHistoricalDataError(RuntimeError):
    """El rango de velas pedido no esta completo en `ohlcv_cache`. El motor
    de backtest ya no descarga por red (tarea 3): corre primero
    `scripts/download_history.py` para llenar el cache."""


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


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).date().isoformat()


async def _fetch_kline_page(
    client: BitunixRestClient, symbol: str, interval: str, cursor: int, price_type: str
) -> list[dict]:
    for attempt in range(EMPTY_PAGE_RETRIES + 1):
        raw_bars = await client.get_kline(
            symbol=symbol, interval=interval, end_time=cursor, limit=PAGE_LIMIT,
            price_type=price_type,
        )
        if raw_bars:
            return raw_bars
        if attempt < EMPTY_PAGE_RETRIES:
            logger.warning(
                "%s %s %s: pagina vacia en end_time=%d (reintento %d/%d)",
                symbol, interval, price_type, cursor, attempt + 1, EMPTY_PAGE_RETRIES,
            )
    return []


async def _download_range(
    client: BitunixRestClient,
    db: Database,
    symbol: str,
    interval: str,
    price_type: str,
    range_start: int,
    range_end: int,
    floor: int | None,
) -> None:
    """Descarga hacia atras SOLO [range_start, range_end]. Guarda cada
    pagina de inmediato -> reanudable sin perder progreso."""
    if range_start > range_end:
        return
    if floor is not None and range_end < floor:
        return  # todo el rango pedido es anterior al piso real conocido

    cursor = range_end
    page_num = 0
    while cursor >= range_start:
        page_num += 1
        raw_bars = await _fetch_kline_page(client, symbol, interval, cursor, price_type)
        if not raw_bars:
            logger.info(
                "%s %s %s: historial agotado en Bitunix antes de %s (pagina %d)",
                symbol, interval, price_type, _fmt(range_start), page_num,
            )
            await ohlcv_repo.set_floor(db, symbol, interval, price_type, cursor)
            return

        bars = [_bar_from_raw(symbol, interval, price_type, r) for r in raw_bars]
        await ohlcv_repo.upsert_bars(db, bars)
        oldest_time = min(b.open_time for b in bars)
        logger.info(
            "%s %s %s: pagina %d guardada (%d velas, hasta %s)",
            symbol, interval, price_type, page_num, len(bars), _fmt(oldest_time),
        )

        if len(raw_bars) < PAGE_LIMIT:
            await ohlcv_repo.set_floor(db, symbol, interval, price_type, oldest_time)
            return
        if oldest_time <= range_start:
            return
        cursor = oldest_time - 1


async def download_missing(
    client: BitunixRestClient,
    db: Database,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    price_type: str = "LAST_PRICE",
) -> None:
    """Descarga por red SOLO lo que falte en cache para cubrir
    [start_time, end_time]: la cola reciente (mas nuevo que lo cacheado,
    hasta `end_time`) y la cabeza vieja (mas viejo que lo cacheado, hasta
    `start_time` o el piso real ya conocido). Usada exclusivamente por
    `scripts/download_history.py` -- el motor de backtest nunca llama
    esto, solo lee con `get_cached_or_raise`."""
    step_ms = interval_to_ms(interval)
    floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)

    if step_ms is None:
        # Sin grilla conocida (p.ej. "1M"): no se puede razonar sobre
        # rangos faltantes, se descarga el pedido completo tal cual.
        await _download_range(
            client, db, symbol, interval, price_type, start_time, end_time, floor
        )
        return

    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        await _download_range(
            client, db, symbol, interval, price_type, start_time, end_time, floor
        )
        return

    min_cached, max_cached = covered

    if max_cached < end_time - 2 * step_ms:
        await _download_range(
            client, db, symbol, interval, price_type, max_cached + step_ms, end_time, floor
        )
        floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)

    head_target = start_time if floor is None else max(start_time, floor)
    if min_cached > head_target:
        await _download_range(
            client, db, symbol, interval, price_type, start_time, min_cached - step_ms, floor
        )


async def check_series_availability(
    db: Database,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    price_type: str = "LAST_PRICE",
) -> str | None:
    """Verifica si [start_time, end_time] esta completo en `ohlcv_cache`
    SIN lanzar nada -- devuelve `None` si esta completo, o un mensaje
    describiendo que falta. Usada tanto por `get_cached_or_raise` (que
    lanza `MissingHistoricalDataError` con ese mismo mensaje) como por
    `scripts/run_backtest.py` para validar TODAS las series necesarias de
    antemano y listarlas juntas, antes de calcular nada (tarea 1a)."""
    step_ms = interval_to_ms(interval)
    if step_ms is None:
        return None

    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        return (
            f"No hay velas cacheadas para {symbol} {interval} {price_type}. "
            "Corre primero: python scripts/download_history.py"
        )

    min_cached, max_cached = covered
    floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)
    # Ademas del piso (que puede faltar aunque la cabeza SI este completa
    # -- bug real encontrado: una descarga interrumpida deja datos
    # cacheados sin que `ohlcv_floor` llegue a escribirse), se confia en
    # la marca afirmativa de "serie completa" que
    # `scripts/download_history.py` escribe al terminar sin errores.
    series_complete_start = await ohlcv_repo.get_series_complete_start(
        db, symbol, interval, price_type
    )
    head_ok = (
        min_cached <= start_time
        or (floor is not None and floor <= min_cached)
        or (series_complete_start is not None and series_complete_start <= start_time)
    )
    tail_ok = max_cached >= end_time - 2 * step_ms
    if head_ok and tail_ok:
        return None
    return (
        f"Velas incompletas para {symbol} {interval} {price_type} en rango "
        f"[{_fmt(start_time)}, {_fmt(end_time)}] (cacheado: "
        f"[{_fmt(min_cached)}, {_fmt(max_cached)}]). "
        "Corre primero: python scripts/download_history.py"
    )


async def get_cached_or_raise(
    db: Database,
    symbol: str,
    interval: str,
    start_time: int,
    end_time: int,
    price_type: str = "LAST_PRICE",
) -> list[OHLCVBar]:
    """Lee SOLO de `ohlcv_cache` (nunca toca la red). Si el rango pedido no
    esta completo, lanza `MissingHistoricalDataError` -- el llamador debe
    correr `scripts/download_history.py` primero."""
    problem = await check_series_availability(
        db, symbol, interval, start_time, end_time, price_type
    )
    if problem is not None:
        raise MissingHistoricalDataError(problem)
    return await ohlcv_repo.get_bars(db, symbol, interval, price_type, start_time, end_time)
