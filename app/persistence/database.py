"""Capa de persistencia (aiosqlite).

Fase 1 crea solo las tablas que esta fase usa: `trades`, `system_state`,
`ohlcv_cache`, `contract_specs_cache`. El resto del esquema propuesto en
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
