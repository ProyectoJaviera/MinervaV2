"""Tests de `app/backtesting/metrics.py::simulate_portfolio` -- simulacion
de una sola cuenta compartida (capital inicial, tope de posiciones
simultaneas, margen fijo por operacion) sobre los trades ya generados de
forma independiente por simbolo/timeframe. Metrica informativa (no
participa en los criterios de descarte congelados)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.backtesting.engine import RawTrade
from app.backtesting.metrics import simulate_portfolio, simulate_portfolio_monte_carlo
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


def test_mtm_drawdown_equals_realized_drawdown_when_nothing_overlaps():
    """Sin superposicion temporal entre operaciones, no hay nada que
    interpolar de forma distinta -- ambos metodos deben coincidir
    exactamente (los unicos puntos de evaluacion de cada operacion son su
    propia apertura/cierre, iguales en los dos metodos)."""
    trades = [
        _trade(T0, T1, pnl_net=-50.0, symbol="BTCUSDT"),
        _trade(T1, T2, pnl_net=30.0, symbol="ETHUSDT"),
    ]
    result = simulate_portfolio(
        trades, initial_capital=100.0, max_simultaneous_positions=1, margin_per_trade=10.0
    )
    assert result.mtm_max_drawdown_pct == pytest.approx(result.max_drawdown_pct)


def test_mtm_drawdown_can_exceed_realized_only_drawdown_with_overlapping_positions():
    """Con posiciones superpuestas, el drawdown mark-to-market (que suma la
    PnL FLOTANTE interpolada de lo que sigue abierto) puede ser
    sustancialmente mayor que el drawdown que solo mira los cierres --
    revela riesgo que el metodo "solo al cierre" esconde por completo
    mientras las posiciones siguen abiertas. Caso encontrado por busqueda
    numerica y verificado con la implementacion real."""
    base = T0
    trades = [
        _trade(base + timedelta(hours=3), base + timedelta(hours=6), pnl_net=-60.0, symbol="S0"),
        _trade(base + timedelta(hours=4), base + timedelta(hours=12), pnl_net=-60.0, symbol="S1"),
        _trade(base + timedelta(hours=9), base + timedelta(hours=11), pnl_net=60.0, symbol="S2"),
    ]
    result = simulate_portfolio(
        trades, initial_capital=100.0, max_simultaneous_positions=3, margin_per_trade=10.0
    )
    assert result.max_drawdown_pct == pytest.approx(60.0)
    assert result.mtm_max_drawdown_pct == pytest.approx(97.5)
    assert result.mtm_max_drawdown_pct > result.max_drawdown_pct


def test_monte_carlo_tie_break_order_varies_who_gets_skipped():
    """Tarea: antes, las operaciones empatadas en `entry_time` se admitian
    segun el orden de la lista (alfabetico por simbolo, por como
    `run_full_backtest` itera `all_symbols`), sesgando sistematicamente la
    admision hacia el mismo simbolo cada vez que hay mas señales que
    cupos. `simulate_portfolio_monte_carlo` baraja el orden completo antes
    de cada corrida (el `sorted()` estable interno solo reordena los
    empates, el resto del orden cronologico no cambia) -- con suficientes
    corridas, mas de un simbolo debe terminar siendo el "tercero" excluido."""
    trades = [
        _trade(T0, T2, pnl_net=1.0, symbol="AAA"),
        _trade(T0, T2, pnl_net=1.0, symbol="BBB"),
        _trade(T0, T2, pnl_net=1.0, symbol="CCC"),
    ]

    # Sin barajar: siempre se admiten las dos primeras de la lista (orden
    # de entrada) y se descarta la tercera -- el sesgo que se corrige.
    unshuffled = simulate_portfolio(
        trades, 100.0, max_simultaneous_positions=2, margin_per_trade=10.0
    )
    assert unshuffled.trades_included == 2

    # Las tres operaciones tienen el mismo entry_time/margen/duracion, asi
    # que la que queda fuera es siempre la ULTIMA procesada en el orden ya
    # barajado -- basta con mirar que simbolo cae al final de cada baraje
    # para saber cual quedaria excluida esa corrida.
    rng = random.Random(7)
    skipped_symbols = set()
    for _ in range(50):
        shuffled = list(trades)
        rng.shuffle(shuffled)
        result = simulate_portfolio(
            shuffled, 100.0, max_simultaneous_positions=2, margin_per_trade=10.0
        )
        assert result.trades_included == 2
        skipped_symbols.add(shuffled[-1].symbol)

    assert len(skipped_symbols) > 1


def test_monte_carlo_summary_is_deterministic_with_fixed_seed():
    trades = [
        _trade(T0, T2, pnl_net=5.0, symbol="AAA"),
        _trade(T0, T2, pnl_net=-3.0, symbol="BBB"),
        _trade(T1, T2, pnl_net=2.0, symbol="CCC"),
    ]
    first = simulate_portfolio_monte_carlo(
        trades, initial_capital=100.0, max_simultaneous_positions=2, margin_per_trade=10.0,
        runs=30, seed=123,
    )
    second = simulate_portfolio_monte_carlo(
        trades, initial_capital=100.0, max_simultaneous_positions=2, margin_per_trade=10.0,
        runs=30, seed=123,
    )
    assert first == second


def test_monte_carlo_median_falls_within_p10_p90_range():
    trades = [
        _trade(T0, T2, pnl_net=5.0, symbol="AAA"),
        _trade(T0, T2, pnl_net=-30.0, symbol="BBB"),
        _trade(T0, T2, pnl_net=12.0, symbol="CCC"),
    ]
    summary = simulate_portfolio_monte_carlo(
        trades, initial_capital=100.0, max_simultaneous_positions=2, margin_per_trade=10.0,
        runs=200, seed=42,
    )
    assert summary.runs == 200
    assert summary.final_capital_p10 <= summary.final_capital_median <= summary.final_capital_p90
    assert summary.max_drawdown_p10 <= summary.max_drawdown_median <= summary.max_drawdown_p90
    assert (
        summary.mtm_max_drawdown_p10
        <= summary.mtm_max_drawdown_median
        <= summary.mtm_max_drawdown_p90
    )
