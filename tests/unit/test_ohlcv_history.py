"""Tests de `app/market/ohlcv_history.py` -- cubre dos bugs reales
encontrados durante la corrida real de Fase 2 (sin tests hasta ahora):

1. `price_type` se asignaba leyendo el campo `type` de la respuesta de la
   API, que no siempre esta presente (p.ej. ausente en varias respuestas
   de MARK_PRICE observadas en vivo) -- mezclaba LAST_PRICE y MARK_PRICE
   en el mismo cache.
2. El chequeo de "ya esta en cache" comparaba contra una grilla aritmetica
   exacta desde `start_time`, que nunca coincide con los `open_time` reales
   de Bitunix cuando `start_time` es arbitrario (p.ej. un corte IS/OOS) --
   forzaba una redescarga completa de red en cada llamada.
"""

from __future__ import annotations

import pytest

from app.market.ohlcv_history import drop_incomplete_last_bar, get_or_fetch, interval_to_ms
from app.persistence.models import OHLCVBar

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
    redescargas innecesarias."""

    def __init__(self, bars_by_type: dict[str, list[dict]]) -> None:
        self.bars_by_type = bars_by_type
        self.calls = 0

    async def get_kline(
        self, symbol, interval, start_time=None, end_time=None, limit=100, price_type="LAST_PRICE"
    ):
        self.calls += 1
        source = self.bars_by_type.get(price_type, [])
        filtered = sorted((b for b in source if b["time"] <= end_time), key=lambda b: b["time"])
        return filtered[-limit:]


@pytest.mark.asyncio
async def test_price_type_assigned_from_request_not_from_response_field(db):
    """Bug 1: aunque la API no incluya 'type' en la respuesta (o lo incluya
    mal), el bar guardado debe quedar etiquetado con el price_type
    SOLICITADO."""
    raw = [_raw_bar(i, include_type_field=False) for i in range(5)]  # sin campo 'type'
    client = CountingRestClient({"MARK_PRICE": raw})

    bars = await get_or_fetch(
        client, db, "BTCUSDT", "4h", BASE_MS, BASE_MS + 4 * STEP_MS, "MARK_PRICE"
    )

    assert len(bars) == 5
    assert all(b.price_type == "MARK_PRICE" for b in bars)


@pytest.mark.asyncio
async def test_last_price_and_mark_price_dont_collide_in_cache(db):
    last_raw = [_raw_bar(i, "LAST_PRICE", include_type_field=False) for i in range(5)]
    mark_raw = [_raw_bar(i, "MARK_PRICE", include_type_field=False) for i in range(5)]
    client = CountingRestClient({"LAST_PRICE": last_raw, "MARK_PRICE": mark_raw})

    end = BASE_MS + 4 * STEP_MS
    last_bars = await get_or_fetch(client, db, "BTCUSDT", "4h", BASE_MS, end, "LAST_PRICE")
    mark_bars = await get_or_fetch(client, db, "BTCUSDT", "4h", BASE_MS, end, "MARK_PRICE")

    assert len(last_bars) == 5
    assert len(mark_bars) == 5  # bug 1 hacia que esto quedara en 0


@pytest.mark.asyncio
async def test_subrange_within_already_cached_data_hits_cache_not_network(db):
    """Bug 2: pedir un sub-rango (p.ej. un corte OOS) que ya esta cubierto
    por una descarga anterior no debe volver a llamar a la red."""
    raw = [_raw_bar(i) for i in range(20)]
    client = CountingRestClient({"LAST_PRICE": raw})

    full_end = BASE_MS + 19 * STEP_MS
    await get_or_fetch(client, db, "BTCUSDT", "4h", BASE_MS, full_end, "LAST_PRICE")
    calls_after_full_fetch = client.calls
    assert calls_after_full_fetch > 0

    # Sub-rango con un start_time ARBITRARIO (no alineado a ninguna vela
    # real) -- antes del fix esto forzaba una redescarga completa.
    sub_start = BASE_MS + int(7.3 * STEP_MS)
    sub_end = BASE_MS + 15 * STEP_MS
    sub_bars = await get_or_fetch(client, db, "BTCUSDT", "4h", sub_start, sub_end, "LAST_PRICE")

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
    very_early_start = BASE_MS - 1000 * STEP_MS  # mucho antes del piso real
    await get_or_fetch(client, db, "BTCUSDT", "4h", very_early_start, end, "LAST_PRICE")
    calls_first = client.calls
    assert calls_first > 0

    await get_or_fetch(client, db, "BTCUSDT", "4h", very_early_start, end, "LAST_PRICE")
    assert client.calls == calls_first  # la segunda vez no toca la red


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
