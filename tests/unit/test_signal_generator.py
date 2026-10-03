"""Tests de `app/trading/signal_generator.py` (Fase 3, subfase 3.3) --
generador de senales en vivo. Cubre, en este orden:

1. `_group_actionable_signals`/`pick_representative_strategy` (funciones
   puras, sin DB ni red).
2. `_evaluate_one` (una estrategia controlada -- `FakeStrategy`, igual
   patron que `tests/unit/test_backtest_engine.py` -- sobre una base en
   memoria): HOLD, accionable dentro del tope, accionable sobre el tope
   (`signals_discarded_by_sl_cap`), y la vela repetida que no se reprocesa.
3. Un smoke test de punta a punta con estrategias REALES del registro
   (`STRATEGIES`) sobre un universo sintetico pequeno sembrado directo en
   `ohlcv_cache`/`asset_universe` (nunca red, mismo patron que
   `test_backtest_engine.py`): una fila por (simbolo, estrategia,
   timeframe, vela), sin duplicados en un segundo poll de la misma vela, y
   la vela en formacion excluida.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.market.ohlcv_history import interval_to_ms
from app.persistence.models import AssetUniverseEntry, OHLCVBar, Side
from app.persistence.repositories import ohlcv_repo, signals_repo, universe_repo
from app.strategies.base import BaseStrategy, Signal
from app.trading import signal_generator as sg


def make_settings(**overrides) -> Settings:
    defaults = dict(
        LEVERAGE=10, LIVE_SL_MARGIN_CAP_PCT=50.0,
        BACKTEST_FALLBACK_SL_PCT=0.05, BACKTEST_FALLBACK_TP_PCT=0.10,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


# --- _group_actionable_signals / pick_representative_strategy -----------


def _actionable(symbol, side, candle, strategy, sl_pct) -> sg._ActionableSignal:
    return sg._ActionableSignal(
        symbol=symbol, side=side, candle_close_time=candle, strategy=strategy,
        sl_margin_loss_pct=sl_pct,
    )


def test_group_actionable_signals_merges_same_symbol_side_candle():
    candle = datetime(2026, 1, 1, tzinfo=UTC)
    items = [
        _actionable("BTCUSDT", Side.LONG, candle, "ema_cross_9_21", 30.0),
        _actionable("BTCUSDT", Side.LONG, candle, "donchian_breakout_20", 40.0),
    ]
    candidates = sg._group_actionable_signals(items)
    assert len(candidates) == 1
    assert candidates[0].contributing_strategies == ["donchian_breakout_20", "ema_cross_9_21"]
    # El peor (mayor) SL entre las contribuyentes, nunca se subestima el riesgo.
    assert candidates[0].sl_margin_loss_pct == 40.0


def test_group_actionable_signals_keeps_different_symbols_separate():
    candle = datetime(2026, 1, 1, tzinfo=UTC)
    items = [
        _actionable("BTCUSDT", Side.LONG, candle, "ema_cross_9_21", 30.0),
        _actionable("ETHUSDT", Side.LONG, candle, "ema_cross_9_21", 30.0),
    ]
    candidates = sg._group_actionable_signals(items)
    assert len(candidates) == 2


def test_group_actionable_signals_keeps_opposite_sides_separate():
    candle = datetime(2026, 1, 1, tzinfo=UTC)
    items = [
        _actionable("BTCUSDT", Side.LONG, candle, "ema_cross_9_21", 30.0),
        _actionable("BTCUSDT", Side.SHORT, candle, "donchian_breakout_20", 30.0),
    ]
    candidates = sg._group_actionable_signals(items)
    assert len(candidates) == 2


def test_pick_representative_strategy_prefers_an_eligible_one():
    settings = make_settings(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="donchian_breakout_20")
    chosen = sg.pick_representative_strategy(
        ["ema_cross_9_21", "donchian_breakout_20"], settings
    )
    assert chosen == "donchian_breakout_20"


def test_pick_representative_strategy_falls_back_to_alphabetical_when_none_eligible():
    settings = make_settings(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="funding_contrarian_experimental")
    chosen = sg.pick_representative_strategy(
        ["trend_atr_stop_9_21_50", "donchian_breakout_20"], settings
    )
    assert chosen == "donchian_breakout_20"  # orden alfabetico, determinista


def test_pick_representative_strategy_alphabetical_when_no_restriction():
    settings = make_settings(REAL_ACCOUNT_ELIGIBLE_STRATEGIES="")
    chosen = sg.pick_representative_strategy(["zeta_strategy", "alpha_strategy"], settings)
    assert chosen == "alpha_strategy"


# --- _evaluate_one (estrategia controlada) -------------------------------


class FakeStrategy(BaseStrategy):
    """Senal y SL totalmente controlados -- mismo patron que
    `tests/unit/test_backtest_engine.py::FakeStrategy`."""

    def __init__(self, signal: Signal, sl_price: float | None = None) -> None:
        self.name = "fake"
        self._signal = signal
        self._sl_price = sl_price

    def evaluate(self, df):
        return self._signal

    def stop_price(self, df, side, entry_price):
        return self._sl_price


CANDLE = datetime(2026, 1, 1, tzinfo=UTC)


async def _df_with_close(price: float = 100.0):
    import pandas as pd

    return pd.DataFrame({"open": [price] * 3, "high": [price] * 3,
                          "low": [price] * 3, "close": [price] * 3})


@pytest.mark.asyncio
async def test_evaluate_one_hold_inserts_row_and_returns_none(db):
    settings = make_settings()
    df = await _df_with_close()
    result = await sg._evaluate_one(
        db, settings, FakeStrategy(Signal.HOLD), "fake", "BTCUSDT", "4h", df, CANDLE,
    )
    assert result is None
    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 1
    assert rows[0].signal == "HOLD"
    assert rows[0].status is None


@pytest.mark.asyncio
async def test_evaluate_one_actionable_within_cap_is_pending(db):
    settings = make_settings(LIVE_SL_MARGIN_CAP_PCT=50.0)
    df = await _df_with_close(100.0)
    # SL a 2 de distancia del precio de entrada (100) a 10x -> 2% * 10 = 20% del margen.
    result = await sg._evaluate_one(
        db, settings, FakeStrategy(Signal.LONG, sl_price=98.0), "fake", "BTCUSDT", "4h", df,
        CANDLE,
    )
    assert result is not None
    assert result.sl_margin_loss_pct == pytest.approx(20.0)

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 1
    assert rows[0].status == "PENDING"
    discarded = await signals_repo.get_discarded_by_sl_cap(db, symbol="BTCUSDT")
    assert discarded == []


@pytest.mark.asyncio
async def test_evaluate_one_actionable_over_cap_is_discarded_and_audited(db):
    settings = make_settings(LIVE_SL_MARGIN_CAP_PCT=10.0)
    df = await _df_with_close(100.0)
    # SL a 20 de distancia a 10x -> 20% * 10 = 200% del margen >> tope de 10%.
    result = await sg._evaluate_one(
        db, settings, FakeStrategy(Signal.SHORT, sl_price=120.0), "fake", "BTCUSDT", "4h", df,
        CANDLE,
    )
    assert result is None

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 1
    assert rows[0].status == "DISCARDED_SL_CAP"

    discarded = await signals_repo.get_discarded_by_sl_cap(db, symbol="BTCUSDT")
    assert len(discarded) == 1
    assert discarded[0].side == Side.SHORT
    assert discarded[0].sl_margin_loss_pct == pytest.approx(200.0)
    assert discarded[0].cap_pct == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_evaluate_one_same_candle_twice_is_not_reprocessed(db):
    """Re-evaluar la MISMA vela cerrada (p.ej. el siguiente poll antes de
    que haya cerrado una vela nueva) no debe duplicar la fila ni volver a
    auditar un descarte por tope de SL."""
    settings = make_settings(LIVE_SL_MARGIN_CAP_PCT=10.0)
    df = await _df_with_close(100.0)
    strategy = FakeStrategy(Signal.SHORT, sl_price=120.0)

    first = await sg._evaluate_one(
        db, settings, strategy, "fake", "BTCUSDT", "4h", df, CANDLE,
    )
    second = await sg._evaluate_one(
        db, settings, strategy, "fake", "BTCUSDT", "4h", df, CANDLE,
    )
    assert first is None  # descartada por SL
    assert second is None  # duplicada -- ni siquiera llega a re-evaluar el tope

    rows = await signals_repo.get_signals(db, symbol="BTCUSDT")
    assert len(rows) == 1
    discarded = await signals_repo.get_discarded_by_sl_cap(db, symbol="BTCUSDT")
    assert len(discarded) == 1  # no se duplico el registro de auditoria


# --- smoke test de punta a punta (estrategias reales, universo sintetico) --


def _seed_bars(symbol: str, timeframe: str, n_closed: int, now_ms: int, price: float = 100.0):
    """`n_closed` velas CERRADAS terminando justo antes de `now_ms`, mas
    UNA vela adicional todavia en formacion (para probar que se excluye).
    Precio con una variacion minima deterministica (nunca perfectamente
    plano) para que las estrategias de indicadores no dividan por cero."""
    step_ms = interval_to_ms(timeframe)
    bars = []
    for i in range(n_closed):
        open_time = now_ms - (n_closed - i) * step_ms
        wiggle = 1.0 + (0.0001 if i % 2 == 0 else -0.0001)
        p = price * wiggle
        bars.append(OHLCVBar(
            symbol=symbol, interval=timeframe, price_type="LAST_PRICE", open_time=open_time,
            open=p, high=p * 1.001, low=p * 0.999, close=p,
        ))
    forming_open_time = now_ms - step_ms // 2
    bars.append(OHLCVBar(
        symbol=symbol, interval=timeframe, price_type="LAST_PRICE", open_time=forming_open_time,
        open=price * 1.5, high=price * 1.6, low=price * 1.4, close=price * 1.55,
    ))
    return bars, forming_open_time


@pytest.mark.asyncio
async def test_run_signal_generation_cycle_end_to_end_synthetic(db):
    import time

    settings = make_settings()
    now_ms = int(time.time() * 1000)

    await universe_repo.insert_snapshot(db, [
        AssetUniverseEntry(
            refreshed_at=datetime.now(UTC), coingecko_id="bitcoin", symbol="BTCUSDT",
            included=True,
        ),
    ])

    forming_times: dict[str, int] = {}
    for timeframe, strategy_names in sg._timeframe_groups().items():
        bars_needed = max(sg._history_bars_for(name) for name in strategy_names)
        bars, forming_open_time = _seed_bars("BTCUSDT", timeframe, bars_needed, now_ms)
        await ohlcv_repo.upsert_bars(db, bars)
        forming_times[timeframe] = forming_open_time

    # Nunca deberia llamarse a la red -- la cache ya cubre todo el rango
    # pedido (ver `_load_closed_bars`/`download_missing`).
    class ExplodingRestClient:
        async def get_kline(self, *args, **kwargs):
            raise AssertionError("no deberia llamarse a la red: la cache ya esta completa")

    rest_client = ExplodingRestClient()

    candidates_1 = await sg.run_signal_generation_cycle(db, rest_client, settings)
    assert isinstance(candidates_1, list)  # puede o no haber accionables en datos sinteticos

    rows_after_first = await signals_repo.get_signals(db, symbol="BTCUSDT", limit=1000)
    assert len(rows_after_first) > 0
    # Una fila por cada (estrategia, timeframe) combinacion evaluada -- 7
    # pares estrategia/timeframe en total (donchian corre en 2 timeframes).
    expected_pairs = sum(len(names) for names in sg._timeframe_groups().values())
    assert len(rows_after_first) == expected_pairs

    # La vela en formacion NUNCA se evaluo -- candle_close_time siempre
    # corresponde a la ULTIMA vela CERRADA, nunca a la que seguia abierta.
    for row in rows_after_first:
        step_ms = interval_to_ms(row.timeframe)
        forming_close_ms = forming_times[row.timeframe] + step_ms
        assert int(row.candle_close_time.timestamp() * 1000) < forming_close_ms

    # Segundo poll SIN que haya cerrado ninguna vela nueva -- debe ser un
    # no-op total (sin duplicados, sin red).
    candidates_2 = await sg.run_signal_generation_cycle(db, rest_client, settings)
    rows_after_second = await signals_repo.get_signals(db, symbol="BTCUSDT", limit=1000)
    assert len(rows_after_second) == len(rows_after_first)
    assert candidates_2 == []  # nada nuevo que agrupar/abrir en el segundo poll


@pytest.mark.asyncio
async def test_run_signal_generation_cycle_empty_universe_is_a_noop(db):
    settings = make_settings()

    class ExplodingRestClient:
        async def get_kline(self, *args, **kwargs):
            raise AssertionError("sin universo no deberia tocar la red")

    candidates = await sg.run_signal_generation_cycle(db, ExplodingRestClient(), settings)
    assert candidates == []
