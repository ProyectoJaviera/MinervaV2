"""Tests de `app/market/ohlcv_history.py`.

Cubre: los dos bugs reales de la corrida de Fase 2 (price_type mal
etiquetado, redescarga completa por un chequeo todo-o-nada), y la
reescritura incremental/reanudable de la tarea 2 (descarga SOLO cola/
cabeza faltante, resiste una interrupcion a mitad de pagina, reintenta
paginas vacias antes de aceptar fin de historial) mas la separacion
descarga/lectura de la tarea 3 (`get_cached_or_raise` nunca toca la red).
"""

from __future__ import annotations

import pytest

from app.market.ohlcv_history import (
    MissingHistoricalDataError,
    download_missing,
    drop_incomplete_last_bar,
    get_cached_or_raise,
    interval_to_ms,
)
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo

STEP_MS = interval_to_ms("4h")
BASE_MS = 1_700_000_000_000


def _raw_bar(idx: int, price_type: str = "LAST_PRICE", include_type_field: bool = True) -> dict:
    bar = {
        "open": "100", "high": "101", "low": "99", "close": "100.5",
        "time": BASE_MS + idx * STEP_MS, "baseVol": "1", "quoteVol": "1",
    }
    if include_type_field:
        bar["type"] = price_type
    return bar


class CountingRestClient:
    """Cuenta las llamadas de red para verificar que el cache evita
    redescargas innecesarias. `empty_pages` simula paginas vacias
    puntuales en los `end_time` dados antes de responder con datos reales."""

    def __init__(
        self, bars_by_type: dict[str, list[dict]], empty_at: set[int] | None = None
    ) -> None:
        self.bars_by_type = bars_by_type
        self.calls = 0
        self.empty_at = empty_at or set()
        self._served_empty_at: set[int] = set()

    async def get_kline(
        self, symbol, interval, start_time=None, end_time=None, limit=100, price_type="LAST_PRICE"
    ):
        self.calls += 1
        if end_time in self.empty_at and end_time not in self._served_empty_at:
            self._served_empty_at.add(end_time)
            return []
        source = self.bars_by_type.get(price_type, [])
        filtered = sorted((b for b in source if b["time"] <= end_time), key=lambda b: b["time"])
        return filtered[-limit:]


@pytest.mark.asyncio
async def test_price_type_assigned_from_request_not_from_response_field(db):
    """Bug 1: aunque la API no incluya 'type' en la respuesta (o lo incluya
    mal), el bar guardado debe quedar etiquetado con el price_type
    SOLICITADO."""
    raw = [_raw_bar(i, include_type_field=False) for i in range(5)]
    client = CountingRestClient({"MARK_PRICE": raw})

    end = BASE_MS + 4 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "MARK_PRICE")
    bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "MARK_PRICE")

    assert len(bars) == 5
    assert all(b.price_type == "MARK_PRICE" for b in bars)


@pytest.mark.asyncio
async def test_last_price_and_mark_price_dont_collide_in_cache(db):
    last_raw = [_raw_bar(i, "LAST_PRICE", include_type_field=False) for i in range(5)]
    mark_raw = [_raw_bar(i, "MARK_PRICE", include_type_field=False) for i in range(5)]
    client = CountingRestClient({"LAST_PRICE": last_raw, "MARK_PRICE": mark_raw})

    end = BASE_MS + 4 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "MARK_PRICE")
    last_bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    mark_bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "MARK_PRICE")

    assert len(last_bars) == 5
    assert len(mark_bars) == 5  # bug 1 hacia que esto quedara en 0


@pytest.mark.asyncio
async def test_subrange_within_already_cached_data_hits_cache_not_network(db):
    """Bug 2: pedir un sub-rango (p.ej. un corte OOS) que ya esta cubierto
    por una descarga anterior no debe volver a llamar a la red."""
    raw = [_raw_bar(i) for i in range(20)]
    client = CountingRestClient({"LAST_PRICE": raw})

    full_end = BASE_MS + 19 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, full_end, "LAST_PRICE")
    calls_after_full_fetch = client.calls
    assert calls_after_full_fetch > 0

    sub_start = BASE_MS + int(7.3 * STEP_MS)
    sub_end = BASE_MS + 15 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", sub_start, sub_end, "LAST_PRICE")
    sub_bars = await get_cached_or_raise(db, "BTCUSDT", "4h", sub_start, sub_end, "LAST_PRICE")

    assert client.calls == calls_after_full_fetch  # sin llamadas de red adicionales
    assert all(b.open_time >= sub_start for b in sub_bars)


@pytest.mark.asyncio
async def test_start_time_before_real_floor_is_remembered_and_not_refetched(db):
    """Una vez detectado el piso real del historial (la API devuelve menos
    de `limit` o una pagina vacia), pedir un `start_time` anterior no debe
    volver a golpear la red."""
    raw = [_raw_bar(i) for i in range(10)]  # menos de PAGE_LIMIT -> piso real = bar 0
    client = CountingRestClient({"LAST_PRICE": raw})

    end = BASE_MS + 9 * STEP_MS
    very_early_start = BASE_MS - 1000 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", very_early_start, end, "LAST_PRICE")
    calls_first = client.calls
    assert calls_first > 0

    await download_missing(client, db, "BTCUSDT", "4h", very_early_start, end, "LAST_PRICE")
    assert client.calls == calls_first  # la segunda vez no toca la red


@pytest.mark.asyncio
async def test_only_downloads_missing_tail_not_whole_range_again(db):
    """Tarea 2: si ya hay datos cacheados para la parte vieja del rango y
    solo falta la cola reciente, se descarga SOLO esa cola -- no todo el
    rango desde `end_time` otra vez."""
    raw = [_raw_bar(i) for i in range(40)]
    client = CountingRestClient({"LAST_PRICE": raw})

    # Primero, solo la mitad vieja.
    old_end = BASE_MS + 19 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, old_end, "LAST_PRICE")
    calls_for_first_half = client.calls
    assert calls_for_first_half > 0

    # Ahora se pide hasta el final: solo deberia pedir la cola nueva (20-39).
    full_end = BASE_MS + 39 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, full_end, "LAST_PRICE")
    extra_calls = client.calls - calls_for_first_half
    assert 0 < extra_calls <= calls_for_first_half  # bastante menos que redescargar todo

    all_bars = await get_cached_or_raise(
        db, "BTCUSDT", "4h", BASE_MS, BASE_MS + 39 * STEP_MS, "LAST_PRICE"
    )
    assert len(all_bars) == 40


@pytest.mark.asyncio
async def test_only_downloads_missing_head_not_whole_range_again(db):
    """Simetrico: si falta la cabeza vieja pero la cola reciente ya esta
    cacheada, se descarga SOLO la cabeza. Usa 250 velas (mas de un
    PAGE_LIMIT=200) para que una descarga de "cola" realista no traiga de
    paso TODO el historial en una sola pagina, como pasaria con un dataset
    de prueba mas chico que un solo page."""
    raw = [_raw_bar(i) for i in range(250)]
    client = CountingRestClient({"LAST_PRICE": raw})

    # "Cola": una sola pagina de 200 velas termina cubriendo 50-249.
    await download_missing(
        client, db, "BTCUSDT", "4h", BASE_MS + 200 * STEP_MS, BASE_MS + 249 * STEP_MS, "LAST_PRICE"
    )
    calls_for_tail = client.calls
    assert calls_for_tail > 0

    # Pedir el rango completo solo deberia traer la cabeza faltante (0-49).
    full_end = BASE_MS + 249 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, full_end, "LAST_PRICE")
    extra_calls = client.calls - calls_for_tail
    assert extra_calls > 0

    all_bars = await get_cached_or_raise(
        db, "BTCUSDT", "4h", BASE_MS, BASE_MS + 249 * STEP_MS, "LAST_PRICE"
    )
    assert len(all_bars) == 250


@pytest.mark.asyncio
async def test_resumes_after_simulated_interruption_without_redownloading_saved_pages(db):
    """Reanudable: si una descarga anterior guardo algunas paginas y se
    interrumpio, la siguiente llamada retoma desde donde quedo (no vuelve a
    pedir lo ya guardado)."""
    raw = [_raw_bar(i) for i in range(30)]
    client = CountingRestClient({"LAST_PRICE": raw})

    # Simula una descarga parcial anterior: solo las velas mas recientes (20-29)
    # ya quedaron guardadas (p.ej. el proceso se interrumpio a mitad de camino).
    partial_bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
            open_time=BASE_MS + i * STEP_MS, open=100, high=101, low=99, close=100.5,
        )
        for i in range(20, 30)
    ]
    await ohlcv_repo.upsert_bars(db, partial_bars)

    end = BASE_MS + 29 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")

    all_bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    assert len(all_bars) == 30
    # Solo hizo falta descargar la cabeza faltante (0-19): unas pocas
    # paginas, no las 30 velas completas de nuevo.
    assert client.calls <= 2


@pytest.mark.asyncio
async def test_empty_page_is_retried_before_being_treated_as_end_of_history(db):
    """Tarea 1/2: una pagina vacia aislada no se acepta de inmediato como
    fin real del historial -- se reintenta antes de fijar el piso."""
    raw = [_raw_bar(i) for i in range(10)]
    end = BASE_MS + 9 * STEP_MS
    # La primera respuesta en `end` es vacia (simulando un hueco puntual),
    # pero el reintento si trae datos.
    client = CountingRestClient({"LAST_PRICE": raw}, empty_at={end})

    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")

    assert len(bars) == 10  # se recupero tras el reintento, no se trato como piso falso


@pytest.mark.asyncio
async def test_get_cached_or_raise_raises_when_nothing_cached(db):
    with pytest.raises(MissingHistoricalDataError):
        await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, BASE_MS + 4 * STEP_MS, "LAST_PRICE")


@pytest.mark.asyncio
async def test_get_cached_or_raise_raises_when_range_partially_covered(db):
    partial_bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
            open_time=BASE_MS + i * STEP_MS, open=100, high=101, low=99, close=100.5,
        )
        for i in range(5)
    ]
    await ohlcv_repo.upsert_bars(db, partial_bars)

    with pytest.raises(MissingHistoricalDataError):
        end = BASE_MS + 19 * STEP_MS
        await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")


@pytest.mark.asyncio
async def test_get_cached_or_raise_succeeds_when_range_fully_covered(db):
    raw = [_raw_bar(i) for i in range(10)]
    client = CountingRestClient({"LAST_PRICE": raw})
    end = BASE_MS + 9 * STEP_MS
    await download_missing(client, db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")

    bars = await get_cached_or_raise(db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    assert len(bars) == 10


@pytest.mark.asyncio
async def test_get_cached_or_raise_raises_when_floor_missing_despite_complete_head(db):
    """Reproduce el bug real: datos cacheados que en los hechos SI cubren
    la cabeza pedida, pero sin fila en `ohlcv_floor` (p.ej. una descarga
    interrumpida) -- sin la marca de "serie completa", debe seguir
    lanzando (es exactamente el caso que causo el `MissingHistoricalDataError`
    evitable en produccion)."""
    bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
            open_time=BASE_MS + i * STEP_MS, open=100, high=101, low=99, close=100.5,
        )
        for i in range(5, 15)  # cubre desde el indice 5, NO desde 0
    ]
    await ohlcv_repo.upsert_bars(db, bars)
    # Sin set_floor y sin mark_series_complete: la cabeza (indice 0) no
    # esta demostrada como completa.
    start = BASE_MS  # pide desde el indice 0
    end = BASE_MS + 14 * STEP_MS

    with pytest.raises(MissingHistoricalDataError):
        await get_cached_or_raise(db, "BTCUSDT", "4h", start, end, "LAST_PRICE")


@pytest.mark.asyncio
async def test_series_complete_marker_resolves_missing_floor_row(db):
    """Misma situacion que el test anterior, pero esta vez
    `download_history.py` SI llego a marcar la serie como completa (sin
    excepciones) -- `get_cached_or_raise` debe confiar en eso aunque
    `ohlcv_floor` siga sin fila."""
    bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
            open_time=BASE_MS + i * STEP_MS, open=100, high=101, low=99, close=100.5,
        )
        for i in range(5, 15)
    ]
    await ohlcv_repo.upsert_bars(db, bars)
    start = BASE_MS
    end = BASE_MS + 14 * STEP_MS
    await ohlcv_repo.mark_series_complete(db, "BTCUSDT", "4h", "LAST_PRICE", start, end)

    result = await get_cached_or_raise(db, "BTCUSDT", "4h", start, end, "LAST_PRICE")
    assert len(result) == 10


@pytest.mark.asyncio
async def test_series_complete_marker_does_not_mask_a_tail_gap(db):
    """La marca de "serie completa" solo se usa para el extremo de cabeza
    (dato antiguo) -- un hueco en la cola (dato reciente faltante) debe
    seguir lanzando igual."""
    bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
            open_time=BASE_MS + i * STEP_MS, open=100, high=101, low=99, close=100.5,
        )
        for i in range(10)  # solo hasta el indice 9
    ]
    await ohlcv_repo.upsert_bars(db, bars)
    await ohlcv_repo.mark_series_complete(
        db, "BTCUSDT", "4h", "LAST_PRICE", BASE_MS, BASE_MS + 9 * STEP_MS
    )

    with pytest.raises(MissingHistoricalDataError):
        # Pide hasta mucho mas alla de lo cacheado -- la cola SI falta.
        await get_cached_or_raise(
            db, "BTCUSDT", "4h", BASE_MS, BASE_MS + 100 * STEP_MS, "LAST_PRICE"
        )


def test_drop_incomplete_last_bar_excludes_unclosed_candle():
    now_ms = BASE_MS + 2 * STEP_MS + 1000  # la vela 2 recien abrio, no cerro
    bars = [
        OHLCVBar(symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
                 open_time=BASE_MS + i * STEP_MS, open=1, high=1, low=1, close=1)
        for i in range(3)
    ]
    result = drop_incomplete_last_bar(bars, "4h", now_ms)
    assert len(result) == 2
    assert all(b.open_time + STEP_MS <= now_ms for b in result)


def test_drop_incomplete_last_bar_keeps_all_when_closed():
    now_ms = BASE_MS + 10 * STEP_MS
    bars = [
        OHLCVBar(symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
                 open_time=BASE_MS + i * STEP_MS, open=1, high=1, low=1, close=1)
        for i in range(3)
    ]
    result = drop_incomplete_last_bar(bars, "4h", now_ms)
    assert len(result) == 3
