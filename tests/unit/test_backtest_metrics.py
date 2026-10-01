from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.backtesting.engine import RawTrade
from app.backtesting.metrics import (
    benchmark_buy_and_hold,
    compute_metrics,
    concentration_pct,
    margin_loss_distribution,
    pct_folds_positive,
    pct_symbols_with_pf_gt1,
    profit_factor_only,
    split_is_oos,
    stressed_profit_factor,
)
from app.persistence.models import Side


def _trade(
    pnl_net: float, pnl_gross: float | None = None, symbol: str = "BTCUSDT",
    entry_time: datetime | None = None, close_reason: str = "TP",
    sl_margin_loss_pct: float | None = None, fee_entry: float = 0.06, fee_exit: float = 0.06,
    slippage: float = 0.0, funding: float = 0.0,
) -> RawTrade:
    t = entry_time or datetime(2024, 1, 1, tzinfo=UTC)
    return RawTrade(
        strategy="fake", symbol=symbol, timeframe="4h", side=Side.LONG,
        entry_time=t, exit_time=t, entry_price=100.0, exit_price=100.0, qty=1.0,
        margin_usdt=10.0, leverage=10, fee_entry_usdt=fee_entry, fee_exit_usdt=fee_exit,
        slippage_cost_usdt=slippage, funding_paid_usdt=funding, funding_is_approximated=False,
        pnl_gross_usdt=pnl_gross if pnl_gross is not None else pnl_net, pnl_net_usdt=pnl_net,
        close_reason=close_reason, sl_margin_loss_pct=sl_margin_loss_pct,
    )


def test_compute_metrics_empty_list():
    m = compute_metrics([], initial_capital=100.0)
    assert m.total_trades == 0
    assert m.winrate is None
    assert m.profit_factor is None


def test_compute_metrics_basic_winrate_and_pf():
    trades = [_trade(10.0), _trade(10.0), _trade(-5.0)]
    m = compute_metrics(trades, initial_capital=100.0)
    assert m.total_trades == 3
    assert m.winrate == pytest.approx(2 / 3)
    assert m.profit_factor == pytest.approx(20.0 / 5.0)
    assert m.pnl_net_total_usdt == pytest.approx(15.0)


def test_compute_metrics_all_wins_pf_is_infinite():
    m = compute_metrics([_trade(10.0), _trade(5.0)], initial_capital=100.0)
    assert m.profit_factor == float("inf")


def test_compute_metrics_drawdown_tracks_equity_curve():
    # +20 (equity 120, peak 120) -> -30 (equity 90, dd = 30/120 = 25%) -> +5 (equity 95)
    trades = [
        _trade(20.0, entry_time=datetime(2024, 1, 1, tzinfo=UTC)),
        _trade(-30.0, entry_time=datetime(2024, 1, 2, tzinfo=UTC)),
        _trade(5.0, entry_time=datetime(2024, 1, 3, tzinfo=UTC)),
    ]
    m = compute_metrics(trades, initial_capital=100.0)
    assert m.max_drawdown_pct == pytest.approx(25.0)


def test_concentration_pct_dominated_by_one_trade():
    trades = [
        _trade(100.0, symbol="BTCUSDT"),
        _trade(1.0, symbol="ETHUSDT"),
        _trade(1.0, symbol="SOLUSDT"),
    ]
    pct = concentration_pct(trades)
    assert pct == pytest.approx(100.0 / 102.0 * 100)


def test_concentration_pct_none_when_total_not_positive():
    assert concentration_pct([_trade(-5.0), _trade(2.0)]) is None


def test_split_is_oos_boundary():
    trades = [
        _trade(1.0, entry_time=datetime(2024, 1, 1, tzinfo=UTC)),
        _trade(1.0, entry_time=datetime(2024, 6, 1, tzinfo=UTC)),
    ]
    is_t, oos_t = split_is_oos(trades, oos_boundary=datetime(2024, 3, 1, tzinfo=UTC))
    assert len(is_t) == 1
    assert len(oos_t) == 1


def test_stressed_profit_factor_doubles_costs():
    trades = [
        _trade(pnl_net=10.0, pnl_gross=10.12, fee_entry=0.06, fee_exit=0.06, slippage=0.0),
        _trade(pnl_net=-5.0, pnl_gross=-5.0, fee_entry=0.0, fee_exit=0.0, slippage=0.0),
    ]
    normal_pf = profit_factor_only(trades)
    stressed = stressed_profit_factor(trades, fee_multiplier=2.0, slippage_multiplier=2.0)
    assert stressed is not None
    # ganadora: 10.12 - 2*(0.06+0.06) = 9.88 (antes 10.0); perdedora sin costos, no cambia.
    assert stressed < normal_pf
    assert stressed == pytest.approx(9.88 / 5.0)


def test_benchmark_buy_and_hold_basic():
    ret, dd = benchmark_buy_and_hold([100.0, 120.0, 90.0, 110.0])
    assert ret == pytest.approx(10.0)  # (110-100)/100
    assert dd == pytest.approx((120.0 - 90.0) / 120.0 * 100)


def test_pct_symbols_with_pf_gt1():
    trades = [
        _trade(10.0, symbol="BTCUSDT"), _trade(10.0, symbol="BTCUSDT"),
        _trade(-10.0, symbol="ETHUSDT"), _trade(-5.0, symbol="ETHUSDT"),
    ]
    pct = pct_symbols_with_pf_gt1(trades)
    assert pct == pytest.approx(50.0)


def test_pct_folds_positive_ignores_folds_below_min_trades():
    fold_with_enough = [_trade(1.0)] * 5
    fold_too_small = [_trade(-100.0)] * 2
    pct = pct_folds_positive([fold_with_enough, fold_too_small], min_trades_per_cell=5)
    assert pct == pytest.approx(100.0)


def test_margin_loss_distribution_only_counts_sl_and_trailing():
    trades = [
        _trade(-5.0, close_reason="SL", sl_margin_loss_pct=40.0),
        _trade(-5.0, close_reason="TRAILING", sl_margin_loss_pct=20.0),
        _trade(10.0, close_reason="TP", sl_margin_loss_pct=99.0),  # no cuenta
    ]
    dist = margin_loss_distribution(trades)
    assert dist["count"] == 2
    assert dist["mean"] == pytest.approx(30.0)
    assert dist["max"] == pytest.approx(40.0)
