"""Smoke test de punta a punta de `run_full_backtest` con datos sinteticos
pequenos (2 simbolos, pocas velas) -- ejecuta TODAS las estrategias
registradas de principio a fin, incluido guardar los veredictos en SQLite.

Este es exactamente el tipo de prueba que habria detectado en segundos el
bug real de produccion ("29 values for 30 columns" en
`backtest_repo.insert_verdict`, ver `test_backtest_repo.py`): los tests
unitarios de cada pieza por separado pasaban igual, porque ninguno
ejercitaba el camino completo hasta la escritura en la base de datos real.
Agregado a CLAUDE.md como regla: antes de dar por terminada una tarea que
toque el pipeline de datos o la persistencia, correr un smoke test de
punta a punta con un subconjunto pequeno."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from app.backtesting.report import run_full_backtest
from app.config import Settings
from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import OHLCVBar
from app.persistence.repositories import backtest_repo, ohlcv_repo
from app.strategies.registry import STRATEGIES

# Rango seguro en el pasado (no depende de la fecha real de ejecucion):
# ningun bar queda "en formacion" para `drop_incomplete_last_bar`.
START_MS = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp() * 1000)
SPAN_DAYS = 90
END_MS = START_MS + SPAN_DAYS * 86_400_000

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
INTERVALS = ("4h", "1h", "1d")  # union de STRATEGY_TIMEFRAMES


async def _seed_symbol(db, symbol: str, rng: random.Random) -> None:
    for interval in INTERVALS:
        step_ms = interval_to_ms(interval)
        n = (END_MS - START_MS) // step_ms
        price = 100.0
        last_bars: list[OHLCVBar] = []
        mark_bars: list[OHLCVBar] = []
        for i in range(n):
            open_time = START_MS + i * step_ms
            o = price
            c = price * (1 + rng.uniform(-0.01, 0.01))
            h = max(o, c) * 1.002
            low = min(o, c) * 0.998
            last_bars.append(OHLCVBar(
                symbol=symbol, interval=interval, price_type="LAST_PRICE", open_time=open_time,
                open=o, high=h, low=low, close=c,
            ))
            mark_bars.append(OHLCVBar(
                symbol=symbol, interval=interval, price_type="MARK_PRICE", open_time=open_time,
                open=o, high=h, low=low, close=c,
            ))
            price = c
        await ohlcv_repo.upsert_bars(db, last_bars)
        await ohlcv_repo.upsert_bars(db, mark_bars)


@pytest.mark.asyncio
async def test_run_full_backtest_smoke_saves_verdicts_for_every_strategy(db):
    rng = random.Random(0)
    for symbol in SYMBOLS:
        await _seed_symbol(db, symbol, rng)

    settings = Settings(
        _env_file=None,
        BACKTEST_CONTROL_SYMBOLS="BTCUSDT,ETHUSDT",  # evita pedir datos de simbolos extra
        BACKTEST_MIN_TRADES_TOTAL=1,  # pocas velas -> pocos trades, no es lo que se prueba aqui
        BACKTEST_MIN_TRADES_PER_CELL=1,
        BACKTEST_PORTFOLIO_SIM_RUNS=5,  # pocas corridas Monte Carlo, alcanza para el smoke test
    )

    results = await run_full_backtest(db, settings, SYMBOLS, START_MS, END_MS - 1)

    assert len(results) == len(STRATEGIES)
    for result in results:
        assert result.strategy_name in STRATEGIES

    # El verdadero objetivo del smoke test: que cada veredicto haya
    # llegado hasta la base de datos real (esto es lo que el bug de
    # "29 values for 30 columns" rompia -- un error a mitad de la
    # primera escritura, sin que ningun test unitario lo detectara).
    verdicts = await backtest_repo.get_verdicts(db)
    assert {v.strategy for v in verdicts} == set(STRATEGIES.keys())
