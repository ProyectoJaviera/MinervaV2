"""Capa de persistencia (aiosqlite).

Fase 1 creo `trades`, `system_state`, `ohlcv_cache`, `contract_specs_cache`.
Fase 2 agrega `asset_universe`, `backtest_trades`, `backtest_skipped_entries`,
`backtest_runs`, `backtest_verdicts`. El resto del esquema propuesto en
docs/FASE0.md (llm_logs, news_items, lessons_learned, etc.) se crea en la
fase que los necesite, para no mantener tablas vacias sin dueno.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    strategy TEXT,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')) DEFAULT 'OPEN',
    leverage INTEGER NOT NULL,
    margin_usdt REAL NOT NULL,
    notional_usdt REAL NOT NULL,
    qty REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    fee_entry_usdt REAL NOT NULL DEFAULT 0,
    fee_exit_usdt REAL,
    funding_paid_usdt REAL NOT NULL DEFAULT 0,
    pnl_gross_usdt REAL,
    pnl_net_usdt REAL,
    close_reason TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    decision_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_status ON trades (symbol, status);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ohlcv_cache (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    price_type TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    base_vol REAL,
    quote_vol REAL,
    PRIMARY KEY (symbol, interval, price_type, open_time)
);

CREATE TABLE IF NOT EXISTS contract_specs_cache (
    symbol TEXT PRIMARY KEY,
    min_trade_volume REAL,
    base_precision INTEGER,
    quote_precision INTEGER,
    min_leverage INTEGER,
    max_leverage INTEGER,
    default_margin_mode TEXT,
    margin_tiers_json TEXT,
    funding_rate REAL,
    funding_interval_hours INTEGER,
    next_funding_time INTEGER,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_universe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refreshed_at TEXT NOT NULL,
    coingecko_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    coingecko_rank INTEGER,
    market_cap_usd REAL,
    excluded_category TEXT,
    excluded_manual INTEGER NOT NULL DEFAULT 0,
    has_bitunix_perp INTEGER NOT NULL DEFAULT 0,
    price_sanity_ok INTEGER,
    included INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_asset_universe_refreshed ON asset_universe (refreshed_at);

CREATE TABLE IF NOT EXISTS backtest_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    segment TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    qty REAL NOT NULL,
    margin_usdt REAL NOT NULL,
    leverage INTEGER NOT NULL,
    fee_entry_usdt REAL NOT NULL,
    fee_exit_usdt REAL NOT NULL,
    slippage_cost_usdt REAL NOT NULL,
    funding_paid_usdt REAL NOT NULL,
    funding_is_approximated INTEGER NOT NULL,
    pnl_gross_usdt REAL NOT NULL,
    pnl_net_usdt REAL NOT NULL,
    close_reason TEXT NOT NULL,
    sl_margin_loss_pct REAL
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_cell
    ON backtest_trades (strategy, symbol, timeframe, segment);

CREATE TABLE IF NOT EXISTS backtest_skipped_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    segment TEXT NOT NULL,
    ts TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    intended_sl_margin_loss_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    segment TEXT NOT NULL,
    winrate REAL,
    profit_factor REAL,
    pnl_gross_total_usdt REAL NOT NULL DEFAULT 0,
    pnl_net_total_usdt REAL NOT NULL DEFAULT 0,
    max_drawdown_pct REAL,
    expectancy_usdt REAL,
    total_trades INTEGER NOT NULL DEFAULT 0,
    fees_total_usdt REAL NOT NULL DEFAULT 0,
    funding_total_usdt REAL NOT NULL DEFAULT 0,
    funding_real_trades INTEGER NOT NULL DEFAULT 0,
    funding_approx_trades INTEGER NOT NULL DEFAULT 0,
    benchmark_return_pct REAL,
    benchmark_max_drawdown_pct REAL,
    run_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_cell
    ON backtest_runs (strategy, symbol, timeframe, segment);

CREATE TABLE IF NOT EXISTS backtest_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    is_experimental INTEGER NOT NULL DEFAULT 0,
    combos_tested INTEGER NOT NULL DEFAULT 0,
    total_trades_all_segments INTEGER NOT NULL DEFAULT 0,
    pf_oos_aggregate REAL,
    pct_symbols_pf_gt1 REAL,
    pct_folds_positive REAL,
    pf_stressed REAL,
    pf_real_funding_only REAL,
    pf_full_period_approx REAL,
    max_drawdown_oos_pct REAL,
    concentration_pct REAL,
    pf_control_group REAL,
    evidence_insufficient INTEGER NOT NULL DEFAULT 0,
    discarded INTEGER NOT NULL DEFAULT 0,
    discard_reasons_json TEXT,
    run_at TEXT NOT NULL
);
"""


class Database:
    """Wrapper fino sobre una conexion aiosqlite compartida.

    Se usa una sola conexion con un lock de escritura porque SQLite no
    soporta bien escrituras concurrentes desde multiples conexiones; para
    el volumen de Minerva (un bot, no multiusuario) esto es suficiente.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database no conectada; llama a connect() primero.")
        return self._conn

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(os.path.dirname(self.path) or ".").mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self.init_schema()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        async with self._write_lock:
            await self.conn.executescript(SCHEMA)
            await self.conn.commit()

    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            cursor = await self.conn.execute(query, params)
            await self.conn.commit()
            return cursor

    async def fetch_one(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetch_all(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)
