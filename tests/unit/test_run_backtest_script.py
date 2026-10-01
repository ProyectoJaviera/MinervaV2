"""Tests de `scripts/run_backtest.py::_find_missing_series` -- verifica
TODAS las series (simbolo x timeframe x tipo de precio) que alguna
estrategia necesitara, ANTES de calcular nada, y las devuelve juntas
(tarea 1a, correccion post-Fase-2: una corrida real fallo a mitad de
camino, tras minutos de computo, por una sola serie faltante en
BNBUSDT 1h)."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo
from app.strategies.registry import STRATEGY_TIMEFRAMES
from scripts import run_backtest

BASE_MS = 1_700_000_000_000
TIMEFRAMES = sorted({tf for tfs in STRATEGY_TIMEFRAMES.values() for tf in tfs})


async def _seed_complete(db, symbol: str, interval: str, price_type: str, end_ms: int) -> None:
    step_ms = interval_to_ms(interval)
    n = (end_ms - BASE_MS) // step_ms + 1
    bars = [
        OHLCVBar(
            symbol=symbol, interval=interval, price_type=price_type,
            open_time=BASE_MS + i * step_ms, open=100, high=101, low=99, close=100.5,
        )
        for i in range(n)
    ]
    await ohlcv_repo.upsert_bars(db, bars)
    await ohlcv_repo.mark_series_complete(db, symbol, interval, price_type, BASE_MS, end_ms)


@pytest.mark.asyncio
async def test_no_missing_series_when_everything_is_cached(db, monkeypatch):
    test_settings = Settings(_env_file=None, BACKTEST_CONTROL_SYMBOLS="AAAUSDT")
    monkeypatch.setattr(run_backtest, "settings", test_settings)

    end_ms = BASE_MS + 50 * interval_to_ms("1d")
    for tf in TIMEFRAMES:
        for price_type in ("LAST_PRICE", "MARK_PRICE"):
            await _seed_complete(db, "AAAUSDT", tf, price_type, end_ms)

    missing = await run_backtest._find_missing_series(db, ["AAAUSDT"], BASE_MS, end_ms)
    assert missing == []


@pytest.mark.asyncio
async def test_one_missing_series_is_reported_alone(db, monkeypatch):
    test_settings = Settings(_env_file=None, BACKTEST_CONTROL_SYMBOLS="AAAUSDT")
    monkeypatch.setattr(run_backtest, "settings", test_settings)

    end_ms = BASE_MS + 50 * interval_to_ms("1d")
    for tf in TIMEFRAMES:
        for price_type in ("LAST_PRICE", "MARK_PRICE"):
            if tf == "1h" and price_type == "MARK_PRICE":
                continue  # esta es la que falta a proposito
            await _seed_complete(db, "AAAUSDT", tf, price_type, end_ms)

    missing = await run_backtest._find_missing_series(db, ["AAAUSDT"], BASE_MS, end_ms)
    assert len(missing) == 1
    assert "AAAUSDT" in missing[0]
    assert "1h" in missing[0]
    assert "MARK_PRICE" in missing[0]


@pytest.mark.asyncio
async def test_multiple_missing_series_are_all_listed_together(db, monkeypatch):
    """No se detiene en la primera que falta -- las junta todas (eso es
    justamente lo que evita descubrirlas una por una a mitad de corrida)."""
    test_settings = Settings(_env_file=None, BACKTEST_CONTROL_SYMBOLS="AAAUSDT")
    monkeypatch.setattr(run_backtest, "settings", test_settings)

    end_ms = BASE_MS + 50 * interval_to_ms("1d")
    # No se siembra nada -- TODAS las series de AAAUSDT deberian faltar.
    missing = await run_backtest._find_missing_series(db, ["AAAUSDT"], BASE_MS, end_ms)
    assert len(missing) == len(TIMEFRAMES) * 2
