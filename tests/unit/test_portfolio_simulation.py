"""Tests de `app/backtesting/metrics.py::simulate_portfolio` -- simulacion
de una sola cuenta compartida (capital inicial, tope de posiciones
simultaneas, margen fijo por operacion) sobre los trades ya generados de
forma independiente por simbolo/timeframe. Metrica informativa (no
participa en los criterios de descarte congelados)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.backtesting.engine import RawTrade
from app.backtesting.metrics import simulate_portfolio
from app.persistence.models import Side


def _trade(
    entry_time: datetime, exit_time: datetime, pnl_net: float,
    margin_usdt: float = 10.0, symbol: str = "BTCUSDT",
) -> RawTrade:
    return RawTrade(
        strategy="fake", symbol=symbol, timeframe="4h", side=Side.LONG,
        entry_time=entry_time, exit_time=exit_time, entry_price=100.0, exit_price=100.0,
        qty=1.0, margin_usdt=margin_usdt, leverage=10, fee_entry_usdt=0.0, fee_exit_usdt=0.0,
        slippage_cost_usdt=0.0, funding_paid_usdt=0.0, funding_is_approximated=False,
        pnl_gross_usdt=pnl_net, pnl_net_usdt=pnl_net, close_reason="TP",
        sl_margin_loss_pct=None,
    )


T0 = datetime(2024, 1, 1, tzinfo=UTC)
T1 = datetime(2024, 1, 2, tzinfo=UTC)
T2 = datetime(2024, 1, 3, tzinfo=UTC)


def test_non_overlapping_trades_all_included_and_capital_accumulates():
    trades = [
        _trade(T0, T1, pnl_net=5.0),
        _trade(T1, T2, pnl_net=3.0),
    ]
    result = simulate_portfolio(
        trades, initial_capital=100.0, max_simultaneous_positions=3, margin_per_trade=10.0
    )
    assert result.trades_included == 2
    assert result.trades_skipped_no_margin == 0
    assert result.final_capital_usdt == 108.0


def test_exceeding_max_simultaneous_positions_skips_extra_trades():
    """3 operaciones que se superponen por completo (misma entrada, misma
    salida) con tope de 2 simultaneas: la tercera debe quedar fuera."""
    trades = [
        _trade(T0, T2, pnl_net=1.0, symbol="BTCUSDT"),
        _trade(T0, T2, pnl_net=1.0, symbol="ETHUSDT"),
        _trade(T0, T2, pnl_net=1.0, symbol="SOLUSDT"),
    ]
    result = simulate_portfolio(
        trades, initial_capital=100.0, max_simultaneous_positions=2, margin_per_trade=10.0
    )
    assert result.trades_included == 2
    assert result.trades_skipped_no_margin == 1


def test_insufficient_margin_skips_trade_even_under_position_limit():
    """Capital que alcanza para una operacion pero no para dos en
    simultaneo (aunque el tope de posiciones simultaneas sea holgado)."""
    trades = [
        _trade(T0, T2, pnl_net=0.0, margin_usdt=10.0, symbol="BTCUSDT"),
        _trade(T0, T2, pnl_net=0.0, margin_usdt=10.0, symbol="ETHUSDT"),
    ]
    result = simulate_portfolio(
        trades, initial_capital=15.0, max_simultaneous_positions=5, margin_per_trade=10.0
    )
    assert result.trades_included == 1
    assert result.trades_skipped_no_margin == 1


def test_capital_never_goes_negative_after_a_large_loss():
    trades = [_trade(T0, T1, pnl_net=-50.0, margin_usdt=10.0)]
    result = simulate_portfolio(
        trades, initial_capital=10.0, max_simultaneous_positions=1, margin_per_trade=10.0
    )
    assert result.trades_included == 1
    assert result.final_capital_usdt == 0.0


def test_a_closed_slot_frees_margin_for_a_later_trade():
    """Una posicion que ya cerro antes de que otra quiera entrar libera su
    margen -- no deberia quedar bloqueada para siempre."""
    trades = [
        _trade(T0, T1, pnl_net=0.0, margin_usdt=10.0, symbol="BTCUSDT"),
        _trade(T1, T2, pnl_net=0.0, margin_usdt=10.0, symbol="ETHUSDT"),
    ]
    result = simulate_portfolio(
        trades, initial_capital=10.0, max_simultaneous_positions=1, margin_per_trade=10.0
    )
    assert result.trades_included == 2
    assert result.trades_skipped_no_margin == 0
