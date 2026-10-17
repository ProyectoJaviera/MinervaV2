"""Reporte de paso a dinero real (subfase 3.8): `app.trading.real_money_report`.

Cubre el ORDEN ESTRICTO (sin reveal permitido no se lee ningun resultado), el
veredicto oficial como insumo (si no es APORTA_VALOR no se evalua nada mas), cada
criterio en sus bordes (PASA / NO PASA / SIN DATOS: la falta de datos nunca pasa) y
las metricas operativas. Todo con datos SINTETICOS; nada toca la red."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import settings
from app.persistence.models import Side, Trade, TradeStatus
from app.trading import real_money_report as rmr
from app.trading.verdict_record import VerdictRecordInfo
from tests.unit.synthetic_measurement import (
    SYNTHETIC_HASH,
    create_schema,
    insert_groups,
    open_read_only,
    scenario,
)

T0 = datetime(2026, 11, 1, tzinfo=UTC)
REPORT_TEXT = (
    "=== Veredicto de valor de la IA (subfase 3.6) ===\n"
    "## Veredicto (bootstrap por conglomerados, regla de la seccion i)\n\n"
    "- Aprobadas: N bruto 310, N efectivo 305\n"
    "- **Veredicto: {verdict}** (intervalo del 95 % por encima de cero)\n"
)


def record(verdict: str = "APORTA_VALOR", magnitud_baja: bool = False) -> VerdictRecordInfo:
    return VerdictRecordInfo(
        path=Path("VEREDICTO_3_6_REGISTRO_2026-12-05.txt"), verdict=verdict,
        magnitud_baja=magnitud_baja, quoted_lines=(f"- **Veredicto: {verdict}**",),
    )


def make_trade(
    i: int, pnl: float, *, symbol: str | None = None, side: Side = Side.LONG,
    hours_apart: float = 2.0, **extra,
) -> Trade:
    """Una operacion cerrada de 1 h, separada de la anterior (un conglomerado por
    operacion). `symbol` por defecto reparte en 10 simbolos."""
    opened = T0 + timedelta(hours=hours_apart * i)
    fields = dict(
        id=i + 1, symbol=symbol or f"S{i % 10}USDT", side=side, strategy="ema_cross_9_21",
        status=TradeStatus.CLOSED, leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0,
        entry_price=100.0, exit_price=101.0, fee_entry_usdt=0.03, fee_exit_usdt=0.03,
        slippage_entry_usdt=0.01, slippage_exit_usdt=0.01, funding_paid_usdt=0.0,
        pnl_gross_usdt=pnl + 0.08, pnl_net_usdt=pnl, close_reason="TP", opened_at=opened,
        closed_at=opened + timedelta(hours=1), decision_source=rmr.EXECUTED_ARM_DECISION_SOURCE,
    )
    fields.update(extra)
    return Trade(**fields)


def pattern_trades(n: int, losses_per_10: int, *, win: float = 1.0, loss: float = -1.0):
    """Perdedoras en los simbolos S0..S{k-1}, ganadoras en el resto (determinista)."""
    return [make_trade(i, loss if i % 10 < losses_per_10 else win) for i in range(n)]


def by_key(criteria):
    return {c.key: c for c in criteria}


def evaluate(trades, *, started=None, baseline=None, counter=None, log_count=None,
             rec=None):
    arm_state = {
        rmr.KEY_REAL_ARM_STARTED_AT: started,
        rmr.KEY_REAL_ARM_DRAWDOWN_BASELINE: baseline,
        rmr.KEY_DRAWDOWN_TRIGGER_COUNT: counter,
    }
    return by_key(rmr.evaluate_criteria(
        record=rec or record(), trades=trades, arm_state=arm_state, settings=settings,
        log_reconcile_count=log_count,
    ))


# --- criterios en sus bordes ----------------------------------------------------------------


def test_no_executed_trades_means_sin_datos_everywhere_except_the_verdict():
    result = evaluate([])

    assert result["ia"].status == rmr.PASA
    for key in ("dias", "n", "pf", "drawdown", "concentracion", "reconciliacion"):
        assert result[key].status == rmr.SIN_DATOS, key
    assert rmr.recommend(list(result.values())) == rmr.RECOMMEND_NO


def test_the_ia_criterion_quotes_the_record_and_flags_magnitud_baja():
    result = evaluate([], rec=record(magnitud_baja=True))

    assert "MAGNITUD_BAJA" in result["ia"].detail
    assert "VEREDICTO_3_6_REGISTRO_2026-12-05.txt" in result["ia"].detail


@pytest.mark.parametrize(
    ("delta", "expected"),
    [(timedelta(days=30), rmr.PASA), (timedelta(days=30) - timedelta(seconds=1), rmr.NO_PASA)],
)
def test_days_edge_at_exactly_thirty_days(delta, expected):
    trades = pattern_trades(3, 0)
    end = max(t.closed_at for t in trades)
    started = (end - delta).isoformat()

    assert evaluate(trades, started=started)["dias"].status == expected


def test_days_fall_back_to_the_first_trade_and_say_so():
    trades = [make_trade(0, 1.0), make_trade(1, 1.0, hours_apart=24 * 40)]  # 40 dias despues
    result = evaluate(trades)["dias"]

    assert result.status == rmr.PASA
    assert "falta real_arm_started_at" in result.detail


def test_days_accept_a_naive_arm_start_without_crashing():
    trades = pattern_trades(2, 0)
    started = (T0 - timedelta(days=40)).replace(tzinfo=None).isoformat()

    assert evaluate(trades, started=started)["dias"].status == rmr.PASA


@pytest.mark.parametrize(("n", "expected"), [(99, rmr.NO_PASA), (100, rmr.PASA)])
def test_closed_trades_edge_at_one_hundred(n, expected):
    result = evaluate(pattern_trades(n, 3))

    assert result["n"].status == expected
    assert f"{n} cerradas, {n} conglomerados efectivos" in result["n"].detail


def test_overlapping_trades_of_the_same_symbol_and_side_count_as_one_cluster():
    # 4 operaciones solapadas del mismo simbolo y direccion = 1 conglomerado
    trades = [make_trade(i, 1.0, symbol="AAAUSDT", hours_apart=0.1) for i in range(4)]
    observed, _, _, n_clusters = rmr.pf_cluster_bootstrap(trades)

    assert n_clusters == 1
    assert observed == float("inf")  # sin perdedoras: PF indefinido, no un numero


def test_profit_factor_strong_edge_passes_both_conditions():
    trades = pattern_trades(100, 3)  # 70 ganadoras, 30 perdedoras: PF 2,33
    result = evaluate(trades)["pf"]

    observed, lo, hi, _ = rmr.pf_cluster_bootstrap(trades)
    assert observed == pytest.approx(70 / 30)
    assert lo > 1.0
    assert result.status == rmr.PASA
    assert "PF 2.33" in result.detail and "IC 95 %" in result.detail


def test_profit_factor_below_the_threshold_does_not_pass():
    trades = pattern_trades(100, 5)  # 50/50: PF 1,0

    assert evaluate(trades)["pf"].status == rmr.NO_PASA


def test_profit_factor_above_threshold_but_ci_floor_not_above_one_does_not_pass():
    """PF puntual 1,38 > 1,3 pero con 100 operaciones el IC cruza 1,0: ruido, no senal."""
    trades = [make_trade(i, 1.0 if i % 100 < 58 else -1.0) for i in range(100)]
    observed, lo, hi, _ = rmr.pf_cluster_bootstrap(trades)

    assert observed > settings.min_profit_factor
    assert lo <= 1.0 < hi
    assert evaluate(trades)["pf"].status == rmr.NO_PASA


def test_profit_factor_needs_the_minimum_sample_before_judging():
    result = evaluate(pattern_trades(99, 3))["pf"]

    assert result.status == rmr.SIN_DATOS
    assert "muestra insuficiente" in result.detail


def test_profit_factor_without_losing_trades_is_not_evaluable():
    result = evaluate([make_trade(i, 1.0) for i in range(100)])["pf"]

    assert result.status == rmr.SIN_DATOS and "PF indefinido" in result.detail


def test_bootstrap_is_deterministic():
    trades = pattern_trades(100, 4)

    assert rmr.pf_cluster_bootstrap(trades) == rmr.pf_cluster_bootstrap(trades)


@pytest.mark.parametrize(
    ("counter", "expected"), [("3", rmr.PASA), ("4", rmr.NO_PASA)],
)
def test_drawdown_counts_activations_since_the_baseline(counter, expected):
    result = evaluate(pattern_trades(3, 0), baseline="2", counter=counter)["drawdown"]

    assert result.status == expected  # 1 activacion pasa; 2 no


def test_drawdown_without_a_baseline_is_sin_datos_even_with_a_zero_counter():
    result = evaluate(pattern_trades(3, 0), baseline=None, counter="0")["drawdown"]

    assert result.status == rmr.SIN_DATOS and "linea base" in result.detail


def test_concentration_edge_at_the_configured_limit():
    # total 10, un simbolo aporta 4 -> exactamente 40 % (el limite pasa); 4,4 de 10 -> 44 %
    ok = [make_trade(0, 4.0, symbol="AAAUSDT")] + [
        make_trade(i, 1.0, symbol=f"S{i}USDT") for i in range(1, 7)
    ]
    bad = [make_trade(0, 4.4, symbol="AAAUSDT")] + [
        make_trade(i, 0.8, symbol=f"S{i}USDT") for i in range(1, 8)
    ]

    assert evaluate(ok)["concentracion"].status == rmr.PASA
    assert evaluate(bad)["concentracion"].status == rmr.NO_PASA


def test_concentration_with_non_positive_total_pnl_does_not_pass():
    result = evaluate([make_trade(0, -1.0), make_trade(1, 0.5)])["concentracion"]

    assert result.status == rmr.NO_PASA


def test_a_closed_trade_without_pnl_is_ignored_by_profitability_but_flags_reconciliation():
    broken = make_trade(
        100, 0.0, pnl_net_usdt=None, pnl_gross_usdt=None, close_reason="RECONCILE_FAILED",
    )
    trades = pattern_trades(100, 3) + [broken]
    result = evaluate(trades)

    assert result["n"].status == rmr.PASA and "100 cerradas" in result["n"].detail
    assert result["concentracion"].status == rmr.PASA  # no revienta con pnl None
    assert result["reconciliacion"].status == rmr.NO_PASA


@pytest.mark.parametrize(
    "extra",
    [{"fill_source": "TICK_UNRECONCILED"}, {"close_reason": "RECONCILE_FAILED"},
     {"reconciliation_failed": True}],
)
def test_reconciliation_proxies_in_the_database_do_not_pass(extra):
    trades = [make_trade(0, 1.0, **extra), make_trade(1, 1.0)]

    assert evaluate(trades)["reconciliacion"].status == rmr.NO_PASA


def test_reconciliation_passes_clean_and_counts_the_log_lines():
    trades = pattern_trades(3, 0)

    assert evaluate(trades, log_count=0)["reconciliacion"].status == rmr.PASA
    assert evaluate(trades, log_count=2)["reconciliacion"].status == rmr.NO_PASA
    clean_without_log = evaluate(trades)["reconciliacion"]
    assert clean_without_log.status == rmr.PASA
    assert "sin --log-file" in clean_without_log.detail


def test_every_criterion_passing_recommends_manual_evaluation_never_enabling():
    trades = pattern_trades(100, 3)
    end = max(t.closed_at for t in trades)
    result = evaluate(
        trades, started=(end - timedelta(days=45)).isoformat(), baseline="0", counter="1",
        log_count=0,
    )

    assert {c.status for c in result.values()} == {rmr.PASA}
    assert rmr.recommend(list(result.values())) == rmr.RECOMMEND_MANUAL
    assert "HABILITA" not in rmr.RECOMMEND_MANUAL.upper()


def test_one_failing_criterion_blocks_the_recommendation():
    trades = pattern_trades(100, 3)
    end = max(t.closed_at for t in trades)
    result = evaluate(
        trades, started=(end - timedelta(days=45)).isoformat(), baseline="0", counter="5",
        log_count=0,
    )

    assert result["drawdown"].status == rmr.NO_PASA
    assert rmr.recommend(list(result.values())) == rmr.RECOMMEND_NO


# --- calculos operativos -------------------------------------------------------------------------


def test_delay_stats_percentiles_and_the_over_limit_count():
    delays = [19.7, 25.0, 30.0, 45.0, 74.1]

    stats = rmr.compute_delay_stats(delays, 60.0)

    assert stats.n == 5 and stats.max == 74.1 and stats.p50 == 30.0
    assert stats.over_limit == 1 and stats.limit_s == 60.0
    assert rmr.compute_delay_stats([], 60.0) is None


def test_delay_exactly_at_the_limit_is_not_over_it():
    assert rmr.compute_delay_stats([60.0], 60.0).over_limit == 0


def test_executability_uses_the_notional_and_floors_to_the_base_precision():
    specs = [
        # TRX: 50 USDT / 0,3355 = 149,03 -> 149 < 150 minimo -> no ejecutable
        {"symbol": "TRXUSDT", "min_trade_volume": 150.0, "base_precision": 0, "has_spec": True,
         "price": 0.3355},
        # BTC: 50 / 78000 = 0,000641 -> floor a 4 decimales = 0,0006, por encima del minimo
        {"symbol": "BTCUSDT", "min_trade_volume": 0.0001, "base_precision": 4, "has_spec": True,
         "price": 78000.0},
        # justo en el minimo tras el redondeo
        {"symbol": "XXXUSDT", "min_trade_volume": 1.0, "base_precision": 0, "has_spec": True,
         "price": 50.0},
        {"symbol": "NOSPECUSDT", "min_trade_volume": None, "base_precision": None,
         "has_spec": False, "price": 1.0},
        {"symbol": "NOPRICEUSDT", "min_trade_volume": 1.0, "base_precision": 0, "has_spec": True,
         "price": None},
    ]

    result = {
        s.symbol: s for s in rmr.classify_executability(specs, margin_usdt=5.0, leverage=10)
    }

    assert result["TRXUSDT"].status == "NO EJECUTABLE" and result["TRXUSDT"].qty == 149
    assert result["BTCUSDT"].status == "EJECUTABLE"
    assert result["XXXUSDT"].status == "EJECUTABLE"
    assert result["NOSPECUSDT"].status == "SIN SPEC"
    assert result["NOPRICEUSDT"].status == "SIN PRECIO"


def test_availability_counts_the_gaps_over_the_threshold():
    base = datetime(2026, 11, 1, tzinfo=UTC)
    hours = [base + timedelta(hours=h) for h in (0, 1, 2, 3, 10, 11, 12, 70)]

    stats = rmr.compute_availability(hours)

    assert stats.hours_with_evaluations == 8
    assert stats.gaps_over_threshold == 2
    assert stats.longest_gap_hours == 58.0
    assert rmr.compute_availability([]) is None


def test_stressed_profit_factor_drops_with_higher_costs():
    trades = [make_trade(i, 0.5 if i % 2 else -0.4) for i in range(40)]

    stressed = {s.label: s.pf for s in rmr.compute_stressed(trades)}

    assert stressed["base"] == pytest.approx(rmr.profit_factor([t.pnl_net_usdt for t in trades]))
    assert stressed["comisiones x2"] < stressed["base"]
    assert stressed["slippage x3"] < stressed["slippage x2"] < stressed["base"]
    assert stressed["comisiones x2 y slippage x3"] < stressed["comisiones x2"]


def test_reconcile_log_count_filters_by_date_with_a_one_day_margin(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text(
        "2026-10-30 10:00:00 CRITICAL Reconciliacion fallida de X\n"  # antes de la ventana
        "2026-10-31 10:00:00 CRITICAL Reconciliacion fallida de Y\n"  # margen de un dia: cuenta
        "2026-11-02 10:00:00 INFO otra cosa\n"
        "2026-11-03 10:00:00 CRITICAL Reconciliacion fallida de Z\n",
        encoding="utf-8",
    )

    assert rmr.count_reconcile_log_lines(log, T0) == 2
    assert rmr.count_reconcile_log_lines(log, None) == 3


# --- orden estricto sobre una base sintetica -----------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_real_money.db"
    await create_schema(path)
    return path


@pytest.fixture
def docs(tmp_path):
    path = tmp_path / "docs"
    path.mkdir()
    return path


def write_record(docs: Path, verdict: str) -> None:
    (docs / "VEREDICTO_3_6_REGISTRO_2026-12-05.txt").write_text(
        REPORT_TEXT.format(verdict=verdict), encoding="utf-8",
    )


def build(db_path: Path, docs: Path, **kwargs):
    conn = open_read_only(db_path)
    try:
        return rmr.build_report(
            conn, settings=settings, frozen_hash=SYNTHETIC_HASH,
            allow_synthetic_for_tests=True, docs_dir=docs, **kwargs,
        )
    finally:
        conn.close()


@pytest.fixture
def no_result_reads(monkeypatch):
    """Cualquier lectura de resultados / trades revienta: prueba el ORDEN ESTRICTO."""

    def boom(*args, **kwargs):
        raise AssertionError("se leyeron resultados antes de permitir el reveal")

    for name in ("_load_executed_trades", "_load_arm_state", "_load_arm_log",
                 "_load_decision_delays", "_load_signal_hours", "_load_universe_specs",
                 "load_verdict_inputs", "compute_breakdown"):
        monkeypatch.setattr(rmr, name, boom)


async def test_gate_closed_reads_nothing_and_only_says_what_is_missing(
    db_path, docs, no_result_reads,
):
    insert_groups(db_path, scenario(299, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")  # un registro valido no basta con el gate cerrado

    report = build(db_path, docs)

    assert report.state == rmr.STATE_NOT_ALLOWED
    assert any("gate de la medicion CERRADO" in m and "299/300" in m for m in report.missing)
    assert not any("registro oficial" in m for m in report.missing)  # ese ya existe
    text = rmr.render_report(report)
    assert "NO SE MUESTRA NINGUN RESULTADO" in text
    for forbidden in ("PF ", "IC 95", "Criterios", "RECOMENDACION", "APORTA_VALOR"):
        assert forbidden not in text
    assert rmr.DISCLAIMER in text


async def test_gate_open_without_the_official_record_reads_nothing(
    db_path, docs, no_result_reads,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    report = build(db_path, docs)

    assert report.state == rmr.STATE_NOT_ALLOWED
    assert len(report.missing) == 1 and "no existe el registro oficial" in report.missing[0]
    assert "docs/VEREDICTO_3_6.md" in rmr.render_report(report)


async def test_gate_closed_and_no_record_lists_both_missing_items(db_path, docs, no_result_reads):
    insert_groups(db_path, scenario(10, 10, approved_ret=0.30))

    report = build(db_path, docs)

    assert report.state == rmr.STATE_NOT_ALLOWED and len(report.missing) == 2


async def test_an_invalid_record_does_not_open_the_reveal(db_path, docs, no_result_reads):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    (docs / "VEREDICTO_3_6_REGISTRO_2026-12-05.txt").write_text("Calibrado: SI", encoding="utf-8")

    assert build(db_path, docs).state == rmr.STATE_NOT_ALLOWED


@pytest.mark.parametrize("verdict", ["INCONCLUSO", "NO_APORTA_VALOR"])
async def test_a_verdict_other_than_aporta_valor_concludes_no_and_evaluates_nothing(
    db_path, docs, no_result_reads, verdict,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, verdict)

    report = build(db_path, docs)

    assert report.state == rmr.STATE_NO_GO
    assert report.recommendation == rmr.RECOMMEND_NO
    assert report.criteria == [] and report.operational is None and report.transfer == []
    text = rmr.render_report(report)
    assert "NO PASAR A DINERO REAL" in text and f"{verdict}, no APORTA_VALOR" in text
    assert "Criterios del brazo ejecutado" not in text
    assert rmr.DISCLAIMER in text


def insert_trades(path: Path, trades: list[Trade]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for t in trades:
            conn.execute(
                """
                INSERT INTO trades (
                    symbol, side, strategy, status, leverage, margin_usdt, notional_usdt, qty,
                    entry_price, exit_price, fee_entry_usdt, fee_exit_usdt, funding_paid_usdt,
                    pnl_gross_usdt, pnl_net_usdt, close_reason, opened_at, closed_at,
                    decision_source, slippage_entry_usdt, slippage_exit_usdt, fill_source,
                    reconciliation_failed
                ) VALUES (?, ?, ?, 'CLOSED', 10, 5, 50, 1, 100, 101, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?)
                """,
                (
                    t.symbol, t.side.value, t.strategy, t.fee_entry_usdt, t.fee_exit_usdt,
                    t.funding_paid_usdt, t.pnl_gross_usdt, t.pnl_net_usdt, t.close_reason,
                    t.opened_at.isoformat(), t.closed_at.isoformat(), t.decision_source,
                    t.slippage_entry_usdt, t.slippage_exit_usdt, t.fill_source,
                    int(t.reconciliation_failed),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def run_sql(path: Path, statements: list[tuple[str, tuple]]) -> None:
    conn = sqlite3.connect(str(path))
    try:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


async def test_aporta_valor_with_an_empty_executed_arm_is_all_sin_datos(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")

    report = build(db_path, docs)

    assert report.state == rmr.STATE_EVALUATED
    statuses = {c.key: c.status for c in report.criteria}
    assert statuses["ia"] == rmr.PASA
    assert {s for k, s in statuses.items() if k != "ia"} == {rmr.SIN_DATOS}
    assert report.recommendation == rmr.RECOMMEND_NO
    assert any("subfase 3.9" in n for n in report.notes)
    text = rmr.render_report(report, settings)
    assert "RECOMENDACION: NO PASAR A DINERO REAL todavia" in text
    assert "[SIN DATOS]" in text and rmr.DISCLAIMER in text


async def test_full_evaluation_with_an_executed_arm_and_operational_metrics(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")
    trades = pattern_trades(100, 3)
    insert_trades(db_path, trades)
    end = max(t.closed_at for t in trades)
    now = datetime(2026, 11, 2, tzinfo=UTC).isoformat()
    run_sql(db_path, [
        ("INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
         (rmr.KEY_REAL_ARM_STARTED_AT, (end - timedelta(days=45)).isoformat(), now)),
        ("INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
         (rmr.KEY_REAL_ARM_DRAWDOWN_BASELINE, "2", now)),
        ("INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
         (rmr.KEY_DRAWDOWN_TRIGGER_COUNT, "3", now)),
        # universo: AAAUSDT no ejecutable (minimo enorme), BBBUSDT si, CCCUSDT sin precio
        ("INSERT INTO asset_universe (refreshed_at, coingecko_id, symbol, included) "
         "VALUES (?, 'a', 'AAAUSDT', 1)", (now,)),
        ("INSERT INTO asset_universe (refreshed_at, coingecko_id, symbol, included) "
         "VALUES (?, 'b', 'BBBUSDT', 1)", (now,)),
        ("INSERT INTO asset_universe (refreshed_at, coingecko_id, symbol, included) "
         "VALUES (?, 'c', 'CCCUSDT', 1)", (now,)),
        ("INSERT INTO asset_universe (refreshed_at, coingecko_id, symbol, included) "
         "VALUES (?, 'd', 'OLDUSDT', 1)", ("2026-10-01T00:00:00+00:00",)),  # snapshot viejo
        ("INSERT INTO contract_specs_cache (symbol, min_trade_volume, base_precision, fetched_at)"
         " VALUES ('AAAUSDT', 1000, 0, ?)", (now,)),
        ("INSERT INTO contract_specs_cache (symbol, min_trade_volume, base_precision, fetched_at)"
         " VALUES ('BBBUSDT', 0.001, 3, ?)", (now,)),
        ("INSERT INTO contract_specs_cache (symbol, min_trade_volume, base_precision, fetched_at)"
         " VALUES ('CCCUSDT', 0.001, 3, ?)", (now,)),
        ("INSERT INTO ohlcv_cache (symbol, interval, price_type, open_time, open, high, low, "
         "close) VALUES ('AAAUSDT', '1m', 'LAST_PRICE', 1, 1, 1, 1, 1.0)", ()),
        ("INSERT INTO ohlcv_cache (symbol, interval, price_type, open_time, open, high, low, "
         "close) VALUES ('BBBUSDT', '1m', 'LAST_PRICE', 1, 10, 10, 10, 10.0)", ()),
        # retrasos de decision: 3 filas de la medicion superan el limite de 60 s
        ("UPDATE llm_logs SET decision_delay_s = 75.0 WHERE id <= 3", ()),
        # evaluaciones del generador de senales: 3 horas distintas, un hueco de 9 h
        *[
            ("INSERT INTO signals (symbol, strategy, timeframe, candle_close_time, evaluated_at,"
             " signal, price_at_eval) VALUES ('AAAUSDT', 'ema_cross_9_21', '15m', ?, ?, 'HOLD', 1)",
             (candle, evaluated))
            for candle, evaluated in (
                ("2026-10-02T00:15:00+00:00", "2026-10-02T00:30:00+00:00"),
                ("2026-10-02T01:15:00+00:00", "2026-10-02T01:30:00+00:00"),
                ("2026-10-02T10:15:00+00:00", "2026-10-02T10:30:00+00:00"),
            )
        ],
    ])
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    report = build(db_path, docs)

    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before  # solo lectura
    assert report.state == rmr.STATE_EVALUATED
    statuses = {c.key: c.status for c in report.criteria}
    assert set(statuses.values()) == {rmr.PASA}, statuses
    assert report.recommendation == rmr.RECOMMEND_MANUAL

    op = report.operational
    assert op.delays.n == 600 and op.delays.over_limit == 3
    by_symbol = {s.symbol: s.status for s in op.executability}
    assert by_symbol == {
        "AAAUSDT": "NO EJECUTABLE", "BBBUSDT": "EJECUTABLE", "CCCUSDT": "SIN PRECIO",
    }  # OLDUSDT es de un snapshot viejo: no entra
    assert op.measured_groups == 600
    assert op.groups_in_non_executable_symbols == 300  # las 300 APROBADAS son de AAAUSDT
    assert op.availability.hours_with_evaluations == 3
    assert op.availability.gaps_over_threshold == 1 and op.availability.longest_gap_hours == 9.0
    assert [s.label for s in op.stressed][0] == "base"
    assert op.fees_total_usdt == pytest.approx(100 * 0.06)

    assert [b.label for b in report.transfer] == ["ema_cross_9_21"]  # elegible y medida
    text = rmr.render_report(report, settings)
    for expected in (
        "## Criterios del brazo ejecutado", "[PASA] PF > 1.3", "Riesgos operativos medidos",
        "3 de 600", "AAAUSDT NO EJECUTABLE", "EXPLORATORIO", "EVALUACION MANUAL",
        "Precio de entrada ejecutado vs sombra: SIN DATOS",
    ):
        assert expected in text, expected
    assert rmr.DISCLAIMER in text


def arm_log_row(key, outcome, *, reason=None, details="{}", symbol="BTCUSDT"):
    return (
        "INSERT INTO paper_execution_log (signal_group_key, created_at, symbol, side, strategy, "
        "decision_delay_s, outcome, reason, details_json) VALUES (?, '2026-11-01T12:00:00+00:00', "
        "?, 'LONG', 'ema_cross_9_21', 25.0, ?, ?, ?)",
        (key, symbol, outcome, reason, details),
    )


async def test_the_report_summarizes_the_arm_log_and_measures_the_executed_entry_vs_the_shadow(
    db_path, docs,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")
    run_sql(db_path, [
        arm_log_row("o1", "OPENED", details='{"entry_delay_s": 30.0, "adverse_gap_bps": 4.0}'),
        arm_log_row("o2", "OPENED", details='{"entry_delay_s": 50.0, "adverse_gap_bps": -2.0}'),
        arm_log_row("o3", "OPENED", details='{"entry_delay_s": 40.0, "adverse_gap_bps": 10.0}'),
        arm_log_row("s1", "SKIPPED", reason="RISK_STRATEGY_NOT_ELIGIBLE"),
        arm_log_row("s2", "SKIPPED", reason="RISK_STRATEGY_NOT_ELIGIBLE"),
        arm_log_row("s3", "SKIPPED", reason="BELOW_MIN_ORDER", symbol="TRXUSDT"),
        arm_log_row("p1", "PENDING"),
    ])

    report = build(db_path, docs)

    arm = report.operational.arm_log
    assert (arm.considered, arm.opened, arm.pending) == (7, 3, 1)
    assert arm.skipped_by_reason == {"RISK_STRATEGY_NOT_ELIGIBLE": 2, "BELOW_MIN_ORDER": 1}
    assert arm.entry_delay_s.median == 40.0 and arm.entry_delay_s.max == 50.0
    assert arm.adverse_gap_bps.median == 4.0 and arm.adverse_gap_bps.max == 10.0
    text = rmr.render_report(report, settings)
    assert "Brazo ejecutado: 7 decisiones APROBADA consideradas: 3 abiertas, 3 omitidas" in text
    assert "RISK_STRATEGY_NOT_ELIGIBLE 2" in text and "BELOW_MIN_ORDER 1" in text
    assert "1 pendientes" in text
    assert "Entrada ejecutada vs sombra (n=3): diferencia de precio mediana +4.0 pb" in text
    assert "positivo = peor que la sombra" in text
    assert "mediana 40.0 s" in text
    assert "SIN DATOS (el brazo ejecutado todavia no abrio operaciones)" not in text


async def test_without_opened_rows_the_entry_comparison_stays_sin_datos(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")
    run_sql(db_path, [arm_log_row("s1", "SKIPPED", reason="LATE_DECISION")])

    report = build(db_path, docs)

    assert report.operational.arm_log.opened == 0
    text = rmr.render_report(report, settings)
    assert "1 decisiones APROBADA consideradas: 0 abiertas, 1 omitidas (LATE_DECISION 1)" in text
    assert "Precio de entrada ejecutado vs sombra: SIN DATOS" in text


async def test_a_copy_without_the_arm_table_is_handled(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")
    run_sql(db_path, [("DROP TABLE paper_execution_log", ())])  # copia anterior a la 3.9

    report = build(db_path, docs)

    assert report.operational.arm_log is None
    assert "Precio de entrada ejecutado vs sombra: SIN DATOS" in rmr.render_report(report, settings)


async def test_transfer_section_drops_strategies_the_real_account_cannot_trade(db_path, docs):
    groups = scenario(300, 300, approved_ret=0.30, strategy="mean_reversion", prefix="m")
    groups += scenario(300, 300, approved_ret=0.30, strategy="ema_cross_9_21", prefix="e",
                       start_hour=5000)
    insert_groups(db_path, groups)
    write_record(docs, "APORTA_VALOR")

    report = build(db_path, docs)

    assert [b.label for b in report.transfer] == ["ema_cross_9_21"]


# --- garantias estructurales ---------------------------------------------------------------------


def test_the_module_cannot_place_orders_or_reach_the_network():
    """v1 es 100 % paper: el reporte no importa ningun backend de ejecucion ni cliente HTTP."""
    source = Path(rmr.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden = ("bitunix", "paper_backend", "httpx", "requests", "aiohttp", "websockets",
                 "urllib", "socket", "anthropic")
    offenders = sorted(m for m in imported if any(word in m.lower() for word in forbidden))
    assert offenders == []


def test_the_module_never_writes_to_the_database():
    source = Path(rmr.__file__).read_text(encoding="utf-8").upper()

    for statement in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "CREATE TABLE"):
        assert statement not in source, statement
