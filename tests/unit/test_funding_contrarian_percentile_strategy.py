"""Tests de `FundingContrarianPercentileStrategy` (v2 experimental, umbral
por percentiles -- ver docstring del modulo para el porque de esta
estrategia y la correccion del bug real de `len(df)` vs. `MAX_LOOKBACK_BARS`
que la dejaba siempre en HOLD).

Los percentiles son causales y se calculan en `precompute()` sobre toda la
serie (igual que las demas estrategias con indicadores, ver
`BaseStrategy.precompute`) -- estos tests llaman `precompute()` primero,
tal como hace el motor, y luego `evaluate()` sobre el resultado."""

from __future__ import annotations

import pandas as pd
import pytest

from app.backtesting.engine import run_backtest
from app.config import Settings
from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import OHLCVBar
from app.persistence.repositories import funding_repo, ohlcv_repo
from app.strategies.base import Signal
from app.strategies.funding_contrarian_percentile import FundingContrarianPercentileStrategy

HISTORY = 20  # ventana chica para que los tests sean legibles (unitarios)


def _precomputed(
    funding: list[float], closes: list[float], approx: list[bool] | None = None,
    **strategy_kwargs,
) -> tuple[FundingContrarianPercentileStrategy, pd.DataFrame]:
    strat = FundingContrarianPercentileStrategy(**strategy_kwargs)
    df = pd.DataFrame({
        "close": closes, "funding_rate": funding,
        "funding_is_approximated": approx if approx is not None else [False] * len(funding),
    })
    return strat, strat.precompute(df)


def test_hold_without_funding_column():
    strat = FundingContrarianPercentileStrategy(history_window=HISTORY)
    df = pd.DataFrame({"close": [100.0] * 30})
    assert strat.evaluate(strat.precompute(df)) == Signal.HOLD


def test_hold_with_insufficient_history():
    strat, df = _precomputed(
        [0.0001] * (HISTORY - 1), [100.0] * (HISTORY - 1), history_window=HISTORY
    )
    assert strat.evaluate(df) == Signal.HOLD


def test_short_when_funding_in_upper_percentile_with_negative_momentum():
    """Funding tipico bajo (~0.0001) con un pico reciente alto: el pico cae
    en el percentil superior de su propia historia, aunque sea muy inferior
    al 0.03% absoluto de la estrategia original -- por eso SI genera senal
    donde la original (umbral fijo) no la generaria. Solo las ultimas 2
    (10% de 20) velas tienen el pico, para que el percentil 90 quede
    claramente por debajo del promedio reciente (lookback=4, que mezcla 2
    velas base + las 2 del pico)."""
    funding = [0.0001] * (HISTORY - 2) + [0.0005] * 2
    closes = [100.0] * (HISTORY - 1) + [99.0]  # momentum negativo
    strat, df = _precomputed(
        funding, closes, history_window=HISTORY, lookback=4,
        upper_percentile=90.0, lower_percentile=10.0,
    )
    assert strat.evaluate(df) == Signal.SHORT


def test_long_when_funding_in_lower_percentile_with_positive_momentum():
    funding = [0.0001] * (HISTORY - 2) + [-0.0003] * 2
    closes = [100.0] * (HISTORY - 1) + [101.0]  # momentum positivo
    strat, df = _precomputed(
        funding, closes, history_window=HISTORY, lookback=4,
        upper_percentile=90.0, lower_percentile=10.0,
    )
    assert strat.evaluate(df) == Signal.LONG


def test_hold_when_funding_flat_no_relative_extreme():
    funding = [0.0001] * HISTORY  # sin variacion -> ningun percentil extremo real
    closes = [100.0] * (HISTORY - 1) + [99.0]
    strat, df = _precomputed(funding, closes, history_window=HISTORY, lookback=4)
    assert strat.evaluate(df) == Signal.HOLD


def test_hold_when_any_bar_in_reference_window_has_approximated_funding():
    """Tramo pre-2024 aproximado (mediana constante, percentiles
    degenerados): si CUALQUIER vela de la ventana de referencia tiene
    funding aproximado, no se emite señal -- aunque el resto de las
    condiciones (percentil + momentum) se cumplirian igual."""
    funding = [0.0001] * (HISTORY - 2) + [0.0005] * 2  # mismas condiciones que el test SHORT
    closes = [100.0] * (HISTORY - 1) + [99.0]
    approx = [True] + [False] * (HISTORY - 1)  # una sola vela aproximada, al principio
    strat, df = _precomputed(
        funding, closes, approx, history_window=HISTORY, lookback=4,
        upper_percentile=90.0, lower_percentile=10.0,
    )
    assert strat.evaluate(df) == Signal.HOLD


BASE_MS = 1_700_000_000_000
STEP_4H = interval_to_ms("4h")


@pytest.mark.asyncio
async def test_generates_signals_through_the_real_engine_past_max_lookback_bars(db):
    """Reproduce el bug real con los parametros REALES (`history_window`
    default = 720): el motor nunca le pasa a una estrategia mas de
    `MAX_LOOKBACK_BARS` (300, `app/backtesting/engine.py`) velas por vela
    evaluada. Con la version vieja (sin `precompute`), `evaluate` exigia
    `len(df) >= history_window + 1` (721) y esa condicion NUNCA se cumplia
    (300 < 721) -- 0 señales en cualquier corrida real, sin importar
    cuanta historia hubiera en verdad. Esta prueba usa 750 velas (mas que
    `history_window`) para que, en la vela de señal, `evaluate` SOLO
    reciba las ultimas 300 (la cola truncada por el motor) y verifica que
    aun asi se genera la operacion -- porque los percentiles ya estan
    precalculados sobre las 750 velas completas antes de truncar."""
    n = 750
    last_bars: list[OHLCVBar] = []
    mark_bars: list[OHLCVBar] = []
    funding_events: list[tuple[int, float]] = []
    price = 100.0
    for i in range(n):
        open_time = BASE_MS + i * STEP_4H
        # Ultimas 20 velas: funding alto sostenido (percentil superior de
        # toda la ventana de 720). Momentum negativo en la anteultima vela
        # (la señal se evalua con la vela cerrada; la entrada se llena en
        # el open de la vela SIGUIENTE, que debe existir).
        rate = 0.0005 if i >= n - 20 else 0.0001
        close = price * 0.98 if i == n - 2 else price
        last_bars.append(OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE", open_time=open_time,
            open=price, high=price * 1.001, low=price * 0.999, close=close,
        ))
        mark_bars.append(OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="MARK_PRICE", open_time=open_time,
            open=price, high=price * 1.001, low=price * 0.999, close=close,
        ))
        funding_events.append((open_time, rate))
        price = close

    await ohlcv_repo.upsert_bars(db, last_bars)
    await ohlcv_repo.upsert_bars(db, mark_bars)
    await funding_repo.upsert_funding(db, "BTCUSDT", funding_events)

    strategy = FundingContrarianPercentileStrategy()  # parametros default reales
    settings = Settings(_env_file=None, MAX_SL_MARGIN_LOSS_PCT=1000.0)
    now_ms = BASE_MS + (n + 10) * STEP_4H

    trades, _ = await run_backtest(
        db, strategy, "funding_contrarian_percentile_experimental", "BTCUSDT", "4h",
        BASE_MS, BASE_MS + (n - 1) * STEP_4H, settings, now_ms=now_ms,
    )

    assert len(trades) > 0
    assert trades[0].side.value == "SHORT"
