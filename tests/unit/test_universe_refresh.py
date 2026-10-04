"""Antigüedad del universo, refresco diario y degradacion (arranque del bot, previo a 3.6).

El refresco nunca bloquea cierres: corre como tarea aparte y, si falla, conserva el
snapshot anterior y registra el error."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.core.logging import setup_logging
from app.market.coingecko_client import CoinGeckoApiError
from app.market.universe import (
    LAST_REFRESH_ERROR_KEY,
    UniverseRefresher,
    is_universe_stale,
    refresh_universe_safely,
)
from app.persistence.models import AssetUniverseEntry
from app.persistence.repositories import system_state_repo, universe_repo
from app.trading.signal_generator import run_signal_generation_cycle


def make_settings(**overrides) -> Settings:
    defaults = dict(
        UNIVERSE_SIZE=10, UNIVERSE_STALENESS_HOURS=48.0, UNIVERSE_REFRESH_HOURS=24.0,
        UNIVERSE_PRICE_SANITY_TOLERANCE_PCT=0.05, UNIVERSE_EXCLUDE_CATEGORIES="stablecoins",
        UNIVERSE_MANUAL_EXCLUSIONS="", UNIVERSE_CANDIDATE_POOL=30,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def entry(symbol: str, refreshed_at: datetime, included: bool = True) -> AssetUniverseEntry:
    return AssetUniverseEntry(
        refreshed_at=refreshed_at, coingecko_id=symbol.lower(), symbol=symbol,
        coingecko_rank=1, included=included,
    )


class FakeCoinGecko:
    def __init__(self, markets: list[dict] | None = None, fail: bool = False) -> None:
        self.markets = markets if markets is not None else []
        self.fail = fail

    async def get_markets(self, per_page: int = 30, category: str | None = None, **_):
        if self.fail:
            raise CoinGeckoApiError("CoinGecko caido (simulado)")
        return [] if category else self.markets


class FakeBitunix:
    def __init__(self, symbols: list[str] | None = None, price: float = 100.0) -> None:
        self.symbols = symbols if symbols is not None else ["BTCUSDT"]
        self.price = price

    async def get_trading_pairs(self, symbol=None):
        return [{"symbol": s, "symbolStatus": "OPEN"} for s in self.symbols]

    async def get_tickers(self, symbol=None):
        return [{"symbol": s, "lastPrice": str(self.price), "markPrice": str(self.price)}
                for s in self.symbols]

    def __getattr__(self, name):
        async def _fail(*args, **kwargs):
            raise AssertionError(f"llamada de red inesperada: {name}")

        return _fail


BTC_MARKET = {"id": "bitcoin", "symbol": "btc", "current_price": 100.0, "market_cap": 1e12}


# --- antigüedad ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_universe_is_stale(db):
    assert await is_universe_stale(db, make_settings()) is True


@pytest.mark.asyncio
async def test_universe_older_than_staleness_threshold_is_stale(db):
    old = datetime.now(UTC) - timedelta(hours=49)
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", old)])
    assert await is_universe_stale(db, make_settings()) is True


@pytest.mark.asyncio
async def test_fresh_universe_is_not_stale(db):
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", datetime.now(UTC))])
    assert await is_universe_stale(db, make_settings()) is False


@pytest.mark.asyncio
async def test_stale_universe_stops_signal_generation_without_touching_the_network(db):
    class Exploding:
        def __getattr__(self, name):
            async def _fail(*a, **k):
                raise AssertionError(f"no deberia llamarse a la red: {name}")

            return _fail

    old = datetime.now(UTC) - timedelta(hours=60)
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", old)])
    result = await run_signal_generation_cycle(db, Exploding(), make_settings())
    assert result == []


# --- refresco diario -----------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_previous_snapshot_and_records_the_error(db):
    previous = datetime.now(UTC) - timedelta(hours=30)
    await universe_repo.insert_snapshot(
        db, [entry("BTCUSDT", previous), entry("ETHUSDT", previous)]
    )

    ok = await refresh_universe_safely(
        FakeCoinGecko(fail=True), FakeBitunix(), db, make_settings()
    )

    assert ok is False
    latest = await universe_repo.get_latest_snapshot(db)
    assert {e.symbol for e in latest} == {"BTCUSDT", "ETHUSDT"}  # intacto
    error = await system_state_repo.get_state(db, LAST_REFRESH_ERROR_KEY)
    assert error is not None and "CoinGeckoApiError" in error


@pytest.mark.asyncio
async def test_successful_refresh_clears_the_error_left_by_a_previous_failure(db):
    await refresh_universe_safely(FakeCoinGecko(fail=True), FakeBitunix(), db, make_settings())
    assert await system_state_repo.get_state(db, LAST_REFRESH_ERROR_KEY)

    ok = await refresh_universe_safely(
        FakeCoinGecko(markets=[BTC_MARKET]), FakeBitunix(), db, make_settings()
    )

    assert ok is True
    assert not await system_state_repo.get_state(db, LAST_REFRESH_ERROR_KEY)


@pytest.mark.asyncio
async def test_snapshot_without_any_included_symbol_is_rejected_and_previous_is_kept(db):
    previous = datetime.now(UTC) - timedelta(hours=30)
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", previous)])

    ok = await refresh_universe_safely(
        FakeCoinGecko(markets=[BTC_MARKET]), FakeBitunix(symbols=[]), db, make_settings()
    )

    assert ok is False
    assert [e.symbol for e in await universe_repo.get_latest_snapshot(db)] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_successful_refresh_replaces_the_snapshot(db):
    previous = datetime.now(UTC) - timedelta(hours=30)
    await universe_repo.insert_snapshot(db, [entry("OLDUSDT", previous)])

    ok = await refresh_universe_safely(
        FakeCoinGecko(markets=[BTC_MARKET]), FakeBitunix(), db, make_settings()
    )

    assert ok is True
    included = await universe_repo.get_included_symbols(db)
    assert included == ["BTCUSDT"]
    assert await is_universe_stale(db, make_settings()) is False


@pytest.mark.asyncio
async def test_refresher_skips_when_the_snapshot_is_not_due(db):
    two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", two_hours_ago)])
    refresher = UniverseRefresher(FakeCoinGecko(fail=True), FakeBitunix(), db, make_settings())
    assert await refresher.run_once() == "vigente"  # no llama a CoinGecko


@pytest.mark.asyncio
async def test_refresher_refreshes_when_the_snapshot_is_due_and_survives_failures(db):
    twenty_five_hours_ago = datetime.now(UTC) - timedelta(hours=25)
    await universe_repo.insert_snapshot(db, [entry("BTCUSDT", twenty_five_hours_ago)])
    refresher = UniverseRefresher(FakeCoinGecko(fail=True), FakeBitunix(), db, make_settings())
    assert await refresher.run_once() == "fallido"  # no lanza: el bucle sobrevive
    assert [e.symbol for e in await universe_repo.get_latest_snapshot(db)] == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_refresher_without_any_snapshot_refreshes_immediately(db):
    refresher = UniverseRefresher(
        FakeCoinGecko(markets=[BTC_MARKET]), FakeBitunix(), db, make_settings()
    )
    assert await refresher.run_once() == "refrescado"
    assert await universe_repo.get_included_symbols(db) == ["BTCUSDT"]


# --- logging -------------------------------------------------------------------


def test_httpx_logger_is_set_to_warning_after_setup_logging():
    setup_logging()
    assert logging.getLogger("httpx").level == logging.WARNING
