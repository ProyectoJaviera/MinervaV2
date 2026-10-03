"""Tests de `app/core/scheduler.py` (Fase 3, subfase 3.3) -- el Scheduler
ya no evalua una sola estrategia/simbolo (vestigio de Fase 1): agrupa
candidatos del generador de senales y decide abrir/revertir en
`PaperBackend`. Estos tests ejercitan `_process_candidate`/`run_cycle`
directamente (sin universo/cache reales -- eso ya lo cubre
`test_signal_generator.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.core.scheduler import Scheduler
from app.persistence.models import Side, TradeStatus
from app.persistence.repositories import trades_repo
from app.trading.signal_generator import SignalCandidate


class FakeRestClient:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]

    async def get_kline(self, *args, **kwargs):
        raise AssertionError("estos tests no deberian tocar la red")


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0006, MAKER_FEE_PCT=0.0002,
        INITIAL_CAPITAL_USDT=100.0, MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=3, MAX_SAME_DIRECTION_POSITIONS=3,
        LIVE_SL_MARGIN_CAP_PCT=50.0,
        REAL_ACCOUNT_ELIGIBLE_STRATEGIES="ema_cross_9_21,donchian_breakout_20",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def make_candidate(**overrides) -> SignalCandidate:
    defaults = dict(
        symbol="BTCUSDT", side=Side.LONG, candle_close_time=datetime(2026, 1, 1, tzinfo=UTC),
        contributing_strategies=["ema_cross_9_21"], sl_margin_loss_pct=20.0,
    )
    defaults.update(overrides)
    return SignalCandidate(**defaults)


def _scheduler(db, price: float = 100.0, **settings_overrides) -> Scheduler:
    from app.execution.paper_backend import PaperBackend

    settings = make_settings(**settings_overrides)
    rest_client = FakeRestClient(price)
    backend = PaperBackend(db, rest_client, settings)
    return Scheduler(db, rest_client, backend, settings), backend, rest_client


@pytest.mark.asyncio
async def test_process_candidate_opens_a_new_position(db):
    scheduler, _backend, _rest = _scheduler(db)
    await scheduler._process_candidate(make_candidate())

    positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert len(positions) == 1
    assert positions[0].side == Side.LONG
    assert positions[0].strategy == "ema_cross_9_21"


@pytest.mark.asyncio
async def test_process_candidate_picks_eligible_representative_strategy(db):
    scheduler, _backend, _rest = _scheduler(db)
    candidate = make_candidate(
        contributing_strategies=["trend_atr_stop_9_21_50", "donchian_breakout_20"]
    )
    await scheduler._process_candidate(candidate)

    positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert len(positions) == 1
    # "donchian_breakout_20" es elegible, "trend_atr_stop_9_21_50" no -- se
    # prefiere la elegible aunque no sea la primera alfabeticamente.
    assert positions[0].strategy == "donchian_breakout_20"


@pytest.mark.asyncio
async def test_process_candidate_skips_when_same_direction_already_open(db):
    scheduler, backend, _rest = _scheduler(db)
    await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        sl_margin_loss_pct=10.0,
    )
    await scheduler._process_candidate(make_candidate())

    positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert len(positions) == 1  # no se abrio una segunda


@pytest.mark.asyncio
async def test_process_candidate_closes_opposite_position_on_reversal(db):
    scheduler, backend, rest_client = _scheduler(db)
    existing = await backend.open_position(
        "BTCUSDT", Side.SHORT, margin_usdt=10.0, leverage=10, strategy="ema_cross_9_21",
        sl_margin_loss_pct=10.0,
    )

    rest_client.price = 90.0  # SHORT a favor, se cierra con ganancia
    await scheduler._process_candidate(make_candidate(side=Side.LONG))

    closed = await trades_repo.get_trade(db, existing.id)
    assert closed.status == TradeStatus.CLOSED
    assert closed.close_reason == "SIGNAL_REVERSAL"

    open_positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert len(open_positions) == 1
    assert open_positions[0].side == Side.LONG


@pytest.mark.asyncio
async def test_process_candidate_does_not_raise_when_risk_engine_rejects(db):
    scheduler, _backend, _rest = _scheduler(db, MAX_SIMULTANEOUS_POSITIONS=0)
    # No debe lanzar -- el rechazo se registra y el ciclo sigue (ver
    # `run_cycle`, que llama esto por cada candidato sin abortar el resto).
    await scheduler._process_candidate(make_candidate())

    positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert positions == []


@pytest.mark.asyncio
async def test_run_cycle_processes_every_candidate_from_the_generator(db, monkeypatch):
    scheduler, _backend, _rest = _scheduler(db)
    candidates = [
        make_candidate(symbol="BTCUSDT", side=Side.LONG),
        make_candidate(
            symbol="ETHUSDT", side=Side.SHORT, contributing_strategies=["donchian_breakout_20"]
        ),
    ]

    async def fake_generation(db_arg, rest_arg, settings_arg):
        return candidates

    monkeypatch.setattr(
        "app.core.scheduler.run_signal_generation_cycle", fake_generation
    )

    returned = await scheduler.run_cycle()
    assert returned == candidates

    btc_positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    eth_positions = await trades_repo.get_open_positions(db, "ETHUSDT")
    assert len(btc_positions) == 1
    assert len(eth_positions) == 1
    assert eth_positions[0].side == Side.SHORT


@pytest.mark.asyncio
async def test_run_cycle_continues_after_one_candidate_errors(db, monkeypatch):
    """Un candidato que lanza una excepcion inesperada (no
    `RiskRejectedError`/`InsufficientRiskBudgetError`) no debe tumbar el
    resto del ciclo -- mismo principio de aislamiento que ya tenia el
    Scheduler de Fase 1 por simbolo."""
    scheduler, _backend, _rest = _scheduler(db)
    good_candidate = make_candidate(symbol="ETHUSDT", side=Side.SHORT,
                                     contributing_strategies=["donchian_breakout_20"])
    # `sl_margin_loss_pct=None` fuerza el `ValueError` de `check_new_entry`
    # (obligatorio para entradas no manuales, ver `risk_engine.py`) -- una
    # excepcion inesperada que `_process_candidate` NO atrapa explicitamente,
    # para probar que `run_cycle` la aisla igual y sigue con el resto.
    bad_candidate = make_candidate(symbol="DOESNOTEXIST", side=Side.LONG, sl_margin_loss_pct=None)

    async def fake_generation(db_arg, rest_arg, settings_arg):
        return [bad_candidate, good_candidate]

    monkeypatch.setattr(
        "app.core.scheduler.run_signal_generation_cycle", fake_generation
    )

    await scheduler.run_cycle()

    eth_positions = await trades_repo.get_open_positions(db, "ETHUSDT")
    assert len(eth_positions) == 1
