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

-- Recuerda que ya se alcanzo el piso real del historial de Bitunix para
-- un (symbol, interval, price_type) -- evita redescargar todo cada vez
-- que se pide un start_time anterior al dato mas antiguo disponible.
CREATE TABLE IF NOT EXISTS ohlcv_floor (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    price_type TEXT NOT NULL,
    floor_open_time INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, price_type)
);

-- Cache de funding_rate_history (igual proposito que ohlcv_cache): evita
-- re-descargar en cada corrida/reintento del backtest. `floor_time` (en
-- `ohlcv_floor` con interval='__funding__', price_type='__funding__' para
-- reusar la misma tabla de piso) marca cuando se alcanzo el principio real
-- del historial de funding de Bitunix (mas corto que el de velas, ver
-- docs/FASE2_PLAN.md).
CREATE TABLE IF NOT EXISTS funding_cache (
    symbol TEXT NOT NULL,
    funding_time INTEGER NOT NULL,
    funding_rate REAL NOT NULL,
    PRIMARY KEY (symbol, funding_time)
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
    portfolio_max_drawdown_pct REAL,
    portfolio_concentration_pct REAL,
    portfolio_final_capital_usdt REAL,
    portfolio_trades_included INTEGER NOT NULL DEFAULT 0,
    portfolio_trades_skipped_no_margin INTEGER NOT NULL DEFAULT 0,
    run_at TEXT NOT NULL
);
"""

# Columnas agregadas DESPUES de la primera version de `backtest_verdicts`
# (simulacion de cartera, correccion post-revision). `CREATE TABLE IF NOT
# EXISTS` no las agrega a una base de datos ya existente -- se migran aqui
# con `ALTER TABLE` (idempotente: se saltan si ya existen) para que un
# archivo `minerva.db` de una corrida anterior no quede con el esquema
# viejo.
_BACKTEST_VERDICTS_MIGRATED_COLUMNS = {
    "portfolio_max_drawdown_pct": "REAL",
    "portfolio_concentration_pct": "REAL",
    "portfolio_final_capital_usdt": "REAL",
    "portfolio_trades_included": "INTEGER NOT NULL DEFAULT 0",
    "portfolio_trades_skipped_no_margin": "INTEGER NOT NULL DEFAULT 0",
}


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
        # Seguro combinado con WAL (solo arriesga las ultimas transacciones
        # en un corte de energia, no la integridad de la BD) y reduce mucho
        # el costo de fsync en escrituras por lote -- ver Database.execute_many.
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self.init_schema()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def init_schema(self) -> None:
        async with self._write_lock:
            await self.conn.executescript(SCHEMA)
            await self.conn.commit()
            await self._migrate_backtest_verdicts_columns()

    async def _migrate_backtest_verdicts_columns(self) -> None:
        cursor = await self.conn.execute("PRAGMA table_info(backtest_verdicts)")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for name, sql_type in _BACKTEST_VERDICTS_MIGRATED_COLUMNS.items():
            if name not in existing:
                await self.conn.execute(
                    f"ALTER TABLE backtest_verdicts ADD COLUMN {name} {sql_type}"
                )
        await self.conn.commit()

    async def execute(self, query: str, params: tuple = ()) -> aiosqlite.Cursor:
        async with self._write_lock:
            cursor = await self.conn.execute(query, params)
            await self.conn.commit()
            return cursor

    async def execute_many(self, query: str, params_list: list[tuple]) -> None:
        """Ejecuta la misma consulta para cada tupla de `params_list` en UNA
        sola transaccion (un solo commit), en vez de uno por fila -- evita
        el costo de fsync por fila al insertar en bloque (p.ej. una pagina
        de 200 velas); se detecto como cuello de botella real durante la
        corrida del backtest de Fase 2."""
        if not params_list:
            return
        async with self._write_lock:
            await self.conn.executemany(query, params_list)
            await self.conn.commit()

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
