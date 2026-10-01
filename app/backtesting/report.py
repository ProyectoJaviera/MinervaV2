"""Orquesta el backtest completo: corre cada estrategia x simbolo x
timeframe, persiste cada corrida, y calcula el veredicto global por
estrategia aplicando los criterios de descarte (docs/FASE2_PLAN.md,
ajustes del usuario, punto 2). Ver tambien docs/FASE2_CRITERIOS.md -- los
parametros y criterios deben quedar COMMITEADOS antes de ejecutar esto
(punto 8 de los ajustes): "tras ver resultados OOS no se modifican
parametros ni criterios".
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass
from datetime import UTC, datetime

from app.backtesting import metrics as m
from app.backtesting.engine import RawTrade, run_backtest
from app.config import Settings
from app.core.logging import get_logger
from app.market.ohlcv_history import (
    MissingHistoricalDataError,
    drop_incomplete_last_bar,
    get_cached_or_raise,
)
from app.persistence.database import Database
from app.persistence.models import BacktestRun, BacktestSkippedEntry, BacktestTrade, BacktestVerdict
from app.persistence.repositories import backtest_repo
from app.strategies.registry import EXPERIMENTAL_STRATEGIES, STRATEGIES, STRATEGY_TIMEFRAMES

logger = get_logger(__name__)


@dataclass
class StrategyResult:
    strategy_name: str
    verdict: BacktestVerdict
    is_trades: int
    oos_trades: int


def _is_oos(trade: RawTrade, oos_boundary_ms: int) -> str:
    return "OOS" if trade.entry_time.timestamp() * 1000 >= oos_boundary_ms else "IS"


def _finite_or_none(pf: float | None) -> float | None:
    return None if pf is None or pf == float("inf") else pf


def _to_backtest_trade(t: RawTrade, segment: str) -> BacktestTrade:
    return BacktestTrade(
        strategy=t.strategy, symbol=t.symbol, timeframe=t.timeframe, segment=segment,
        side=t.side, entry_time=t.entry_time, exit_time=t.exit_time,
        entry_price=t.entry_price, exit_price=t.exit_price, qty=t.qty,
        margin_usdt=t.margin_usdt, leverage=t.leverage,
        fee_entry_usdt=t.fee_entry_usdt, fee_exit_usdt=t.fee_exit_usdt,
        slippage_cost_usdt=t.slippage_cost_usdt, funding_paid_usdt=t.funding_paid_usdt,
        funding_is_approximated=t.funding_is_approximated,
        pnl_gross_usdt=t.pnl_gross_usdt, pnl_net_usdt=t.pnl_net_usdt,
        close_reason=t.close_reason, sl_margin_loss_pct=t.sl_margin_loss_pct,
    )


def _to_skip_model(s) -> BacktestSkippedEntry:
    return BacktestSkippedEntry(
        strategy=s.strategy, symbol=s.symbol, timeframe=s.timeframe, segment="N/A",
        ts=s.ts, side=s.side, intended_sl_margin_loss_pct=s.intended_sl_margin_loss_pct,
    )


async def _benchmark_for_cell(
    db: Database,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
) -> tuple[float, float]:
    bars = await get_cached_or_raise(db, symbol, timeframe, start_ms, end_ms, "LAST_PRICE")
    bars = drop_incomplete_last_bar(bars, timeframe, int(_time.time() * 1000))
    bars.sort(key=lambda b: b.open_time)
    return m.benchmark_buy_and_hold([b.close for b in bars])


async def _run_one_cell(
    db: Database,
    strategy,
    strategy_name: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    oos_boundary_ms: int,
    settings: Settings,
) -> list[RawTrade]:
    try:
        trades, skips = await run_backtest(
            db, strategy, strategy_name, symbol, timeframe,
            start_ms, end_ms, settings,
        )
    except MissingHistoricalDataError:
        # Faltan datos: no es un bug de una celda puntual, es una
        # precondicion incumplida de toda la corrida -- se propaga para que
        # el script se detenga con un mensaje claro en vez de reportar
        # "0 trades" silenciosamente en esta celda (y probablemente en
        # todas las demas del mismo simbolo/timeframe).
        raise
    except Exception:
        logger.exception("Backtest fallido: %s %s %s", strategy_name, symbol, timeframe)
        return []

    await backtest_repo.insert_trades(
        db, [_to_backtest_trade(t, _is_oos(t, oos_boundary_ms)) for t in trades]
    )
    for skip in skips:
        await backtest_repo.insert_skipped_entry(db, _to_skip_model(skip))

    for segment in ("IS", "OOS"):
        seg_trades = [t for t in trades if _is_oos(t, oos_boundary_ms) == segment]
        cell_start = oos_boundary_ms if segment == "OOS" else start_ms
        cell_end = end_ms if segment == "OOS" else oos_boundary_ms
        cell_metrics = m.compute_metrics(seg_trades, settings.backtest_initial_capital)
        bench_return, bench_dd = await _benchmark_for_cell(
            db, symbol, timeframe, cell_start, cell_end
        )
        await backtest_repo.insert_run(db, BacktestRun(
            strategy=strategy_name, symbol=symbol, timeframe=timeframe, segment=segment,
            winrate=cell_metrics.winrate,
            profit_factor=_finite_or_none(cell_metrics.profit_factor),
            pnl_gross_total_usdt=cell_metrics.pnl_gross_total_usdt,
            pnl_net_total_usdt=cell_metrics.pnl_net_total_usdt,
            max_drawdown_pct=cell_metrics.max_drawdown_pct,
            expectancy_usdt=cell_metrics.expectancy_usdt,
            total_trades=cell_metrics.total_trades,
            fees_total_usdt=cell_metrics.fees_total_usdt,
            funding_total_usdt=cell_metrics.funding_total_usdt,
            funding_real_trades=cell_metrics.funding_real_trades,
            funding_approx_trades=cell_metrics.funding_approx_trades,
            benchmark_return_pct=bench_return, benchmark_max_drawdown_pct=bench_dd,
            run_at=datetime.now(UTC),
        ))

    return trades


def _build_verdict(
    strategy_name: str,
    all_trades: list[RawTrade],
    oos_boundary_ms: int,
    start_ms: int,
    end_ms: int,
    combos_tested: int,
    settings: Settings,
) -> BacktestVerdict:
    oos_trades = [t for t in all_trades if _is_oos(t, oos_boundary_ms) == "OOS"]

    pf_oos = m.profit_factor_only(oos_trades)
    pct_symbols = m.pct_symbols_with_pf_gt1(oos_trades)

    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=UTC)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    folds = m.walk_forward_folds(
        all_trades, start_dt, end_dt,
        settings.backtest_walk_forward_fold_months,
        settings.backtest_walk_forward_step_months,
    )
    pct_folds = m.pct_folds_positive(folds, settings.backtest_min_trades_per_cell)

    pf_stressed = m.stressed_profit_factor(
        oos_trades,
        settings.backtest_stress_fee_multiplier,
        settings.backtest_stress_slippage_multiplier,
    )

    real_funding_trades = [t for t in oos_trades if not t.funding_is_approximated]
    pf_real_funding_only = m.profit_factor_only(real_funding_trades)

    control = set(settings.backtest_control_symbols_list)
    control_trades = [t for t in oos_trades if t.symbol in control]
    pf_control = m.profit_factor_only(control_trades)

    oos_metrics = m.compute_metrics(oos_trades, settings.backtest_initial_capital)
    concentration = m.concentration_pct(oos_trades)

    is_experimental = strategy_name in EXPERIMENTAL_STRATEGIES
    evidence_insufficient = len(all_trades) < settings.backtest_min_trades_total

    discard_reasons = _evaluate_discard_criteria(
        is_experimental, evidence_insufficient, pf_oos, pct_symbols, pct_folds,
        pf_stressed, oos_metrics.max_drawdown_pct, concentration, settings,
    )
    discarded = bool(discard_reasons) and not is_experimental and not evidence_insufficient

    return BacktestVerdict(
        strategy=strategy_name, is_experimental=is_experimental, combos_tested=combos_tested,
        total_trades_all_segments=len(all_trades),
        pf_oos_aggregate=_finite_or_none(pf_oos),
        pct_symbols_pf_gt1=pct_symbols, pct_folds_positive=pct_folds,
        pf_stressed=_finite_or_none(pf_stressed),
        pf_real_funding_only=_finite_or_none(pf_real_funding_only),
        pf_full_period_approx=_finite_or_none(pf_oos),
        max_drawdown_oos_pct=oos_metrics.max_drawdown_pct,
        concentration_pct=concentration, pf_control_group=_finite_or_none(pf_control),
        evidence_insufficient=evidence_insufficient, discarded=discarded,
        discard_reasons_json=json.dumps(discard_reasons) if discard_reasons else None,
        run_at=datetime.now(UTC),
    )


def _evaluate_discard_criteria(
    is_experimental: bool,
    evidence_insufficient: bool,
    pf_oos: float | None,
    pct_symbols: float | None,
    pct_folds: float | None,
    pf_stressed: float | None,
    max_drawdown_pct: float | None,
    concentration: float | None,
    settings: Settings,
) -> list[str]:
    if is_experimental or evidence_insufficient:
        return []

    reasons = []
    min_pf = settings.backtest_min_profit_factor
    if pf_oos is None or pf_oos < min_pf:
        reasons.append(f"PF OOS agregado {pf_oos} < minimo {min_pf}")

    min_pct_symbols = settings.backtest_min_pct_symbols_pf_gt1 * 100
    if pct_symbols is None or pct_symbols < min_pct_symbols:
        reasons.append(f"PF>1 en solo {pct_symbols}% de simbolos (< {min_pct_symbols}%)")

    min_pct_folds = settings.backtest_min_pct_folds_positive * 100
    if pct_folds is None or pct_folds < min_pct_folds:
        reasons.append(f"Positivo en solo {pct_folds}% de folds (< {min_pct_folds}%)")

    if pf_stressed is None or pf_stressed <= 1.0:
        reasons.append(f"PF tras estres (2x fees/slippage) {pf_stressed} <= 1.0")

    max_dd_limit = settings.backtest_max_drawdown_pct * 100
    if max_drawdown_pct is not None and max_drawdown_pct > max_dd_limit:
        reasons.append(f"Drawdown OOS {max_drawdown_pct:.1f}% > {max_dd_limit}%")

    concentration_limit = settings.backtest_concentration_limit_pct * 100
    if concentration is not None and concentration > concentration_limit:
        reasons.append(f"Concentracion {concentration:.1f}% > {concentration_limit}%")

    return reasons


async def run_full_backtest(
    db: Database,
    settings: Settings,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
) -> list[StrategyResult]:
    oos_boundary_ms = start_ms + int((end_ms - start_ms) * (1 - settings.backtest_oos_split_pct))
    control_symbols = set(settings.backtest_control_symbols_list)
    all_symbols = sorted(set(symbols) | control_symbols)

    # Idempotente ante reintentos (ver backtest_repo.clear_results): no deja
    # filas duplicadas de una corrida anterior interrumpida.
    await backtest_repo.clear_results(db)

    results: list[StrategyResult] = []

    for strategy_name, strategy in STRATEGIES.items():
        timeframes = STRATEGY_TIMEFRAMES.get(strategy_name, ["4h"])
        strategy_trades: list[RawTrade] = []
        combos_tested = 0

        for timeframe in timeframes:
            for symbol in all_symbols:
                combos_tested += 1
                cell_t0 = _time.time()
                logger.info("celda %s %s %s: empieza", strategy_name, symbol, timeframe)
                trades = await _run_one_cell(
                    db, strategy, strategy_name, symbol, timeframe,
                    start_ms, end_ms, oos_boundary_ms, settings,
                )
                logger.info(
                    "celda %s %s %s: termina (%d trades, %.1fs)",
                    strategy_name, symbol, timeframe, len(trades), _time.time() - cell_t0,
                )
                strategy_trades.extend(trades)

        verdict = _build_verdict(
            strategy_name, strategy_trades, oos_boundary_ms, start_ms, end_ms,
            combos_tested, settings,
        )
        await backtest_repo.insert_verdict(db, verdict)

        is_count = sum(1 for t in strategy_trades if _is_oos(t, oos_boundary_ms) == "IS")
        oos_count = len(strategy_trades) - is_count
        results.append(StrategyResult(strategy_name, verdict, is_count, oos_count))

    return results
