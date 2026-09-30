from __future__ import annotations

import pytest

from app.config import Settings
from app.execution.paper_backend import InsufficientRiskBudgetError, PaperBackend
from app.persistence.models import ContractSpec, Side
from app.persistence.repositories import specs_repo


class FakeRestClient:
    """Sustituye a BitunixRestClient en los tests: sin red, precio fijo controlable."""

    def __init__(self, price: float) -> None:
        self.price = price

    async def get_tickers(self, symbol: str | None = None) -> list[dict]:
        return [{"symbol": symbol, "lastPrice": str(self.price), "markPrice": str(self.price)}]


def make_settings(**overrides) -> Settings:
    defaults = dict(
        DEFAULT_MARGIN_USDT=10.0,
        LEVERAGE=10,
        TAKER_FEE_PCT=0.0006,
        MAKER_FEE_PCT=0.0002,
        INITIAL_CAPITAL_USDT=100.0,
        MAX_CAPITAL_PCT_PER_ASSET=0.10,
        MAX_SIMULTANEOUS_POSITIONS=3,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_open_and_close_long_position_computes_pnl_and_fees(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)

    trade = await backend.open_position("BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10)
    assert trade.qty == pytest.approx(1.0)  # notional 100 / precio 100
    assert trade.fee_entry_usdt == pytest.approx(100.0 * 0.0006)

    rest_client.price = 110.0  # +10% -> +100% ROI sobre el margen a 10x
    closed = await backend.close_position(trade.id, reason="MANUAL")

    expected_gross = (110.0 - 100.0) * 1.0  # 10 USDT
    expected_fee_exit = 110.0 * 0.0006
    expected_net = expected_gross - trade.fee_entry_usdt - expected_fee_exit

    assert closed.pnl_gross_usdt == pytest.approx(expected_gross)
    assert closed.pnl_net_usdt == pytest.approx(expected_net)
    assert closed.status.value == "CLOSED"


@pytest.mark.asyncio
async def test_open_and_close_short_position_profits_on_price_drop(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)

    trade = await backend.open_position("BTCUSDT", Side.SHORT, margin_usdt=10.0, leverage=10)
    rest_client.price = 90.0
    closed = await backend.close_position(trade.id, reason="MANUAL")

    expected_gross = (100.0 - 90.0) * trade.qty
    assert closed.pnl_gross_usdt == pytest.approx(expected_gross)
    assert closed.pnl_gross_usdt > 0


@pytest.mark.asyncio
async def test_cannot_close_already_closed_trade(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)
    trade = await backend.open_position("BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10)
    await backend.close_position(trade.id)
    with pytest.raises(ValueError):
        await backend.close_position(trade.id)


@pytest.mark.asyncio
async def test_margin_capped_at_10pct_per_asset(db):
    """100 USDT de capital, tope 10% por activo: pedir 50 USDT de margen debe
    ajustarse a 10 USDT (ver docs/FASE0.md, verificacion de coherencia)."""
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)

    trade = await backend.open_position("BTCUSDT", Side.LONG, margin_usdt=50.0, leverage=10)
    assert trade.margin_usdt == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_rejects_qty_below_contract_minimum(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    await specs_repo.upsert_spec(
        db,
        ContractSpec(
            symbol="BTCUSDT",
            min_trade_volume=10.0,  # muy por encima de la qty que produciria este margen
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        ),
    )
    backend = PaperBackend(db, rest_client, settings)

    with pytest.raises(InsufficientRiskBudgetError):
        await backend.open_position("BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10)


@pytest.mark.asyncio
async def test_get_balance_reflects_realized_pnl(db):
    settings = make_settings()
    rest_client = FakeRestClient(price=100.0)
    backend = PaperBackend(db, rest_client, settings)

    assert await backend.get_balance() == pytest.approx(100.0)

    trade = await backend.open_position("BTCUSDT", Side.LONG, margin_usdt=10.0, leverage=10)
    rest_client.price = 110.0
    closed = await backend.close_position(trade.id)

    balance = await backend.get_balance()
    assert balance == pytest.approx(100.0 + closed.pnl_net_usdt)
