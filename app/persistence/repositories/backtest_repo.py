"""Repositorio de las tablas de backtesting (Fase 2): `backtest_trades`,
`backtest_skipped_entries`, `backtest_runs`, `backtest_verdicts`."""

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


async def insert_trade(db: Database, t: BacktestTrade) -> None:
    await db.execute(
        """
        INSERT INTO backtest_trades (
            strategy, symbol, timeframe, segment, side, entry_time, exit_time,
            entry_price, exit_price, qty, margin_usdt, leverage, fee_entry_usdt,
            fee_exit_usdt, slippage_cost_usdt, funding_paid_usdt,
            funding_is_approximated, pnl_gross_usdt, pnl_net_usdt, close_reason,
            sl_margin_loss_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            t.strategy, t.symbol, t.timeframe, t.segment, t.side.value,
            t.entry_time.isoformat(), t.exit_time.isoformat(), t.entry_price,
            t.exit_price, t.qty, t.margin_usdt, t.leverage, t.fee_entry_usdt,
            t.fee_exit_usdt, t.slippage_cost_usdt, t.funding_paid_usdt,
            int(t.funding_is_approximated), t.pnl_gross_usdt, t.pnl_net_usdt,
            t.close_reason, t.sl_margin_loss_pct,
        ),
    )


async def insert_trades(db: Database, trades: list[BacktestTrade]) -> None:
    """Un solo commit para todo el lote (ver Database.execute_many)."""
    params = [
        (
            t.strategy, t.symbol, t.timeframe, t.segment, t.side.value,
            t.entry_time.isoformat(), t.exit_time.isoformat(), t.entry_price,
            t.exit_price, t.qty, t.margin_usdt, t.leverage, t.fee_entry_usdt,
            t.fee_exit_usdt, t.slippage_cost_usdt, t.funding_paid_usdt,
            int(t.funding_is_approximated), t.pnl_gross_usdt, t.pnl_net_usdt,
            t.close_reason, t.sl_margin_loss_pct,
        )
        for t in trades
    ]
    await db.execute_many(
        """
        INSERT INTO backtest_trades (
            strategy, symbol, timeframe, segment, side, entry_time, exit_time,
            entry_price, exit_price, qty, margin_usdt, leverage, fee_entry_usdt,
            fee_exit_usdt, slippage_cost_usdt, funding_paid_usdt,
            funding_is_approximated, pnl_gross_usdt, pnl_net_usdt, close_reason,
            sl_margin_loss_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params,
    )


async def insert_skipped_entry(db: Database, s: BacktestSkippedEntry) -> None:
    await db.execute(
        """
        INSERT INTO backtest_skipped_entries (
            strategy, symbol, timeframe, segment, ts, side, intended_sl_margin_loss_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (s.strategy, s.symbol, s.timeframe, s.segment, s.ts.isoformat(), s.side.value,
         s.intended_sl_margin_loss_pct),
    )


async def insert_run(db: Database, r: BacktestRun) -> None:
    await db.execute(
        """
        INSERT INTO backtest_runs (
            strategy, symbol, timeframe, segment, winrate, profit_factor,
            pnl_gross_total_usdt, pnl_net_total_usdt, max_drawdown_pct, expectancy_usdt,
            total_trades, fees_total_usdt, funding_total_usdt, funding_real_trades,
            funding_approx_trades, benchmark_return_pct, benchmark_max_drawdown_pct, run_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            r.strategy, r.symbol, r.timeframe, r.segment, r.winrate, r.profit_factor,
            r.pnl_gross_total_usdt, r.pnl_net_total_usdt, r.max_drawdown_pct,
            r.expectancy_usdt, r.total_trades, r.fees_total_usdt, r.funding_total_usdt,
            r.funding_real_trades, r.funding_approx_trades, r.benchmark_return_pct,
            r.benchmark_max_drawdown_pct, r.run_at.isoformat(),
        ),
    )


async def insert_verdict(db: Database, v: BacktestVerdict) -> None:
    await db.execute(
        """
        INSERT INTO backtest_verdicts (
            strategy, is_experimental, combos_tested, total_trades_all_segments,
            pf_oos_aggregate, pct_symbols_pf_gt1, pct_folds_positive, pf_stressed,
            pf_real_funding_only, pf_full_period_approx, max_drawdown_oos_pct,
            concentration_pct, pf_control_group, evidence_insufficient, discarded,
            discard_reasons_json, run_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            v.strategy, int(v.is_experimental), v.combos_tested, v.total_trades_all_segments,
            v.pf_oos_aggregate, v.pct_symbols_pf_gt1, v.pct_folds_positive, v.pf_stressed,
            v.pf_real_funding_only, v.pf_full_period_approx, v.max_drawdown_oos_pct,
            v.concentration_pct, v.pf_control_group, int(v.evidence_insufficient),
            int(v.discarded), v.discard_reasons_json, v.run_at.isoformat(),
        ),
    )


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


async def get_trades(
    db: Database,
    strategy: str,
    symbol: str | None = None,
    timeframe: str | None = None,
    segment: str | None = None,
) -> list[BacktestTrade]:
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


async def count_distinct_combos(db: Database) -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS n FROM ("
        "SELECT DISTINCT strategy, symbol, timeframe FROM backtest_trades "
        "UNION "
        "SELECT DISTINCT strategy, symbol, timeframe FROM backtest_skipped_entries"
        ")"
    )
    return int(row["n"]) if row else 0
