"""Modelos pydantic que reflejan las filas de la base de datos."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class Trade(BaseModel):
    id: int | None = None
    symbol: str
    side: Side
    strategy: str | None = None
    status: TradeStatus = TradeStatus.OPEN
    leverage: int
    margin_usdt: float
    notional_usdt: float
    qty: float
    entry_price: float
    exit_price: float | None = None
    fee_entry_usdt: float = 0.0
    fee_exit_usdt: float | None = None
    funding_paid_usdt: float = 0.0
    pnl_gross_usdt: float | None = None
    pnl_net_usdt: float | None = None
    close_reason: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    decision_json: str | None = None


class OHLCVBar(BaseModel):
    symbol: str
    interval: str
    price_type: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    base_vol: float | None = None
    quote_vol: float | None = None


class ContractSpec(BaseModel):
    symbol: str
    min_trade_volume: float | None = None
    base_precision: int | None = None
    quote_precision: int | None = None
    min_leverage: int | None = None
    max_leverage: int | None = None
    default_margin_mode: str | None = None
    margin_tiers_json: str | None = None
    funding_rate: float | None = None
    funding_interval_hours: int | None = None
    next_funding_time: int | None = None
    fetched_at: datetime
