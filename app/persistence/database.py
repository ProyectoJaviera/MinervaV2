"""Capa de persistencia (aiosqlite).

Fase 1 creo `trades`, `system_state`, `ohlcv_cache`, `contract_specs_cache`.
Fase 2 agrega `asset_universe`, `backtest_trades`, `backtest_skipped_entries`,
`backtest_runs`, `backtest_verdicts`. Fase 3 subfase 3.2 agrega
`risk_rejections`; subfase 3.3 agrega `signals` y
`signals_discarded_by_sl_cap`; subfase 3.5 agrega `shadow_trades`; subfase 3.6
agrega `llm_logs` y el disparador `llm_decision_inmutable`. El resto del
esquema propuesto en docs/FASE0.md (news_items, lessons_learned, etc.) se
crea en la fase que lo necesite, para no mantener tablas vacias sin dueno.
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

-- Cada rechazo del motor de riesgo en vivo (Fase 3, subfase 3.2) a una
-- entrada nueva, con el motivo y los valores exactos involucrados, para
-- poder auditarlo despues. Nunca se usa para cierres (SL/TP/liquidacion
-- ni cierre manual) -- el motor de riesgo solo bloquea entradas nuevas.
CREATE TABLE IF NOT EXISTS risk_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy TEXT,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_rejections_reason ON risk_rejections (reason);

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

-- Marca afirmativa de "esta serie se descargo completa" escrita por
-- scripts/download_history.py al terminar cada serie SIN errores --
-- corrige un bug real donde `ohlcv_floor` podia no tener fila (p.ej. una
-- descarga anterior interrumpida) aunque el rango pedido SI estuviera
-- completo, causando un MissingHistoricalDataError evitable. Es una señal
-- mas fuerte que inferir a partir de `ohlcv_floor` + el rango cacheado:
-- solo existe si `download_history.py` llego al final de esa serie sin
-- lanzar ninguna excepcion. `get_cached_or_raise` la usa para el extremo
-- "cabeza" (dato antiguo) ademas del chequeo de piso existente, no lo
-- reemplaza.
CREATE TABLE IF NOT EXISTS ohlcv_series_complete (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    price_type TEXT NOT NULL,
    start_time INTEGER NOT NULL,
    end_time INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, price_type)
);

-- Huecos puntuales que `fill_gaps` ya intento reparar con un pedido estrecho
-- y SIGUEN sin aparecer (incidente de estabilidad 2026-10-10, "Añadido 0" --
-- ver docs/FASE2_INTEGRIDAD_VELAS.md). Evita que `_verify_and_repair` los
-- reintente contra la API en cada ciclo del generador de señales: los vuelve
-- a intentar solo despues de `GAP_RETRY_COOLDOWN_HOURS` (app/market/
-- ohlcv_history.py), y se borra la fila si un reintento SI lo recupera.
CREATE TABLE IF NOT EXISTS ohlcv_unrepairable_gaps (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    price_type TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_attempted_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (symbol, interval, price_type, open_time)
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

-- Generador de senales en vivo (Fase 3, subfase 3.3) -- una fila por CADA
-- evaluacion (simbolo, estrategia, timeframe, vela cerrada), HOLD incluido
-- (registro auditable completo, ver `app/trading/signal_generator.py`).
-- `UNIQUE` + `INSERT OR IGNORE` evita duplicar la fila si el scheduler
-- vuelve a evaluar la misma vela ya cerrada en un poll posterior.
-- `sl_margin_loss_pct` NO esta en la lista de columnas de docs/FASE3_PLAN.md
-- punto 1, pero se agrega aqui para auditar el MISMO numero que decide
-- `status`/`signals_discarded_by_sl_cap` sin tener que recalcularlo despues.
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    is_experimental INTEGER NOT NULL DEFAULT 0,
    timeframe TEXT NOT NULL,
    candle_close_time TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('LONG', 'SHORT', 'HOLD')),
    price_at_eval REAL NOT NULL,
    stop_price REAL,
    take_profit_price REAL,
    trailing_distance REAL,
    funding_rate_pct REAL,
    funding_is_approximated INTEGER NOT NULL DEFAULT 0,
    sl_margin_loss_pct REAL,
    indicators_json TEXT,
    status TEXT,
    reason TEXT,
    UNIQUE (symbol, strategy, timeframe, candle_close_time)
);

CREATE INDEX IF NOT EXISTS idx_signals_actionable
    ON signals (symbol, signal, candle_close_time);

-- Senal accionable (LONG/SHORT) cuyo `sl_margin_loss_pct` supero
-- `LIVE_SL_MARGIN_CAP_PCT` -- se descarta ANTES de cualquier simulacion o
-- decision del LLM (docs/FASE3_PLAN.md punto 2). `signal_id` referencia la
-- fila ya existente en `signals` (que queda con `status='DISCARDED_SL_CAP'`,
-- nunca se borra -- sigue siendo el registro auditable completo).
CREATE TABLE IF NOT EXISTS signals_discarded_by_sl_cap (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    candle_close_time TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    sl_margin_loss_pct REAL NOT NULL,
    cap_pct REAL NOT NULL,
    created_at TEXT NOT NULL
);

-- Salud de las fuentes de datos en vivo (subfase 3.4, punto 7 de
-- docs/FASE3_PLAN.md): ultimo exito, ultimo error y fallos seguidos. Hoy la
-- fuente `ws_feed` es el latido del WebSocket publico (cualquier mensaje del
-- servidor, incluido el pong) -- si se queda sin latido, no se abren entradas.
CREATE TABLE IF NOT EXISTS data_source_health (
    source TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

-- Operaciones sombra (subfase 3.5, docs/FASE3_PLAN.md punto 2): una por senal
-- agrupada (simbolo, direccion, vela), simuladas con margen ilimitado. `signal_group_key`
-- es UNICA: la misma senal agrupada nunca genera dos operaciones sombra. `llm_decision`
-- queda SIN_LLM hasta la subfase 3.6 (APROBADA/RECHAZADA cuando exista el LLM).
CREATE TABLE IF NOT EXISTS shadow_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    strategy TEXT NOT NULL,
    contributing_strategies TEXT NOT NULL,
    signal_group_key TEXT NOT NULL UNIQUE,
    candle_close_time TEXT NOT NULL,
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
    slippage_entry_usdt REAL NOT NULL DEFAULT 0,
    slippage_exit_usdt REAL NOT NULL DEFAULT 0,
    pnl_gross_usdt REAL,
    pnl_net_usdt REAL,
    close_reason TEXT,
    fill_source TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    sl_price REAL,
    tp_price REAL,
    trailing_distance REAL,
    effective_stop REAL,
    best_price REAL,
    liq_price REAL,
    sl_margin_loss_pct REAL,
    funding_is_approximated INTEGER NOT NULL DEFAULT 0,
    funding_last_applied_ms INTEGER,
    llm_decision TEXT NOT NULL DEFAULT 'SIN_LLM'
        CHECK (llm_decision IN ('APROBADA', 'RECHAZADA', 'SIN_LLM')),
    executed_in_real_account INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shadow_trades_status ON shadow_trades (status, symbol);

-- Decisiones del LLM sobre cada senal agrupada (subfase 3.6, docs/FASE3_6_LLM.md
-- seccion c). Una fila por llamada, exitosa o no. `signal_group_key` es UNICA: una
-- sola llamada por grupo, sin reintentos. `fase` separa el piloto (excluido del
-- analisis) de la medicion.
CREATE TABLE IF NOT EXISTS llm_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    signal_group_key TEXT NOT NULL UNIQUE,
    shadow_trade_id INTEGER,
    fase TEXT NOT NULL CHECK (fase IN ('PILOTO', 'MEDICION')),
    candle_close_time TEXT NOT NULL,
    decision_delay_s REAL NOT NULL,
    hour_utc INTEGER NOT NULL,
    atr_pct REAL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    prompt TEXT NOT NULL,
    response_raw TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('OK', 'TIMEOUT', 'ERROR_HTTP', 'INVALID', 'BUDGET_EXCEEDED')),
    error TEXT,
    decision TEXT CHECK (decision IN ('APROBAR', 'RECHAZAR')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_llm_logs_created_at ON llm_logs (created_at);

-- Etiqueta inmutable (docs/FASE3_6_LLM.md seccion d): una vez decidida
-- (SIN_LLM -> APROBADA/RECHAZADA), no se puede volver a cambiar. El codigo ya
-- filtra con `WHERE llm_decision = 'SIN_LLM'` al actualizar; este disparador lo
-- garantiza tambien a nivel de base.
CREATE TRIGGER IF NOT EXISTS llm_decision_inmutable
BEFORE UPDATE OF llm_decision ON shadow_trades
WHEN OLD.llm_decision <> 'SIN_LLM' AND NEW.llm_decision <> OLD.llm_decision
BEGIN
    SELECT RAISE(ABORT, 'llm_decision es inmutable una vez decidida');
END;

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
    pf_is_aggregate REAL,
    is_trades_count INTEGER NOT NULL DEFAULT 0,
    oos_trades_count INTEGER NOT NULL DEFAULT 0,
    portfolio_simulation_runs INTEGER NOT NULL DEFAULT 0,
    portfolio_final_capital_median REAL,
    portfolio_final_capital_p10 REAL,
    portfolio_final_capital_p90 REAL,
    portfolio_max_drawdown_median REAL,
    portfolio_max_drawdown_p10 REAL,
    portfolio_max_drawdown_p90 REAL,
    portfolio_mtm_max_drawdown_median REAL,
    portfolio_mtm_max_drawdown_p10 REAL,
    portfolio_mtm_max_drawdown_p90 REAL,
    portfolio_concentration_pct_median REAL,
    portfolio_trades_included_median REAL,
    portfolio_trades_skipped_no_margin_median REAL,
    portfolio_oos_simulation_runs INTEGER NOT NULL DEFAULT 0,
    portfolio_oos_final_capital_median REAL,
    portfolio_oos_final_capital_p10 REAL,
    portfolio_oos_final_capital_p90 REAL,
    portfolio_oos_max_drawdown_median REAL,
    portfolio_oos_max_drawdown_p10 REAL,
    portfolio_oos_max_drawdown_p90 REAL,
    portfolio_oos_mtm_max_drawdown_median REAL,
    portfolio_oos_mtm_max_drawdown_p10 REAL,
    portfolio_oos_mtm_max_drawdown_p90 REAL,
    portfolio_oos_concentration_pct_median REAL,
    portfolio_oos_trades_included_median REAL,
    portfolio_oos_trades_skipped_no_margin_median REAL,
    run_at TEXT NOT NULL
);
"""

# Columnas agregadas DESPUES de la primera version de `backtest_verdicts`.
# `CREATE TABLE IF NOT EXISTS` no las agrega a una base de datos ya
# existente -- se migran aqui con `ALTER TABLE` (idempotente: se saltan si
# ya existen) para que un archivo `minerva.db` de una corrida anterior no
# quede con el esquema viejo.
_BACKTEST_VERDICTS_MIGRATED_COLUMNS = {
    # V1: simulacion de cartera de una sola corrida (reemplazada por la
    # version Monte Carlo de abajo; se deja la columna por compatibilidad
    # con bases de datos existentes, sin usarla mas).
    "portfolio_max_drawdown_pct": "REAL",
    "portfolio_concentration_pct": "REAL",
    "portfolio_final_capital_usdt": "REAL",
    "portfolio_trades_included": "INTEGER NOT NULL DEFAULT 0",
    "portfolio_trades_skipped_no_margin": "INTEGER NOT NULL DEFAULT 0",
    # V2: simulacion de cartera Monte Carlo (barajando el desempate de
    # entry_time) con mediana/p10/p90 y drawdown mark-to-market.
    "portfolio_simulation_runs": "INTEGER NOT NULL DEFAULT 0",
    "portfolio_final_capital_median": "REAL",
    "portfolio_final_capital_p10": "REAL",
    "portfolio_final_capital_p90": "REAL",
    "portfolio_max_drawdown_median": "REAL",
    "portfolio_max_drawdown_p10": "REAL",
    "portfolio_max_drawdown_p90": "REAL",
    "portfolio_mtm_max_drawdown_median": "REAL",
    "portfolio_mtm_max_drawdown_p10": "REAL",
    "portfolio_mtm_max_drawdown_p90": "REAL",
    "portfolio_concentration_pct_median": "REAL",
    "portfolio_trades_included_median": "REAL",
    "portfolio_trades_skipped_no_margin_median": "REAL",
    # V3: informe de degradacion IS/OOS y simulacion de cartera solo-OOS
    # (tarea 4, segunda revision de Fase 2).
    "pf_is_aggregate": "REAL",
    "is_trades_count": "INTEGER NOT NULL DEFAULT 0",
    "oos_trades_count": "INTEGER NOT NULL DEFAULT 0",
    "portfolio_oos_simulation_runs": "INTEGER NOT NULL DEFAULT 0",
    "portfolio_oos_final_capital_median": "REAL",
    "portfolio_oos_final_capital_p10": "REAL",
    "portfolio_oos_final_capital_p90": "REAL",
    "portfolio_oos_max_drawdown_median": "REAL",
    "portfolio_oos_max_drawdown_p10": "REAL",
    "portfolio_oos_max_drawdown_p90": "REAL",
    "portfolio_oos_mtm_max_drawdown_median": "REAL",
    "portfolio_oos_mtm_max_drawdown_p10": "REAL",
    "portfolio_oos_mtm_max_drawdown_p90": "REAL",
    "portfolio_oos_concentration_pct_median": "REAL",
    "portfolio_oos_trades_included_median": "REAL",
    "portfolio_oos_trades_skipped_no_margin_median": "REAL",
}


# Columnas agregadas a `trades` en las subfases 3.3-3.4 (ver `_migrate_columns`):
# niveles de riesgo planeados al abrir (SL/TP/trailing/liquidacion, lo que el
# monitor de posiciones evalua en vivo), estado de trailing, funding ya
# aplicado (marca de tiempo para no contarlo dos veces) y la fuente de la
# decision que abrio la posicion (SIN_LLM hasta la subfase 3.6).
_TRADES_MIGRATED_COLUMNS = {
    "sl_price": "REAL",
    "tp_price": "REAL",
    "trailing_distance": "REAL",
    "effective_stop": "REAL",
    "best_price": "REAL",
    "liq_price": "REAL",
    "sl_margin_loss_pct": "REAL",
    "funding_is_approximated": "INTEGER NOT NULL DEFAULT 0",
    "funding_last_applied_ms": "INTEGER",
    "decision_source": "TEXT",
    "slippage_entry_usdt": "REAL NOT NULL DEFAULT 0",
    "slippage_exit_usdt": "REAL NOT NULL DEFAULT 0",
    "fill_source": "TEXT",
    # Incidente de estabilidad 2026-10-10, Etapa 2 (docs/FASE2_INTEGRIDAD_VELAS.md):
    # una reconciliacion fallida marca la posicion en vez de cerrarla a ciegas por
    # precio en vivo. Las REALES siguen vigiladas en vivo pero marcadas (el cierre
    # por tick usa `fill_source='TICK_UNRECONCILED'`, auditable); las sombras se
    # congelan (ver `_SHADOW_TRADES_MIGRATED_COLUMNS`, mismo set de columnas).
    "reconciliation_failed": "INTEGER NOT NULL DEFAULT 0",
    "reconciliation_attempts": "INTEGER NOT NULL DEFAULT 0",
    # Revision de Etapa 2 (correcciones 1 y 2): fijadas solo en la primera
    # falla de la racha, para que los reintentos resuman desde ahi (no desde
    # `opened_at`) y el minimo de horas antes de rendirse se mida desde el
    # inicio real de la racha, no desde el ultimo reintento.
    "reconciliation_window_start_ms": "INTEGER",
    "reconciliation_first_failed_at_ms": "INTEGER",
}

_SIGNALS_MIGRATED_COLUMNS = {
    "reason": "TEXT",
}

_SHADOW_TRADES_MIGRATED_COLUMNS = {
    "reconciliation_failed": "INTEGER NOT NULL DEFAULT 0",
    "reconciliation_attempts": "INTEGER NOT NULL DEFAULT 0",
    "reconciliation_window_start_ms": "INTEGER",
    "reconciliation_first_failed_at_ms": "INTEGER",
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
            await self._migrate_columns("backtest_verdicts", _BACKTEST_VERDICTS_MIGRATED_COLUMNS)
            await self._migrate_columns("trades", _TRADES_MIGRATED_COLUMNS)
            await self._migrate_columns("signals", _SIGNALS_MIGRATED_COLUMNS)
            await self._migrate_columns("shadow_trades", _SHADOW_TRADES_MIGRATED_COLUMNS)

    async def _migrate_columns(self, table: str, columns: dict[str, str]) -> None:
        """`CREATE TABLE IF NOT EXISTS` no agrega columnas a una tabla que ya
        existe (p.ej. `minerva.db` de una corrida anterior) -- se agregan
        aqui con `ALTER TABLE` (idempotente: se saltan las que ya estan).
        Debe correr despues de `executescript(SCHEMA)`, dentro del mismo lock."""
        cursor = await self.conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cursor.fetchall()}
        await cursor.close()
        for name, sql_type in columns.items():
            if name not in existing:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
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
