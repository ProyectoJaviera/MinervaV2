from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence.models import OHLCVBar, Side, Trade, TradeStatus
from app.persistence.repositories import ohlcv_repo, trades_repo


@pytest.mark.asyncio
async def test_create_and_get_open_position(db):
    trade = Trade(
        symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21", leverage=10,
        margin_usdt=10.0, notional_usdt=100.0, qty=1.0, entry_price=100.0,
        fee_entry_usdt=0.06, opened_at=datetime.now(UTC),
    )
    created = await trades_repo.create_trade(db, trade)
    assert created.id is not None

    open_positions = await trades_repo.get_open_positions(db, "BTCUSDT")
    assert len(open_positions) == 1
    assert open_positions[0].status == TradeStatus.OPEN


@pytest.mark.asyncio
async def test_close_trade_updates_status_and_pnl(db):
    trade = Trade(
        symbol="ETHUSDT", side=Side.SHORT, leverage=10, margin_usdt=10.0,
        notional_usdt=100.0, qty=0.5, entry_price=200.0, fee_entry_usdt=0.06,
        opened_at=datetime.now(UTC),
    )
    created = await trades_repo.create_trade(db, trade)
    await trades_repo.close_trade(
        db, created.id, exit_price=190.0, fee_exit_usdt=0.057,
        pnl_gross_usdt=5.0, pnl_net_usdt=4.88, close_reason="MANUAL",
    )
    fetched = await trades_repo.get_trade(db, created.id)
    assert fetched.status == TradeStatus.CLOSED
    assert fetched.pnl_net_usdt == pytest.approx(4.88)
    assert fetched.close_reason == "MANUAL"

    open_positions = await trades_repo.get_open_positions(db, "ETHUSDT")
    assert open_positions == []


@pytest.mark.asyncio
async def test_committed_margin_sums_only_open_trades(db):
    t1 = Trade(
        symbol="BTCUSDT", side=Side.LONG, leverage=10, margin_usdt=10.0,
        notional_usdt=100.0, qty=1.0, entry_price=100.0, fee_entry_usdt=0.06,
        opened_at=datetime.now(UTC),
    )
    t2 = Trade(
        symbol="BTCUSDT", side=Side.SHORT, leverage=10, margin_usdt=7.0,
        notional_usdt=70.0, qty=0.7, entry_price=100.0, fee_entry_usdt=0.04,
        opened_at=datetime.now(UTC),
    )
    created1 = await trades_repo.create_trade(db, t1)
    await trades_repo.create_trade(db, t2)
    await trades_repo.close_trade(
        db, created1.id, exit_price=100.0, fee_exit_usdt=0.06,
        pnl_gross_usdt=0.0, pnl_net_usdt=-0.12, close_reason="MANUAL",
    )

    total = await trades_repo.committed_margin(db, "BTCUSDT")
    assert total == pytest.approx(7.0)  # solo t2 sigue abierta


@pytest.mark.asyncio
async def test_ohlcv_upsert_is_idempotent(db):
    bar = OHLCVBar(
        symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE", open_time=1000,
        open=100.0, high=101.0, low=99.0, close=100.5, base_vol=10.0, quote_vol=1000.0,
    )
    await ohlcv_repo.upsert_bars(db, [bar])
    updated_bar = bar.model_copy(update={"close": 105.0})
    await ohlcv_repo.upsert_bars(db, [updated_bar])

    bars = await ohlcv_repo.get_bars(db, "BTCUSDT", "4h", "LAST_PRICE")
    assert len(bars) == 1  # misma PK (symbol, interval, price_type, open_time) -> upsert
    assert bars[0].close == pytest.approx(105.0)


@pytest.mark.asyncio
async def test_get_covered_open_times(db):
    bars = [
        OHLCVBar(
            symbol="BTCUSDT", interval="4h", price_type="LAST_PRICE", open_time=t,
            open=1.0, high=1.0, low=1.0, close=1.0,
        )
        for t in (1000, 2000, 3000)
    ]
    await ohlcv_repo.upsert_bars(db, bars)
    covered = await ohlcv_repo.get_covered_open_times(db, "BTCUSDT", "4h", "LAST_PRICE")
    assert covered == {1000, 2000, 3000}
