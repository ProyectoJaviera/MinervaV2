"""Slippage con la MISMA formula que el backtest (paridad) y acumulacion de funding
idempotente (subfase 3.4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.backtesting import engine as backtest_engine
from app.config import Settings
from app.execution.paper_backend import PaperBackend
from app.persistence.models import Side, Trade, TradeStatus
from app.persistence.repositories import funding_repo, trades_repo
from app.trading import slippage
from app.trading.funding_accrual import accrue_funding


class FakeRest:
    def __init__(self, price: float) -> None:
        self.price = price

    async def get_tickers(self, symbol=None):
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0, LEVERAGE=10, TAKER_FEE_PCT=0.0, MAKER_FEE_PCT=0.0,
        INITIAL_CAPITAL_USDT=100.0, MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=5, MAX_SAME_DIRECTION_POSITIONS=5,
        LIVE_SL_MARGIN_CAP_PCT=50.0, MAX_DAILY_LOSS_PCT=0.9, MAX_DRAWDOWN_PCT=0.9,
        BACKTEST_SLIPPAGE_BPS=10.0, REAL_ACCOUNT_ELIGIBLE_STRATEGIES="ema_cross_9_21",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


def test_slippage_formulas_match_the_definition():
    assert slippage.entry_slippage_usdt(100.0, 10.0) == pytest.approx(0.1)  # 10 bps
    assert slippage.exit_slippage_usdt(2.0, 50.0, 10.0) == pytest.approx(0.1)  # 2*50*10bps


def test_backtest_and_paper_use_the_same_slippage_functions():
    """Paridad: el backtest importa las MISMAS funciones que usa el paper trading."""
    assert backtest_engine.entry_slippage_usdt is slippage.entry_slippage_usdt
    assert backtest_engine.exit_slippage_usdt is slippage.exit_slippage_usdt


@pytest.mark.asyncio
async def test_paper_close_charges_entry_and_exit_slippage_like_the_backtest(db):
    settings = make_settings(BACKTEST_SLIPPAGE_BPS=10.0)
    rest = FakeRest(100.0)
    backend = PaperBackend(db, rest, settings)
    trade = await backend.open_position(
        "BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10, is_manual=True,
    )
    assert trade.slippage_entry_usdt == pytest.approx(0.1)  # 100 * 10bps

    rest.price = 110.0
    closed = await backend.close_position(trade.id, reason="MANUAL")
    expected_exit_slip = trade.qty * 110.0 * 10.0 / 10_000
    assert closed.slippage_exit_usdt == pytest.approx(expected_exit_slip)
    # Fees en 0: el PnL neto es bruto menos los dos costos de slippage.
    assert closed.pnl_net_usdt == pytest.approx(10.0 - 0.1 - expected_exit_slip)
    assert closed.fill_source == "REST_MARK"


def _trade(side: Side, funding_paid: float = 0.0, last_applied: int | None = None) -> Trade:
    return Trade(
        id=1, symbol="BTCUSDT", side=side, strategy=None, status=TradeStatus.OPEN,
        leverage=10, margin_usdt=10.0, notional_usdt=100.0, qty=1.0, entry_price=100.0,
        fee_entry_usdt=0.0, opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        funding_paid_usdt=funding_paid, funding_last_applied_ms=last_applied,
    )


async def _insert_open(db, side: Side) -> Trade:
    return await trades_repo.create_trade(db, _trade(side))


@pytest.mark.asyncio
async def test_long_pays_positive_funding_and_short_receives_it(db):
    t0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    await funding_repo.upsert_funding(db, "BTCUSDT", [(t0 + 3_600_000, 0.001)])
    long_trade = await _insert_open(db, Side.LONG)
    short_trade = await _insert_open(db, Side.SHORT)
    until = t0 + 7_200_000

    long_after = await accrue_funding(db, long_trade, until)
    short_after = await accrue_funding(db, short_trade, until)
    assert long_after.funding_paid_usdt == pytest.approx(0.1)  # paga
    assert short_after.funding_paid_usdt == pytest.approx(-0.1)  # recibe


@pytest.mark.asyncio
async def test_funding_is_applied_only_once_even_if_accrued_again(db):
    t0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    await funding_repo.upsert_funding(db, "BTCUSDT", [(t0 + 3_600_000, 0.001)])
    trade = await _insert_open(db, Side.LONG)
    once = await accrue_funding(db, trade, t0 + 7_200_000)
    again = await accrue_funding(db, once, t0 + 7_200_000)
    assert again.funding_paid_usdt == pytest.approx(0.1)
    stored = await trades_repo.get_trade(db, trade.id)
    assert stored.funding_paid_usdt == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_events_before_opening_or_after_the_window_are_ignored(db):
    t0 = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    await funding_repo.upsert_funding(db, "BTCUSDT", [
        (t0 - 3_600_000, 0.5),  # antes de abrir
        (t0 + 90 * 60_000, 0.001),  # dentro
        (t0 + 10 * 3_600_000, 0.9),  # despues de la ventana
    ])
    trade = await _insert_open(db, Side.LONG)
    after = await accrue_funding(db, trade, t0 + 2 * 3_600_000)
    assert after.funding_paid_usdt == pytest.approx(0.1)
