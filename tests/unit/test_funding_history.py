"""Tests de `app/market/funding_history.py` -- descarga incremental y
reanudable de funding, misma logica que `ohlcv_history.py` (tarea 2)."""

from __future__ import annotations

import pytest

from app.market.funding_history import download_missing_funding
from app.persistence.repositories import funding_repo

BASE_MS = 1_700_000_000_000
STEP_MS = 8 * 60 * 60 * 1000  # 8h, intervalo tipico de funding


def _raw(idx: int) -> dict:
    return {"fundingTime": BASE_MS + idx * STEP_MS, "fundingRate": "0.0001"}


class CountingFundingClient:
    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.calls = 0

    async def get_funding_rate_history(self, symbol, start_time=None, end_time=None, limit=100):
        self.calls += 1
        filtered = sorted(
            (e for e in self.events if e["fundingTime"] <= end_time),
            key=lambda e: e["fundingTime"],
        )
        return filtered[-limit:]


@pytest.mark.asyncio
async def test_downloads_only_missing_tail(db):
    events = [_raw(i) for i in range(20)]
    client = CountingFundingClient(events)

    end_first = BASE_MS + 9 * STEP_MS
    await download_missing_funding(client, db, "BTCUSDT", BASE_MS, end_first)
    calls_first = client.calls
    assert calls_first > 0

    end_full = BASE_MS + 19 * STEP_MS
    await download_missing_funding(client, db, "BTCUSDT", BASE_MS, end_full)
    assert client.calls > calls_first  # tuvo que pedir la cola nueva

    funding = await funding_repo.get_funding(db, "BTCUSDT", BASE_MS, end_full)
    assert len(funding) == 20


@pytest.mark.asyncio
async def test_subrange_already_cached_hits_no_network(db):
    events = [_raw(i) for i in range(20)]
    client = CountingFundingClient(events)

    full_end = BASE_MS + 19 * STEP_MS
    await download_missing_funding(client, db, "BTCUSDT", BASE_MS, full_end)
    calls_after_full = client.calls

    await download_missing_funding(client, db, "BTCUSDT", BASE_MS, full_end)
    assert client.calls == calls_after_full  # ya esta todo cacheado


@pytest.mark.asyncio
async def test_empty_page_is_retried_before_accepting_floor(db):
    events = [_raw(i) for i in range(10)]
    end = BASE_MS + 9 * STEP_MS

    class FlakyClient(CountingFundingClient):
        def __init__(self, events):
            super().__init__(events)
            self._served_empty = False

        async def get_funding_rate_history(self, symbol, start_time=None, end_time=None, limit=100):
            if end_time == end and not self._served_empty:
                self._served_empty = True
                self.calls += 1
                return []
            return await super().get_funding_rate_history(symbol, start_time, end_time, limit)

    client = FlakyClient(events)
    await download_missing_funding(client, db, "BTCUSDT", BASE_MS, end)

    funding = await funding_repo.get_funding(db, "BTCUSDT", BASE_MS, end)
    assert len(funding) == 10
