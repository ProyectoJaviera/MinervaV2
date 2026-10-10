"""Criterio de valor de la IA sobre la sombra (subfase 3.6, fase ii): conglomerados
por solape, bootstrap por conglomerados sobre fraccion del margen, regla de los
tres veredictos (APORTA_VALOR/NO_APORTA_VALOR/INCONCLUSO, con MAGNITUD_BAJA),
tope de SIN_LLM, exclusion del piloto y calibracion por placebo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.trading import shadow_report
from app.trading.ai_value import (
    APROBADA,
    MIN_EFFECTIVE_N,
    RECHAZADA,
    ai_value_verdict,
    assign_clusters,
    build_units,
    cluster_bootstrap_difference,
    placebo_calibration,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def trade(id_, *, opened_h, closed_h, pnl, label=APROBADA, symbol="BTCUSDT",
          side=Side.LONG, strategies=("ema_cross_9_21",), margin=10.0, status=TradeStatus.CLOSED):
    return ShadowTrade(
        id=id_, symbol=symbol, side=side, strategy=strategies[0], status=status,
        leverage=10, margin_usdt=margin, notional_usdt=margin * 10, qty=1.0, entry_price=100.0,
        opened_at=BASE + timedelta(hours=opened_h),
        closed_at=(BASE + timedelta(hours=closed_h)) if status == TradeStatus.CLOSED else None,
        pnl_net_usdt=pnl if status == TradeStatus.CLOSED else None,
        contributing_strategies=list(strategies),
        signal_group_key=f"{symbol}|{side.value}|{id_}",
        candle_close_time=BASE + timedelta(hours=opened_h), llm_decision=label,
    )


def independent_trades(n: int, approved_mean: float, rejected_mean: float, margin: float = 10.0):
    """n operaciones por lado, cada una en su propio intervalo (sin solapes), con
    margen `margin` -- el PnL en USDT se da en esa escala (p.ej. `approved_mean=2.0`
    con `margin=10.0` es un retorno medio de 0.20 = 20% del margen)."""
    out = []
    for i in range(n):
        start = i * 10.0  # horas: intervalos disjuntos
        noise = 0.5 if i % 2 == 0 else -0.5
        out.append(trade(2 * i + 1, opened_h=start, closed_h=start + 1,
                         pnl=approved_mean + noise, label=APROBADA, margin=margin))
        out.append(trade(2 * i + 2, opened_h=start + 2, closed_h=start + 3,
                         pnl=rejected_mean + noise, label=RECHAZADA, margin=margin))
    return out


def sin_llm_trades(n: int, start_hour: float = 100_000.0):
    """`n` grupos SIN_LLM, cada uno en su propio intervalo y simbolo (el cluster no
    importa para el SIN_LLM share, que se cuenta por grupo)."""
    return [
        trade(900_000 + i, opened_h=start_hour + i, closed_h=start_hour + i + 1, pnl=0.0,
              label="SIN_LLM", symbol=f"SIM{i}USDT")
        for i in range(n)
    ]


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
    still_open = trade(2, opened_h=0, closed_h=0, pnl=0, status=TradeStatus.OPEN)
    assert set(assign_clusters([closed, still_open])) == {1}


def test_effective_n_is_the_number_of_clusters_not_trades():
    trades = [trade(1, opened_h=0, closed_h=5, pnl=1, label=APROBADA),
              trade(2, opened_h=1, closed_h=6, pnl=1, label=RECHAZADA),
              trade(3, opened_h=2, closed_h=7, pnl=1, label=APROBADA)]
    units = build_units(trades)
    assert len(units) == 1  # tres operaciones solapadas = una unidad
    verdict = ai_value_verdict(trades, min_effective_n=1)
    assert verdict.n_raw_approved == 2 and verdict.n_eff_approved == 1


# --- bootstrap por conglomerados, sobre fraccion del margen -------------------


def test_bootstrap_is_deterministic_with_the_same_seed():
    units = build_units(independent_trades(30, 2.0, 0.0))
    first = cluster_bootstrap_difference(units, seed=7)
    assert first == cluster_bootstrap_difference(units, seed=7)


def test_bootstrap_difference_is_a_fraction_of_margin_not_raw_usdt():
    # pnl 2.0 USDT sobre margen 10 = retorno 0.20 (20% del margen), no 2.0.
    units = build_units(independent_trades(120, 2.0, 0.0))
    diff, lo, hi = cluster_bootstrap_difference(units)
    assert 0.15 < diff < 0.25 and lo > 0 and hi > lo


def test_bootstrap_difference_is_the_same_fraction_regardless_of_margin_size():
    units_margin_10 = build_units(independent_trades(60, 2.0, 0.0, margin=10.0))
    units_margin_5 = build_units(independent_trades(60, 1.0, 0.0, margin=5.0))
    diff_10, _, _ = cluster_bootstrap_difference(units_margin_10, seed=1)
    diff_5, _, _ = cluster_bootstrap_difference(units_margin_5, seed=1)
    assert diff_10 == pytest.approx(diff_5)  # misma fraccion del margen (0.20) en ambos


def test_bootstrap_interval_contains_zero_when_there_is_no_difference():
    units = build_units(independent_trades(120, 0.0, 0.0))
    _diff, lo, hi = cluster_bootstrap_difference(units)
    assert lo <= 0 <= hi


# --- regla de los tres veredictos, punto de analisis en MIN_EFFECTIVE_N -------


def test_verdict_is_inconclusive_when_effective_n_is_below_the_analysis_point():
    verdict = ai_value_verdict(independent_trades(MIN_EFFECTIVE_N - 50, 2.0, -1.0))
    assert verdict.verdict == "INCONCLUSO"
    assert "N efectivo insuficiente" in verdict.reason


def test_verdict_adds_value_with_enough_clusters_and_a_clearly_positive_interval():
    # diferencia = (2.0 - (-1.0)) / 10 = 0.30 (30% del margen), muy por encima de δ.
    verdict = ai_value_verdict(independent_trades(MIN_EFFECTIVE_N, 2.0, -1.0))
    assert verdict.verdict == "APORTA_VALOR"
    assert verdict.magnitud_baja is False
    assert verdict.n_eff_approved >= MIN_EFFECTIVE_N and verdict.n_eff_rejected >= MIN_EFFECTIVE_N


def test_verdict_adds_value_with_low_magnitude_when_the_interval_stays_under_delta():
    # diferencia = (0.3 - 0.1) / 10 = 0.02 (2% del margen): positiva pero bajo δ=3%.
    verdict = ai_value_verdict(independent_trades(MIN_EFFECTIVE_N, 0.3, 0.1))
    assert verdict.verdict == "APORTA_VALOR"
    assert verdict.magnitud_baja is True
    assert "δ" in verdict.reason


def test_verdict_no_value_when_the_interval_stays_under_delta_and_not_positive():
    verdict = ai_value_verdict(independent_trades(MIN_EFFECTIVE_N, 0.0, 0.0))
    assert verdict.verdict == "NO_APORTA_VALOR"


def test_verdict_no_value_when_the_ia_clearly_subtracts_value():
    verdict = ai_value_verdict(independent_trades(MIN_EFFECTIVE_N, -2.0, 1.0))
    assert verdict.verdict == "NO_APORTA_VALOR"
    assert "resta valor" in verdict.reason


def test_verdict_is_inconclusive_when_everything_is_sin_llm():
    # 100% SIN_LLM: choca primero con el tope de la seccion m, no con "sin datos
    # en ambos lados" (ese caso lo cubren los tests de "todos APROBADA/RECHAZADA").
    shadow_only = sin_llm_trades(5)
    verdict = ai_value_verdict(shadow_only)
    assert verdict.verdict == "INCONCLUSO"
    assert "sesgo de exclusion" in verdict.reason
    assert verdict.sin_llm_share == 1.0


def test_verdict_is_inconclusive_when_all_groups_are_approved():
    all_approved = [
        trade(i, opened_h=i * 10, closed_h=i * 10 + 1, pnl=1.0, label=APROBADA,
              symbol=f"SIM{i}USDT")
        for i in range(10)
    ]
    verdict = ai_value_verdict(all_approved)
    assert verdict.verdict == "INCONCLUSO"
    assert verdict.n_raw_approved == 10 and verdict.n_raw_rejected == 0


def test_verdict_is_inconclusive_when_all_groups_are_rejected():
    all_rejected = [
        trade(i, opened_h=i * 10, closed_h=i * 10 + 1, pnl=1.0, label=RECHAZADA,
              symbol=f"SIM{i}USDT")
        for i in range(10)
    ]
    verdict = ai_value_verdict(all_rejected)
    assert verdict.verdict == "INCONCLUSO"
    assert verdict.n_raw_rejected == 10 and verdict.n_raw_approved == 0


# --- tope de SIN_LLM: posible sesgo de exclusion -------------------------------


def test_verdict_is_inconclusive_when_sin_llm_exceeds_the_cap_even_with_a_clear_signal():
    # Por si sola, esta mezcla seria APORTA_VALOR (ver test de arriba). Con 80
    # grupos SIN_LLM agregados (80 / (600+80) = 11.8% > 10%), pasa a INCONCLUSO.
    clear_signal = independent_trades(MIN_EFFECTIVE_N, 2.0, -1.0)
    trades = clear_signal + sin_llm_trades(80)
    verdict = ai_value_verdict(trades)
    assert verdict.verdict == "INCONCLUSO"
    assert "sesgo de exclusion" in verdict.reason
    assert verdict.sin_llm_share > 0.10


def test_verdict_passes_when_sin_llm_is_below_the_cap():
    clear_signal = independent_trades(MIN_EFFECTIVE_N, 2.0, -1.0)
    trades = clear_signal + sin_llm_trades(10)  # 10 / 610 = 1.6%, bien por debajo
    verdict = ai_value_verdict(trades)
    assert verdict.verdict == "APORTA_VALOR"
    assert verdict.sin_llm_share < 0.10


def test_sin_llm_share_counts_open_groups_too():
    trades = independent_trades(5, 1.0, 1.0) + sin_llm_trades(5)
    verdict = ai_value_verdict(trades, min_effective_n=1)
    assert verdict.total_groups == 15 and verdict.sin_llm_count == 5
    assert verdict.sin_llm_share == 5 / 15


# --- exclusion del piloto -------------------------------------------------------


def test_piloto_groups_are_excluded_before_any_calculation():
    trades = independent_trades(5, 2.0, -1.0, margin=10.0)
    piloto_keys = {trades[0].signal_group_key, trades[1].signal_group_key}
    verdict_with_piloto = ai_value_verdict(trades, min_effective_n=1)
    verdict_excluding_piloto = ai_value_verdict(trades, min_effective_n=1, piloto_keys=piloto_keys)
    assert verdict_excluding_piloto.total_groups == verdict_with_piloto.total_groups - 2


# --- unreliable_keys: sombras con reconciliacion fallida no cuentan ----------
# (incidente de estabilidad 2026-10-10, Etapa 2b)


def test_unreliable_groups_are_excluded_before_any_calculation():
    trades = independent_trades(5, 2.0, -1.0, margin=10.0)
    unreliable_keys = {trades[0].signal_group_key, trades[1].signal_group_key}
    verdict_with = ai_value_verdict(trades, min_effective_n=1)
    verdict_excluding = ai_value_verdict(
        trades, min_effective_n=1, unreliable_keys=unreliable_keys
    )
    assert verdict_excluding.total_groups == verdict_with.total_groups - 2


def test_unreliable_keys_is_a_separate_filter_from_piloto_keys():
    """Las dos razones de exclusion son independientes: un grupo puede estar en
    una, en la otra, en ambas, o en ninguna, y cada una descarta lo suyo."""
    trades = independent_trades(5, 2.0, -1.0, margin=10.0)
    piloto_keys = {trades[0].signal_group_key}
    unreliable_keys = {trades[1].signal_group_key}
    verdict = ai_value_verdict(
        trades, min_effective_n=1, piloto_keys=piloto_keys, unreliable_keys=unreliable_keys,
    )
    verdict_neither = ai_value_verdict(trades, min_effective_n=1)
    assert verdict.total_groups == verdict_neither.total_groups - 2


# --- measurement_keys: sombras sin fila en llm_logs no cuentan ----------------


def test_shadows_without_an_llm_logs_row_do_not_count_toward_the_measurement():
    # Sombras de antes de la 3.6 (o de otra version del prompt): nunca pasaron
    # por el LLM de esta medicion. Sin `measurement_keys` igual cuentan como
    # SIN_LLM (comportamiento anterior); con el, se descartan del todo.
    measured = independent_trades(5, 2.0, -1.0, margin=10.0)
    pre_llm_shadows = sin_llm_trades(20, start_hour=500_000.0)
    trades = measured + pre_llm_shadows
    measurement_keys = {t.signal_group_key for t in measured}

    without_filter = ai_value_verdict(trades, min_effective_n=1)
    with_filter = ai_value_verdict(trades, min_effective_n=1, measurement_keys=measurement_keys)

    assert without_filter.total_groups == 30 and without_filter.sin_llm_count == 20
    assert with_filter.total_groups == 10 and with_filter.sin_llm_count == 0


def test_a_failed_decision_still_counts_as_sin_llm_within_the_measurement():
    # TIMEOUT/ERROR_HTTP/BUDGET_EXCEEDED SI dejan fila en llm_logs (status
    # distinto de OK), asi que su grupo entra en measurement_keys y sigue
    # contando como SIN_LLM: se intento decidir y no se pudo.
    measured = independent_trades(5, 2.0, -1.0, margin=10.0)
    failed_call = sin_llm_trades(1, start_hour=600_000.0)  # su grupo SI tiene fila en llm_logs
    trades = measured + failed_call
    measurement_keys = {t.signal_group_key for t in measured} | {failed_call[0].signal_group_key}

    verdict = ai_value_verdict(trades, min_effective_n=1, measurement_keys=measurement_keys)

    assert verdict.total_groups == 11 and verdict.sin_llm_count == 1


# --- placebo: el metodo no debe inventar valor bajo una relabelacion al azar --


def test_placebo_frequency_is_low_when_there_is_no_true_effect():
    # Sin verdadera diferencia: la relabelacion al azar no deberia producir
    # APORTA_VALOR con frecuencia alta. Dataset chico, `min_effective_n` bajo y
    # pocas repeticiones para que el test corra rapido; el valor por defecto en
    # produccion es 1.000 repeticiones (ver docstring de `placebo_calibration`).
    trades = independent_trades(20, 1.0, 1.0)  # mismo promedio en los dos "lados" originales
    result = placebo_calibration(trades, approval_rate=0.5, n_repeats=25, min_effective_n=8)
    assert result.n_repeats == 25
    assert 0.0 <= result.frequency <= 0.3
    assert 0.0 <= result.ci_low <= result.ci_high <= 1.0
    assert result.calibrated == (result.frequency < 0.05)


def test_placebo_is_deterministic_with_the_same_seed():
    trades = independent_trades(20, 1.0, 1.0)
    first = placebo_calibration(trades, approval_rate=0.5, n_repeats=10, min_effective_n=8, seed=3)
    second = placebo_calibration(trades, approval_rate=0.5, n_repeats=10, min_effective_n=8, seed=3)
    assert first == second


# --- reporte: grupos mixtos aparte, y el veredicto en el markdown -------------


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


def test_report_shows_raw_and_effective_n_and_the_three_verdict_fields():
    trades = independent_trades(MIN_EFFECTIVE_N, 2.0, -1.0)
    text = shadow_report.render_markdown(
        shadow_report.summarize_by_strategy([], trades),
        shadow_report.summarize_totals([], trades),
        ai_value_verdict(trades),
    )
    assert "N efectivo" in text and "N bruto" in text
    assert "APORTA_VALOR" in text
    assert "SIN_LLM" in text
    assert "Grupos de una sola estrategia" in text
