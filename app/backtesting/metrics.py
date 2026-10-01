"""Metricas agregadas sobre los trades producidos por `engine.run_backtest`
(docs/FASE2_PLAN.md secciones A y C)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from app.backtesting.engine import RawTrade


@dataclass
class AggregateMetrics:
    winrate: float | None
    profit_factor: float | None
    pnl_gross_total_usdt: float
    pnl_net_total_usdt: float
    max_drawdown_pct: float | None
    expectancy_usdt: float | None
    total_trades: int
    fees_total_usdt: float
    funding_total_usdt: float
    funding_real_trades: int
    funding_approx_trades: int


def compute_metrics(trades: list[RawTrade], initial_capital: float) -> AggregateMetrics:
    n = len(trades)
    if n == 0:
        return AggregateMetrics(
            winrate=None, profit_factor=None, pnl_gross_total_usdt=0.0, pnl_net_total_usdt=0.0,
            max_drawdown_pct=None, expectancy_usdt=None, total_trades=0, fees_total_usdt=0.0,
            funding_total_usdt=0.0, funding_real_trades=0, funding_approx_trades=0,
        )

    ordered = sorted(trades, key=lambda t: t.exit_time)
    wins = [t for t in ordered if t.pnl_net_usdt > 0]
    losses = [t for t in ordered if t.pnl_net_usdt <= 0]
    gross_profit = sum(t.pnl_net_usdt for t in wins)
    gross_loss = -sum(t.pnl_net_usdt for t in losses)  # positivo

    winrate = len(wins) / n
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else None
    )
    pnl_gross_total = sum(t.pnl_gross_usdt for t in ordered)
    pnl_net_total = sum(t.pnl_net_usdt for t in ordered)
    expectancy = pnl_net_total / n
    fees_total = sum(t.fee_entry_usdt + t.fee_exit_usdt for t in ordered)
    funding_total = sum(t.funding_paid_usdt for t in ordered)
    funding_real = sum(1 for t in ordered if not t.funding_is_approximated)
    funding_approx = n - funding_real

    equity = initial_capital
    peak = initial_capital
    max_dd_pct = 0.0
    for t in ordered:
        equity += t.pnl_net_usdt
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100
            max_dd_pct = max(max_dd_pct, dd)

    return AggregateMetrics(
        winrate=winrate, profit_factor=profit_factor, pnl_gross_total_usdt=pnl_gross_total,
        pnl_net_total_usdt=pnl_net_total, max_drawdown_pct=max_dd_pct, expectancy_usdt=expectancy,
        total_trades=n, fees_total_usdt=fees_total, funding_total_usdt=funding_total,
        funding_real_trades=funding_real, funding_approx_trades=funding_approx,
    )


def profit_factor_only(trades: list[RawTrade]) -> float | None:
    if not trades:
        return None
    gross_profit = sum(t.pnl_net_usdt for t in trades if t.pnl_net_usdt > 0)
    gross_loss = -sum(t.pnl_net_usdt for t in trades if t.pnl_net_usdt <= 0)
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else None


def split_is_oos(
    trades: list[RawTrade], oos_boundary: datetime
) -> tuple[list[RawTrade], list[RawTrade]]:
    is_trades = [t for t in trades if t.entry_time < oos_boundary]
    oos_trades = [t for t in trades if t.entry_time >= oos_boundary]
    return is_trades, oos_trades


def walk_forward_folds(
    trades: list[RawTrade], range_start: datetime, range_end: datetime,
    fold_months: int, step_months: int,
) -> list[list[RawTrade]]:
    """Folds superpuestos de `fold_months` de ancho, con paso de
    `step_months` -- puramente diagnostico (docs/FASE2_PLAN.md: "el OOS es
    un unico periodo", los folds NO deciden el veredicto, solo informan
    `pct_folds_positive`)."""
    folds: list[list[RawTrade]] = []
    cursor = range_start
    while cursor < range_end:
        fold_end = _add_months(cursor, fold_months)
        fold_trades = [t for t in trades if cursor <= t.entry_time < fold_end]
        folds.append(fold_trades)
        cursor = _add_months(cursor, step_months)
    return folds


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, 28)  # evita errores de dia invalido (p.ej. 31 de febrero)
    return dt.replace(year=year, month=month, day=day)


def concentration_pct(trades: list[RawTrade]) -> float | None:
    """Mayor porcentaje del PnL neto total que proviene de UNA sola
    operacion o de UN solo simbolo (docs/FASE2_PLAN.md seccion C)."""
    total_net = sum(t.pnl_net_usdt for t in trades)
    if total_net <= 0 or not trades:
        return None
    max_single_trade = max(t.pnl_net_usdt for t in trades)
    by_symbol: dict[str, float] = defaultdict(float)
    for t in trades:
        by_symbol[t.symbol] += t.pnl_net_usdt
    max_single_symbol = max(by_symbol.values())
    return max(max_single_trade, max_single_symbol) / total_net * 100


def stressed_profit_factor(
    trades: list[RawTrade], fee_multiplier: float, slippage_multiplier: float
) -> float | None:
    """Recomputa el PnL neto de cada trade con fees y slippage multiplicados
    (prueba de estrategia, docs/FASE2_PLAN.md punto 2) -- sin re-simular: el
    PnL bruto y el funding no cambian, solo los costos de fee/slippage ya
    registrados por operacion."""
    stressed_pnls = [
        t.pnl_gross_usdt
        - fee_multiplier * (t.fee_entry_usdt + t.fee_exit_usdt)
        - slippage_multiplier * t.slippage_cost_usdt
        - t.funding_paid_usdt
        for t in trades
    ]
    if not stressed_pnls:
        return None
    gross_profit = sum(p for p in stressed_pnls if p > 0)
    gross_loss = -sum(p for p in stressed_pnls if p <= 0)
    if gross_loss > 0:
        return gross_profit / gross_loss
    return float("inf") if gross_profit > 0 else None


def benchmark_buy_and_hold(closes: list[float]) -> tuple[float, float]:
    """Retorno total (%) y drawdown maximo (%) de comprar y mantener desde
    la primera hasta la ultima vela del rango (sin apalancamiento)."""
    if len(closes) < 2:
        return 0.0, 0.0
    start = closes[0]
    total_return_pct = (closes[-1] - start) / start * 100
    peak = closes[0]
    max_dd_pct = 0.0
    for price in closes:
        peak = max(peak, price)
        if peak > 0:
            dd = (peak - price) / peak * 100
            max_dd_pct = max(max_dd_pct, dd)
    return total_return_pct, max_dd_pct


def pct_symbols_with_pf_gt1(trades: list[RawTrade]) -> float | None:
    by_symbol: dict[str, list[RawTrade]] = defaultdict(list)
    for t in trades:
        by_symbol[t.symbol].append(t)
    if not by_symbol:
        return None
    symbols_with_edge = sum(
        1 for ts in by_symbol.values() if (profit_factor_only(ts) or 0) > 1.0
    )
    return symbols_with_edge / len(by_symbol) * 100


def pct_folds_positive(folds: list[list[RawTrade]], min_trades_per_cell: int) -> float | None:
    eligible = [f for f in folds if len(f) >= min_trades_per_cell]
    if not eligible:
        return None
    positive = sum(1 for f in eligible if sum(t.pnl_net_usdt for t in f) > 0)
    return positive / len(eligible) * 100


def margin_loss_distribution(trades: list[RawTrade]) -> dict[str, float]:
    """Distribucion del % de margen perdido al tocar el SL (punto 1 de los
    ajustes de Fase 2) -- solo sobre operaciones cerradas por SL/TRAILING."""
    sl_trades = [t for t in trades if t.close_reason in ("SL", "TRAILING") and t.sl_margin_loss_pct]
    values = sorted(t.sl_margin_loss_pct for t in sl_trades)  # type: ignore[misc]
    if not values:
        return {"count": 0, "mean": 0.0, "median": 0.0, "max": 0.0}
    n = len(values)
    median = values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2
    return {
        "count": n, "mean": sum(values) / n, "median": median, "max": values[-1],
    }
