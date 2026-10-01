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


class AssetUniverseEntry(BaseModel):
    """Una fila del snapshot de universo dinamico (Fase 2). Se guarda una
    fila por moneda evaluada en cada refresco, incluidas las excluidas, para
    que la exclusion quede auditable (docs/FASE2_PLAN.md, seccion D)."""

    id: int | None = None
    refreshed_at: datetime
    coingecko_id: str
    symbol: str  # simbolo de Bitunix propuesto, p.ej. "BTCUSDT" (puede no existir)
    coingecko_rank: int | None = None
    market_cap_usd: float | None = None
    excluded_category: str | None = None  # p.ej. "stablecoins", None si no aplica
    excluded_manual: bool = False
    has_bitunix_perp: bool = False
    price_sanity_ok: bool | None = None
    included: bool = False


class BacktestTrade(BaseModel):
    """Una operacion individual simulada por el motor de backtest (Fase 2).
    `segment` identifica a que particion pertenece: "IS", "OOS" o
    "FOLD_<n>" (walk-forward, ver docs/FASE2_PLAN.md)."""

    id: int | None = None
    strategy: str
    symbol: str
    timeframe: str
    segment: str
    side: Side
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: float
    margin_usdt: float
    leverage: int
    fee_entry_usdt: float
    fee_exit_usdt: float
    slippage_cost_usdt: float
    funding_paid_usdt: float
    funding_is_approximated: bool
    pnl_gross_usdt: float
    pnl_net_usdt: float
    close_reason: str
    sl_margin_loss_pct: float | None = None


class BacktestSkippedEntry(BaseModel):
    """Senal que la estrategia emitio pero el motor omitio por exceder el
    tope de riesgo por operacion (punto 1 de los ajustes de Fase 2)."""

    id: int | None = None
    strategy: str
    symbol: str
    timeframe: str
    segment: str
    ts: datetime
    side: Side
    intended_sl_margin_loss_pct: float


class BacktestRun(BaseModel):
    """Metricas agregadas de una celda (estrategia, simbolo, timeframe,
    segmento) -- ver docs/FASE0.md / docs/FASE2_PLAN.md."""

    id: int | None = None
    strategy: str
    symbol: str
    timeframe: str
    segment: str
    winrate: float | None = None
    profit_factor: float | None = None
    pnl_gross_total_usdt: float = 0.0
    pnl_net_total_usdt: float = 0.0
    max_drawdown_pct: float | None = None
    expectancy_usdt: float | None = None
    total_trades: int = 0
    fees_total_usdt: float = 0.0
    funding_total_usdt: float = 0.0
    funding_real_trades: int = 0
    funding_approx_trades: int = 0
    benchmark_return_pct: float | None = None
    benchmark_max_drawdown_pct: float | None = None
    run_at: datetime


class BacktestVerdict(BaseModel):
    """Veredicto global por estrategia (agrega todos los simbolos/timeframes/
    folds OOS). Se guarda TODO, aprobadas y descartadas -- registro auditable
    (docs/FASE2_PLAN.md, seccion C)."""

    id: int | None = None
    strategy: str
    is_experimental: bool = False
    combos_tested: int = 0
    total_trades_all_segments: int = 0
    pf_oos_aggregate: float | None = None
    pct_symbols_pf_gt1: float | None = None
    pct_folds_positive: float | None = None
    pf_stressed: float | None = None
    pf_real_funding_only: float | None = None
    pf_full_period_approx: float | None = None
    max_drawdown_oos_pct: float | None = None
    concentration_pct: float | None = None
    pf_control_group: float | None = None
    evidence_insufficient: bool = False
    discarded: bool = False
    discard_reasons_json: str | None = None
    run_at: datetime
