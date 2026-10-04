"""Criterio de valor de la IA sobre la sombra (subfase 3.5, ajustes 1-3): conglomerados
por solape, bootstrap por conglomerados, regla de decision fijada y separacion de
grupos mixtos en el reporte."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.trading import shadow_report
from app.trading.ai_value import (
    APROBADA,
    RECHAZADA,
    ai_value_verdict,
    assign_clusters,
    build_units,
    cluster_bootstrap_difference,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def trade(id_, *, opened_h, closed_h, pnl, label=APROBADA, symbol="BTCUSDT",
          side=Side.LONG, strategies=("ema_cross_9_21",)):
    return ShadowTrade(
        id=id_, symbol=symbol, side=side, strategy=strategies[0], status=TradeStatus.CLOSED,
        leverage=10, margin_usdt=10.0, notional_usdt=100.0, qty=1.0, entry_price=100.0,
        opened_at=BASE + timedelta(hours=opened_h), closed_at=BASE + timedelta(hours=closed_h),
        pnl_net_usdt=pnl, contributing_strategies=list(strategies),
        signal_group_key=f"{symbol}|{side.value}|{id_}",
        candle_close_time=BASE + timedelta(hours=opened_h), llm_decision=label,
    )


def independent_trades(n: int, approved_mean: float, rejected_mean: float):
    """n operaciones por lado, cada una en su propio intervalo (sin solapes)."""
    out = []
    for i in range(n):
        start = i * 10.0  # horas: intervalos disjuntos
        noise = 0.5 if i % 2 == 0 else -0.5
        out.append(trade(2 * i + 1, opened_h=start, closed_h=start + 1,
                         pnl=approved_mean + noise, label=APROBADA))
        out.append(trade(2 * i + 2, opened_h=start + 2, closed_h=start + 3,
                         pnl=rejected_mean + noise, label=RECHAZADA))
    return out


# --- conglomerados -----------------------------------------------------------


def test_overlapping_same_symbol_and_side_form_one_cluster():
    a = trade(1, opened_h=0, closed_h=5, pnl=1)
    b = trade(2, opened_h=3, closed_h=8, pnl=1)  # se solapa con a
    c = trade(3, opened_h=10, closed_h=11, pnl=1)  # separada
    clusters = assign_clusters([a, b, c])
    assert clusters[1] == clusters[2] != clusters[3]


def test_different_symbol_or_side_never_merge_even_if_overlapping():
    a = trade(1, opened_h=0, closed_h=5, pnl=1)
    other_symbol = trade(2, opened_h=1, closed_h=4, pnl=1, symbol="ETHUSDT")
    other_side = trade(3, opened_h=1, closed_h=4, pnl=1, side=Side.SHORT)
    clusters = assign_clusters([a, other_symbol, other_side])
    assert len({clusters[1], clusters[2], clusters[3]}) == 3


def test_open_trades_do_not_enter_the_clusters():
    closed = trade(1, opened_h=0, closed_h=5, pnl=1)
    still_open = ShadowTrade(
        id=2, symbol="BTCUSDT", side=Side.LONG, strategy="ema_cross_9_21",
        status=TradeStatus.OPEN, leverage=10, margin_usdt=10.0, notional_usdt=100.0,
        qty=1.0, entry_price=100.0, opened_at=BASE, contributing_strategies=["ema_cross_9_21"],
        signal_group_key="k2", candle_close_time=BASE,
    )
    assert set(assign_clusters([closed, still_open])) == {1}


def test_effective_n_is_the_number_of_clusters_not_trades():
    trades = [trade(1, opened_h=0, closed_h=5, pnl=1, label=APROBADA),
              trade(2, opened_h=1, closed_h=6, pnl=1, label=RECHAZADA),
              trade(3, opened_h=2, closed_h=7, pnl=1, label=APROBADA)]
    units = build_units(trades)
    assert len(units) == 1  # tres operaciones solapadas = una unidad
    verdict = ai_value_verdict(trades)
    assert verdict.n_raw_approved == 2 and verdict.n_eff_approved == 1


# --- bootstrap por conglomerados ---------------------------------------------


def test_bootstrap_is_deterministic_with_the_same_seed():
    units = build_units(independent_trades(30, 2.0, 0.0))
    first = cluster_bootstrap_difference(units, seed=7)
    assert first == cluster_bootstrap_difference(units, seed=7)


def test_bootstrap_interval_excludes_zero_for_a_clear_difference():
    units = build_units(independent_trades(120, 2.0, 0.0))
    diff, lo, hi = cluster_bootstrap_difference(units)
    assert diff > 1.5 and lo > 0 and hi > lo


def test_bootstrap_interval_contains_zero_when_there_is_no_difference():
    units = build_units(independent_trades(120, 0.0, 0.0))
    _diff, lo, hi = cluster_bootstrap_difference(units)
    assert lo <= 0 <= hi


# --- regla de decision fijada -------------------------------------------------


def test_verdict_no_value_when_effective_n_is_below_100_even_with_a_clear_difference():
    verdict = ai_value_verdict(independent_trades(50, 2.0, 0.0))
    assert verdict.verdict == "LA_IA_NO_APORTA_VALOR"
    assert "N efectivo insuficiente" in verdict.reason


def test_verdict_adds_value_only_with_enough_clusters_and_a_positive_interval():
    verdict = ai_value_verdict(independent_trades(120, 2.0, 0.0))
    assert verdict.verdict == "APORTA_VALOR"
    assert verdict.n_eff_approved >= 100 and verdict.n_eff_rejected >= 100


def test_verdict_no_value_when_the_interval_includes_zero_with_enough_n():
    verdict = ai_value_verdict(independent_trades(120, 0.0, 0.0))
    assert verdict.verdict == "LA_IA_NO_APORTA_VALOR"
    assert "incluye el cero" in verdict.reason


def test_verdict_is_no_data_when_there_are_no_llm_decisions():
    shadow_only = [trade(i, opened_h=i * 10, closed_h=i * 10 + 1, pnl=1.0, label="SIN_LLM")
                   for i in range(5)]
    assert ai_value_verdict(shadow_only).verdict == "SIN_DATOS"


# --- reporte: grupos mixtos aparte --------------------------------------------


def test_report_separates_single_strategy_groups_from_mixed_ones():
    single = trade(1, opened_h=0, closed_h=1, pnl=2.0, strategies=("ema_cross_9_21",))
    mixed = trade(2, opened_h=5, closed_h=6, pnl=-1.0,
                  strategies=("donchian_breakout_20", "ema_cross_9_21"))
    rows = {r.strategy: r for r in shadow_report.summarize_by_strategy([], [single, mixed])}
    ema = rows["ema_cross_9_21"]
    assert ema.single_closed == 1 and ema.single_pnl_net == 2.0
    assert ema.mixed_closed == 1 and ema.mixed_pnl_net == -1.0
    assert rows["donchian_breakout_20"].single_closed == 0
    assert rows["donchian_breakout_20"].mixed_closed == 1


def test_report_shows_raw_and_effective_n_and_the_verdict():
    trades = independent_trades(120, 2.0, 0.0)
    text = shadow_report.render_markdown(
        shadow_report.summarize_by_strategy([], trades),
        shadow_report.summarize_totals([], trades),
        ai_value_verdict(trades),
    )
    assert "N efectivo" in text and "N bruto" in text
    assert "APORTA_VALOR" in text
    assert "Grupos de una sola estrategia" in text
