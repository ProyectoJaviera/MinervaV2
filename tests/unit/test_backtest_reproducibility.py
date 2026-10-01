"""Tests de `Settings.backtest_official_end_ms` (tarea 3: fecha final FIJA
del backtest "oficial" para que dos corridas den exactamente los mismos
numeros -- antes se usaba la hora real de cada corrida)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings


def test_default_official_end_date_matches_documented_date():
    settings = Settings(_env_file=None)
    expected = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
    assert settings.backtest_official_end_ms == expected


def test_official_end_ms_is_configurable_and_deterministic():
    settings = Settings(_env_file=None, BACKTEST_OFFICIAL_END_DATE="2025-06-15")
    expected = int(datetime(2025, 6, 15, tzinfo=UTC).timestamp() * 1000)
    assert settings.backtest_official_end_ms == expected

    # Dos lecturas seguidas dan exactamente el mismo valor (no depende de
    # la hora real de ejecucion, a diferencia de `time.time()`).
    assert settings.backtest_official_end_ms == settings.backtest_official_end_ms
