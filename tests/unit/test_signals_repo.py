"""Tests de `app/persistence/repositories/signals_repo.py` (Fase 3,
subfase 3.3) -- en particular, que `INSERT OR IGNORE` sobre
`UNIQUE(symbol, strategy, timeframe, candle_close_time)` de verdad
deduplica y que `insert_signal` lo reporta correctamente (`None` en vez de
un id nuevo), que es la base de "sin duplicados en consultas repetidas de
la misma vela cerrada"."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence.models import Side, SignalDiscardedBySLCap, SignalRecord
from app.persistence.repositories import signals_repo


def make_signal(**overrides) -> SignalRecord:
    defaults = dict(
        symbol="BTCUSDT", strategy="ema_cross_9_21", is_experimental=False, timeframe="4h",
        candle_close_time=datetime(2026, 1, 1, tzinfo=UTC),
        evaluated_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=UTC),
        signal="HOLD", price_at_eval=100.0, status=None,
    )
    defaults.update(overrides)
    return SignalRecord(**defaults)


@pytest.mark.asyncio
async def test_insert_signal_returns_new_id(db):
    signal_id = await signals_repo.insert_signal(db, make_signal())
    assert signal_id is not None


@pytest.mark.asyncio
async def test_insert_signal_same_key_twice_is_ignored_and_returns_none(db):
    """La MISMA (simbolo, estrategia, timeframe, vela) insertada dos veces
    -- re-evaluar la misma vela cerrada en un poll posterior no debe
    duplicar la fila."""
    first_id = await signals_repo.insert_signal(db, make_signal())
    second_id = await signals_repo.insert_signal(db, make_signal())
    assert first_id is not None
    assert second_id is None

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_insert_signal_different_strategy_same_candle_is_a_separate_row(db):
    """Distinta estrategia sobre la MISMA vela SI es una fila nueva -- el
    UNIQUE incluye `strategy`, no solo (simbolo, timeframe, vela)."""
    await signals_repo.insert_signal(db, make_signal(strategy="ema_cross_9_21"))
    second_id = await signals_repo.insert_signal(db, make_signal(strategy="donchian_breakout_20"))
    assert second_id is not None

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_update_status_persists(db):
    signal_id = await signals_repo.insert_signal(
        db, make_signal(signal="LONG", status="PENDING")
    )
    assert signal_id is not None
    await signals_repo.update_status(db, signal_id, "DISCARDED_SL_CAP")

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert rows[0].status == "DISCARDED_SL_CAP"


@pytest.mark.asyncio
async def test_insert_and_get_discarded_by_sl_cap(db):
    signal_id = await signals_repo.insert_signal(
        db, make_signal(signal="LONG", status="DISCARDED_SL_CAP")
    )
    assert signal_id is not None
    await signals_repo.insert_discarded_by_sl_cap(
        db,
        SignalDiscardedBySLCap(
            signal_id=signal_id, symbol="BTCUSDT", strategy="ema_cross_9_21", timeframe="4h",
            candle_close_time=datetime(2026, 1, 1, tzinfo=UTC), side=Side.LONG,
            sl_margin_loss_pct=75.0, cap_pct=50.0, created_at=datetime.now(UTC),
        ),
    )
    rows = await signals_repo.get_discarded_by_sl_cap(db, symbol="BTCUSDT")
    assert len(rows) == 1
    assert rows[0].sl_margin_loss_pct == pytest.approx(75.0)
    assert rows[0].signal_id == signal_id
