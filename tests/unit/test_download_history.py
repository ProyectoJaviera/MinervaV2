"""Tests de `scripts/download_history.py::_check_internal_gaps` --
compara velas esperadas vs. realmente guardadas dentro de un rango ya
cubierto, para detectar huecos internos (p.ej. una interrupcion real del
exchange), sin confundirlos con el limite real del historial (piso)."""

from __future__ import annotations

import logging

import pytest

from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import OHLCVBar
from app.persistence.repositories import ohlcv_repo
from scripts.download_history import _check_internal_gaps

BASE_MS = 1_700_000_000_000
STEP_MS = interval_to_ms("4h")


def _bar(idx: int) -> OHLCVBar:
    return OHLCVBar(
        symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE",
        open_time=BASE_MS + idx * STEP_MS, open=100, high=101, low=99, close=100.5,
    )


@pytest.mark.asyncio
async def test_no_warning_when_range_is_fully_covered(db, caplog):
    bars = [_bar(i) for i in range(10)]
    await ohlcv_repo.upsert_bars(db, bars)

    with caplog.at_level(logging.WARNING):
        await _check_internal_gaps(
            db, "BTCUSDT", "4h", "LAST_PRICE", BASE_MS, BASE_MS + 9 * STEP_MS
        )

    assert not any("hueco interno" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_warns_when_internal_gap_exists(db, caplog):
    # Velas 0-9 pero falta la 5 (hueco interno real).
    bars = [_bar(i) for i in range(10) if i != 5]
    await ohlcv_repo.upsert_bars(db, bars)

    with caplog.at_level(logging.WARNING):
        await _check_internal_gaps(
            db, "BTCUSDT", "4h", "LAST_PRICE", BASE_MS, BASE_MS + 9 * STEP_MS
        )

    warnings = [r.message for r in caplog.records if "hueco interno" in r.message]
    assert len(warnings) == 1
    assert "esperadas 10, guardadas 9" in warnings[0]


@pytest.mark.asyncio
async def test_no_false_positive_for_missing_history_before_real_floor(db, caplog):
    """Si el piso real del historial es posterior al `start_ms` pedido, no
    es un hueco -- es el limite real de los datos, no debe generar
    advertencia."""
    bars = [_bar(i) for i in range(5, 10)]  # las velas 0-4 nunca existieron
    await ohlcv_repo.upsert_bars(db, bars)
    await ohlcv_repo.set_floor(db, "BTCUSDT", "4h", "LAST_PRICE", BASE_MS + 5 * STEP_MS)

    with caplog.at_level(logging.WARNING):
        await _check_internal_gaps(
            db, "BTCUSDT", "4h", "LAST_PRICE", BASE_MS, BASE_MS + 9 * STEP_MS
        )

    assert not any("hueco interno" in r.message for r in caplog.records)
