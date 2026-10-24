"""Repositorio de las tablas del backtest (Fase 2).

Tablas: `backtest_trades`, `backtest_skipped_entries`, `backtest_runs` y
`backtest_verdicts`. Los `INSERT` generan los `?` de `VALUES` a partir de la lista de
columnas (nunca escritos a mano): un desajuste entre columnas y `?` causo un error real
("29 values for 30 columns") en `insert_verdict` al agregar la simulacion de cartera
Monte Carlo. Con `_insert(table, columns, values)` ese desajuste ya no es posible, y un
`assert` detecta si `values` no tiene la misma longitud que `columns`.
"""

from __future__ import annotations

from datetime import datetime

from app.persistence.database import Database
from app.persistence.models import (
    BacktestRun,
    BacktestSkippedEntry,
    BacktestTrade,
    BacktestVerdict,
    Side,
)


async def _insert(db: Database, table: str, columns: tuple[str, ...], values: tuple) -> None:
    assert len(values) == len(columns), (
        f"{table}: {len(values)} valores para {len(columns)} columnas ({columns})"
    )
    placeholders = ", ".join(["?"] * len(columns))
    columns_sql = ", ".join(columns)
    # `table` y `columns` son siempre constantes fijas del modulo, nunca
    # input de usuario.
    await db.execute(
        f"INSERT INTO {table} ({columns_sql}) VALUES ({placeholders})", values
    )


async def clear_results(db: Database) -> None:
    """Borra los resultados de corridas anteriores antes de una corrida completa nueva.

    Hace que reintentar tras una interrupcion (p. ej. un colgado de red) sea seguro e
    idempotente en vez de acumular filas duplicadas. NO borra `asset_universe`,
    `contract_specs_cache`, `ohlcv_cache` ni `funding_cache`: son caches reutilizables.

    Args:
        db: conexion a la base.

    Efectos:
        Escribe (DELETE de todas las filas) `backtest_trades`, `backtest_skipped_entries`,
        `backtest_runs` y `backtest_verdicts`.
    """
    tables = ("backtest_trades", "backtest_skipped_entries", "backtest_runs", "backtest_verdicts")
    for table in tables:
        await db.execute(f"DELETE FROM {table}")  # nombres de tabla fijos, no son input de usuario


_TRADE_COLUMNS = (
    "strategy", "symbol", "timeframe", "segment", "side", "entry_time", "exit_time",
    "entry_price", "exit_price", "qty", "margin_usdt", "leverage", "fee_entry_usdt",
    "fee_exit_usdt", "slippage_cost_usdt", "funding_paid_usdt",
    "funding_is_approximated", "pnl_gross_usdt", "pnl_net_usdt", "close_reason",
    "sl_margin_loss_pct",
)


def _trade_values(t: BacktestTrade) -> tuple:
    return (
        t.strategy, t.symbol, t.timeframe, t.segment, t.side.value,
        t.entry_time.isoformat(), t.exit_time.isoformat(), t.entry_price,
        t.exit_price, t.qty, t.margin_usdt, t.leverage, t.fee_entry_usdt,
        t.fee_exit_usdt, t.slippage_cost_usdt, t.funding_paid_usdt,
        int(t.funding_is_approximated), t.pnl_gross_usdt, t.pnl_net_usdt,
        t.close_reason, t.sl_margin_loss_pct,
    )


async def insert_trade(db: Database, t: BacktestTrade) -> None:
    """Guarda una operacion simulada del backtest.

    Args:
        db: conexion a la base.
        t: la operacion.

    Efectos:
        Escribe `backtest_trades` (INSERT).
    """
    await _insert(db, "backtest_trades", _TRADE_COLUMNS, _trade_values(t))


async def insert_trades(db: Database, trades: list[BacktestTrade]) -> None:
    """Guarda un lote de operaciones simuladas con un solo commit.

    Args:
        db: conexion a la base.
        trades: operaciones a guardar; con la lista vacia no hace nada.

    Efectos:
        Escribe `backtest_trades` (INSERT en bloque).
    """
    if not trades:
        return
    placeholders = ", ".join(["?"] * len(_TRADE_COLUMNS))
    columns_sql = ", ".join(_TRADE_COLUMNS)
    params = [_trade_values(t) for t in trades]
    await db.execute_many(
        f"INSERT INTO backtest_trades ({columns_sql}) VALUES ({placeholders})", params
    )


_SKIPPED_ENTRY_COLUMNS = (
    "strategy", "symbol", "timeframe", "segment", "ts", "side", "intended_sl_margin_loss_pct",
)


async def insert_skipped_entry(db: Database, s: BacktestSkippedEntry) -> None:
    """Guarda una entrada que el motor omitio por exceder el tope de riesgo por operacion.

    Args:
        db: conexion a la base.
        s: la entrada omitida.

    Efectos:
        Escribe `backtest_skipped_entries` (INSERT).
    """
    await _insert(
        db, "backtest_skipped_entries", _SKIPPED_ENTRY_COLUMNS,
        (s.strategy, s.symbol, s.timeframe, s.segment, s.ts.isoformat(), s.side.value,
         s.intended_sl_margin_loss_pct),
    )


_RUN_COLUMNS = (
    "strategy", "symbol", "timeframe", "segment", "winrate", "profit_factor",
    "pnl_gross_total_usdt", "pnl_net_total_usdt", "max_drawdown_pct", "expectancy_usdt",
    "total_trades", "fees_total_usdt", "funding_total_usdt", "funding_real_trades",
    "funding_approx_trades", "benchmark_return_pct", "benchmark_max_drawdown_pct", "run_at",
)


async def insert_run(db: Database, r: BacktestRun) -> None:
    """Guarda las metricas agregadas de una celda (estrategia, simbolo, timeframe, segmento).

    Args:
        db: conexion a la base.
        r: las metricas de la celda.

    Efectos:
        Escribe `backtest_runs` (INSERT).
    """
    await _insert(
        db, "backtest_runs", _RUN_COLUMNS,
        (
            r.strategy, r.symbol, r.timeframe, r.segment, r.winrate, r.profit_factor,
            r.pnl_gross_total_usdt, r.pnl_net_total_usdt, r.max_drawdown_pct,
            r.expectancy_usdt, r.total_trades, r.fees_total_usdt, r.funding_total_usdt,
            r.funding_real_trades, r.funding_approx_trades, r.benchmark_return_pct,
            r.benchmark_max_drawdown_pct, r.run_at.isoformat(),
        ),
    )


async def insert_verdict(db: Database, v: BacktestVerdict) -> None:
    """Guarda el veredicto global de una estrategia.

    A diferencia de las demas funciones del archivo, construye las columnas Y los valores a
    partir de `v.model_dump()` en vez de listas paralelas escritas a mano: `BacktestVerdict`
    es, con mucho, el modelo que mas crecio (de 17 a mas de 36 columnas en tres revisiones) y
    es donde ocurrio el bug real. Agregar un campo al modelo alcanza por si solo, aunque la
    columna tambien debe existir en `SCHEMA` y en la lista de migracion.

    Args:
        db: conexion a la base.
        v: el veredicto.

    Efectos:
        Escribe `backtest_verdicts` (INSERT).
    """
    data = v.model_dump(exclude={"id"})
    data["is_experimental"] = int(data["is_experimental"])
    data["evidence_insufficient"] = int(data["evidence_insufficient"])
    data["discarded"] = int(data["discarded"])
    data["run_at"] = v.run_at.isoformat()
    await _insert(db, "backtest_verdicts", tuple(data.keys()), tuple(data.values()))


def _row_to_trade(row) -> BacktestTrade:
    return BacktestTrade(
        id=row["id"], strategy=row["strategy"], symbol=row["symbol"],
        timeframe=row["timeframe"], segment=row["segment"], side=Side(row["side"]),
        entry_time=datetime.fromisoformat(row["entry_time"]),
        exit_time=datetime.fromisoformat(row["exit_time"]),
        entry_price=row["entry_price"], exit_price=row["exit_price"], qty=row["qty"],
        margin_usdt=row["margin_usdt"], leverage=row["leverage"],
        fee_entry_usdt=row["fee_entry_usdt"], fee_exit_usdt=row["fee_exit_usdt"],
        slippage_cost_usdt=row["slippage_cost_usdt"], funding_paid_usdt=row["funding_paid_usdt"],
        funding_is_approximated=bool(row["funding_is_approximated"]),
        pnl_gross_usdt=row["pnl_gross_usdt"], pnl_net_usdt=row["pnl_net_usdt"],
        close_reason=row["close_reason"], sl_margin_loss_pct=row["sl_margin_loss_pct"],
    )


_VERDICT_BOOL_COLUMNS = ("is_experimental", "evidence_insufficient", "discarded")


def _row_to_verdict(row) -> BacktestVerdict:
    """Igual que `insert_verdict`: construye los kwargs a partir de las
    columnas de la fila (que, por convencion, se llaman igual que los
    campos del modelo) en vez de enumerarlos a mano uno por uno -- un
    campo nuevo en `BacktestVerdict` no requiere tocar esta funcion."""
    data = {key: row[key] for key in row.keys()}
    for column in _VERDICT_BOOL_COLUMNS:
        data[column] = bool(data[column])
    data["run_at"] = datetime.fromisoformat(data["run_at"])
    # Columnas "v1" (simulacion de cartera de una sola corrida) que
    # pueden existir en una base de datos migrada pero ya no tienen campo
    # correspondiente en el modelo -- se descartan en vez de fallar.
    model_fields = set(BacktestVerdict.model_fields)
    data = {k: v for k, v in data.items() if k in model_fields}
    return BacktestVerdict(**data)


async def get_verdicts(db: Database, strategy: str | None = None) -> list[BacktestVerdict]:
    """Los veredictos del backtest, opcionalmente de una estrategia.

    Args:
        db: conexion a la base.
        strategy: si se da, solo esa estrategia.

    Returns:
        Lista de `BacktestVerdict` por `id` ascendente. Las columnas "v1" que ya no tienen
        campo en el modelo se descartan.

    Efectos:
        Lee `backtest_verdicts`.
    """
    query = "SELECT * FROM backtest_verdicts"
    params: tuple = ()
    if strategy:
        query += " WHERE strategy = ?"
        params = (strategy,)
    query += " ORDER BY id ASC"
    rows = await db.fetch_all(query, params)
    return [_row_to_verdict(r) for r in rows]


async def get_trades(
    db: Database,
    strategy: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    segment: str | None = None,
) -> list[BacktestTrade]:
    """Las operaciones simuladas de una estrategia, con filtros opcionales.

    Args:
        db: conexion a la base.
        strategy: estrategia.
        symbol: si se da, solo ese simbolo.
        timeframe: si se da, solo ese timeframe.
        segment: si se da, solo ese segmento (`IS`, `OOS`, `FOLD_<n>`).

    Returns:
        Lista de `BacktestTrade` por `entry_time` ascendente.

    Efectos:
        Lee `backtest_trades`.
    """
    query = "SELECT * FROM backtest_trades WHERE strategy = ?"
    params: list = [strategy]
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if timeframe:
        query += " AND timeframe = ?"
        params.append(timeframe)
    if segment:
        query += " AND segment = ?"
        params.append(segment)
    query += " ORDER BY entry_time ASC"
    rows = await db.fetch_all(query, tuple(params))
    return [_row_to_trade(r) for r in rows]
