"""Descarga incremental y reanudable de velas, con cache local.

Respeta el limite de Bitunix (200 velas/llamada, paginacion hacia ATRAS
desde `end_time` -- verificado empiricamente, ver docs/FASE2_PLAN.md) y
descarga SOLO lo que falta en `ohlcv_cache`, nunca el rango completo de
nuevo.

Formato de cada vela devuelto por `GET /market/kline` (verificado):
`{"open": "60000", "high": "60001", "close": "60000", "low": "59989.2",
"time": 111111, "quoteVol": "1", "baseVol": "60000", "type": "LAST_PRICE"}`.

**Semantica de `endTime`, verificada empiricamente el 2026-10-10** contra la
API real (publica, sin credenciales): es **estrictamente EXCLUSIVA sobre
`open_time`** (`open_time < endTime`), no sobre el cierre de la vela ni
inclusiva. Se probo con `endTime` alineado a una vela, a mitad de vela, y 1 ms
antes de que una vela cerrara; los tres casos son consistentes solo con esa
regla (ver `docs/FASE2_INTEGRIDAD_VELAS.md`). Por eso `cursor = oldest_time -
1` en `_download_range` (un paso hacia atras de 1 ms, no de una vela
completa) YA es correcto: excluye exactamente la vela ya guardada e incluye
la anterior.

**Pero una paginacion matematicamente correcta no alcanza.** Se encontro (y
se reprodujo mas de una vez, en vivo) que Bitunix a veces omite UNA vela real
en una respuesta con `limit=200` sin `startTime` (el patron exacto de
`_download_range`) que SI esta presente si se pide esa misma vela con una
ventana chica (`limit` bajo). No se identifico una regla determinista para
cuando pasa, ni hay documentacion oficial de Bitunix sobre esto que se haya
podido consultar -- asi que en vez de confiar en la formula de paginacion
sola, `download_missing` verifica la serie resultante con `find_gaps` y
repara lo que falte con `fill_gaps` (pedidos puntuales y estrechos, que en
todas las pruebas si devolvieron la vela real).

**Tarea 2/3 (correccion post-Fase-2)**: la version anterior de
`get_or_fetch` mezclaba descarga de red y lectura de cache en una sola
funcion, y su chequeo de "ya cubierto" era todo-o-nada: si el rango pedido
no estaba 100% cubierto, re-pedia por red el RANGO COMPLETO otra vez
(incluida la parte ya cacheada), desperdiciando cientos de solicitudes en
cada reintento. Se separa en dos funciones con responsabilidades distintas:

- `download_missing`: descarga por red SOLO la cola reciente (mas nuevo
  que lo cacheado) y la cabeza vieja (mas viejo que lo cacheado) que
  realmente falten, y verifica/repara huecos al final. Cada pagina se
  guarda de inmediato -> interrumpir esta funcion a mitad de camino no
  pierde progreso, la siguiente llamada retoma desde donde quedo. La usan
  `scripts/download_history.py` y, para velas de 1 minuto, la
  reconciliacion al reiniciar (`app/trading/position_monitor.py`).
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


GAP_FILL_MARGIN_BARS = 3
GAP_FILL_LIMIT = 20
# Cuantas velas de hueco entran en una sola llamada de `fill_gaps`, dejando
# margen de `GAP_FILL_MARGIN_BARS` de cada lado sin pasar de `GAP_FILL_LIMIT`
# en total. Una racha contigua mas larga que esto se reintenta en varias
# llamadas (nunca se trunca en silencio: lo que no entre en una llamada entra
# en la siguiente, ver `fill_gaps`).
GAP_FILL_CHUNK_BARS = GAP_FILL_LIMIT - 2 * GAP_FILL_MARGIN_BARS
# Cuanto se espera antes de reintentar un hueco ya confirmado irreparable
# (`ohlcv_unrepairable_gaps`) -- evita golpear la API cada ciclo del
# generador de señales por un hueco permanente (incidente de estabilidad
# 2026-10-10, "Añadido 0"; ver docs/FASE2_INTEGRIDAD_VELAS.md).
GAP_RETRY_COOLDOWN_HOURS = 24.0


def find_gaps(
    open_times: list[int], step_ms: int, start: int | None = None, end: int | None = None
) -> list[int]:
    """`open_time` que faltan en una serie cacheada, dado el paso `step_ms`.
    Por defecto revisa entre el minimo y el maximo de `open_times`; si se
    pasan `start`/`end` (alineados a la grilla) revisa ESE rango en vez del
    que resulte de los datos -- para detectar un hueco justo en el borde de
    lo pedido, no solo entre lo que ya este cacheado."""
    times = set(open_times)
    lo = start if start is not None else (min(times) if times else None)
    hi = end if end is not None else (max(times) if times else None)
    if lo is None or hi is None or lo > hi:
        return []
    return [t for t in range(lo, hi + 1, step_ms) if t not in times]


async def fill_gaps(
    client: BitunixRestClient,
    db: Database,
    symbol: str,
    interval: str,
    price_type: str,
    gap_times: list[int],
    step_ms: int,
) -> list[int]:
    """Repara huecos puntuales con pedidos ESTRECHOS (ventana chica, `limit`
    bajo: ver la nota de modulo sobre la vela que Bitunix a veces omite en
    una respuesta de `limit=200`). Agrupa huecos contiguos para no gastar una
    llamada por minuto. Devuelve los `open_time` que se lograron recuperar
    (puede ser un subconjunto de `gap_times`, si alguno sigue sin aparecer
    incluso con la ventana chica)."""
    if not gap_times:
        return []
    gap_times = sorted(gap_times)
    runs: list[list[int]] = []
    for t in gap_times:
        if runs and t - runs[-1][-1] == step_ms:
            runs[-1].append(t)
        else:
            runs.append([t])

    recovered: list[int] = []
    for run in runs:
        # Una racha mas larga que lo que entra en una sola llamada (con
        # margen) se parte en varios pedidos -- nunca se trunca en silencio.
        for i in range(0, len(run), GAP_FILL_CHUNK_BARS):
            chunk = run[i : i + GAP_FILL_CHUNK_BARS]
            start_time = chunk[0] - GAP_FILL_MARGIN_BARS * step_ms
            end_time = chunk[-1] + (GAP_FILL_MARGIN_BARS + 1) * step_ms
            limit = len(chunk) + 2 * GAP_FILL_MARGIN_BARS  # <= GAP_FILL_LIMIT por construccion
            raw_bars = await client.get_kline(
                symbol=symbol, interval=interval, start_time=start_time, end_time=end_time,
                limit=limit, price_type=price_type,
            )
            bars = [_bar_from_raw(symbol, interval, price_type, r) for r in raw_bars]
            if bars:
                await ohlcv_repo.upsert_bars(db, bars)
            fetched = {b.open_time for b in bars}
            recovered.extend(t for t in chunk if t in fetched)
    return recovered


def _align_up(ms: int, anchor: int, step_ms: int) -> int:
    """Redondea `ms` hacia arriba al primer punto de la grilla `anchor,
    anchor+step,...` (NO a la grilla de epoca 0): `anchor` es siempre un
    `open_time` real ya cacheado (`min_cached`), asi esto funciona aunque la
    serie no caiga en la grilla de epoca -- las velas reales de Bitunix si
    caen ahi (verificado empiricamente), pero anclar a un dato real en vez
    de asumirlo evita huecos falsos si alguna vez no fuera asi."""
    if ms <= anchor:
        return anchor
    steps = -(-(ms - anchor) // step_ms)
    return anchor + steps * step_ms


def _align_down(ms: int, anchor: int, step_ms: int) -> int:
    if ms <= anchor:
        return anchor
    steps = (ms - anchor) // step_ms
    return anchor + steps * step_ms


async def _verify_and_repair(
    client: BitunixRestClient, db: Database, symbol: str, interval: str,
    price_type: str, step_ms: int, start_time: int, end_time: int,
) -> None:
    """Verifica que la VENTANA PEDIDA (`[start_time, end_time]`, acotada a lo
    que de verdad esta cacheado) este densa, y repara lo que falte -- ver la
    nota de modulo. Se llama al final de `download_missing`, tanto si hizo
    falta pedir algo por red como si no (asi una cache ya guardada con
    huecos de antes se autorepara en la siguiente llamada normal, no solo
    con una reparacion manual).

    Revisa SOLO lo pedido, no toda la serie cacheada: `download_missing` la
    llama el generador de señales en cada ciclo (`app/trading/
    signal_generator.py`), y un hueco permanente de hace años (ver seccion 5
    de docs/FASE2_INTEGRIDAD_VELAS.md) en una parte de la serie que nadie
    pidio no tiene por que reintentarse ahi.

    Un hueco que YA se confirmo irreparable (`ohlcv_unrepairable_gaps`) no se
    reintenta hasta que pasen `GAP_RETRY_COOLDOWN_HOURS` desde el ultimo
    intento -- y el WARNING de "no se pudo reparar" se registra una sola vez,
    al confirmarlo por primera vez; los reintentos posteriores que sigan
    fallando quedan en INFO, no en WARNING."""
    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        return
    min_cached, max_cached = covered
    lo = max(_align_up(start_time, min_cached, step_ms), min_cached)
    hi = min(_align_down(end_time, min_cached, step_ms), max_cached)
    if lo > hi:
        return

    bars = await ohlcv_repo.get_bars(db, symbol, interval, price_type, lo, hi)
    gaps = find_gaps([b.open_time for b in bars], step_ms, lo, hi)
    if not gaps:
        return

    now = datetime.now(UTC)
    known = await ohlcv_repo.get_unrepairable_gaps(db, symbol, interval, price_type)
    to_retry = []
    for g in gaps:
        last_attempt = known.get(g)
        if last_attempt is None:
            to_retry.append(g)
            continue
        age_hours = (now - last_attempt).total_seconds() / 3600
        if age_hours >= GAP_RETRY_COOLDOWN_HOURS:
            to_retry.append(g)
    if not to_retry:
        return  # todos los huecos de la ventana ya son conocidos y en cooldown

    recovered = await fill_gaps(client, db, symbol, interval, price_type, to_retry, step_ms)
    if recovered:
        logger.info(
            "%s %s %s: %d hueco(s) detectado(s) en la ventana pedida, %d reparado(s)",
            symbol, interval, price_type, len(to_retry), len(recovered),
        )
    for g in recovered:
        if g in known:
            await ohlcv_repo.clear_unrepairable_gap(db, symbol, interval, price_type, g)
            logger.info(
                "%s %s %s: hueco en %s recuperado tras reintento", symbol, interval, price_type,
                _fmt(g),
            )

    still_missing = sorted(set(to_retry) - set(recovered))
    for g in still_missing:
        is_new = g not in known
        await ohlcv_repo.mark_unrepairable_gap(db, symbol, interval, price_type, g, now)
        if is_new:
            logger.warning(
                "%s %s %s: hueco en %s no se pudo reparar ni con pedido estrecho (confirmado "
                "irreparable; no se reintentara por %.0fh)",
                symbol, interval, price_type, _fmt(g), GAP_RETRY_COOLDOWN_HOURS,
            )
        else:
            logger.info(
                "%s %s %s: hueco en %s sigue sin repararse tras reintento",
                symbol, interval, price_type, _fmt(g),
            )


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
    `start_time` o el piso real ya conocido); al final verifica y repara
    huecos puntuales (`_verify_and_repair`, ver la nota de modulo). La usan
    `scripts/download_history.py` y, para velas de 1 minuto, la
    reconciliacion al reiniciar -- el motor de backtest nunca llama esto,
    solo lee con `get_cached_or_raise`."""
    step_ms = interval_to_ms(interval)
    floor = await ohlcv_repo.get_floor(db, symbol, interval, price_type)

    if step_ms is None:
        # Sin grilla conocida (p.ej. "1M"): no se puede razonar sobre
        # rangos faltantes ni verificar huecos, se descarga el pedido
        # completo tal cual.
        await _download_range(
            client, db, symbol, interval, price_type, start_time, end_time, floor
        )
        return

    covered = await ohlcv_repo.get_covered_range(db, symbol, interval, price_type)
    if covered is None:
        await _download_range(
            client, db, symbol, interval, price_type, start_time, end_time, floor
        )
        await _verify_and_repair(
            client, db, symbol, interval, price_type, step_ms, start_time, end_time
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

    await _verify_and_repair(
        client, db, symbol, interval, price_type, step_ms, start_time, end_time
    )


async def _warn_internal_gaps(
    db: Database, symbol: str, interval: str, price_type: str, start_time: int, end_time: int,
    step_ms: int, min_cached: int, max_cached: int,
) -> None:
    """Solo informa (INFO/WARNING), nunca lanza ni cambia el resultado de
    `check_series_availability`: una serie puede tener los BORDES completos (lo
    unico que esa funcion exige) y aun asi huecos SUELTOS adentro -- exactamente
    lo que paso con los 236 huecos de Fase 2 (ZECUSDT/LINKUSDT, 2023, ver seccion
    5 de docs/FASE2_INTEGRIDAD_VELAS.md), que pasaron desapercibidos porque nadie
    miraba la densidad interna, solo la cobertura de punta a punta."""
    lo = max(_align_up(start_time, min_cached, step_ms), min_cached)
    hi = min(_align_down(end_time, min_cached, step_ms), max_cached)
    if lo > hi:
        return
    bars = await ohlcv_repo.get_bars(db, symbol, interval, price_type, lo, hi)
    gaps = find_gaps([b.open_time for b in bars], step_ms, lo, hi)
    if gaps:
        logger.warning(
            "%s %s %s: %d hueco(s) interno(s) en [%s, %s] (bordes completos, pero faltan "
            "velas sueltas adentro)",
            symbol, interval, price_type, len(gaps), _fmt(lo), _fmt(hi),
        )
    else:
        logger.info(
            "%s %s %s: sin huecos internos en [%s, %s]", symbol, interval, price_type,
            _fmt(lo), _fmt(hi),
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
        await _warn_internal_gaps(
            db, symbol, interval, price_type, start_time, end_time, step_ms,
            min_cached, max_cached,
        )
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
