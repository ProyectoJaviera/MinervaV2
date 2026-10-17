"""`app.trading.ai_value_report`: informe del veredicto de valor de la IA. Datos
SINTETICOS en una base de archivo con el esquema real, reabierta en solo lectura
(mismo patron que `test_measurement_status_script.py`)."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from app.trading import ai_value_report
from app.trading.ai_value_report import (
    STATE_GATE_CLOSED,
    STATE_POINT_NOT_REACHED,
    STATE_VERDICT,
    LogMeta,
    SyntheticGuardError,
    assert_synthetic_database,
    build_report,
    compute_sin_llm_diagnostics,
    render_report,
)
from tests.unit.synthetic_measurement import (
    BASE,
    SYNTHETIC_HASH,
    Group,
    create_schema,
    insert_groups,
    open_read_only,
    scenario,
)

SMALL_N = 40


@pytest.fixture
def small_n(monkeypatch):
    """Baja el punto de analisis a 40 conglomerados por lado: el gate
    (`measurement_status`) y el informe leen cada uno su propia copia de la
    constante."""
    monkeypatch.setattr("app.trading.measurement_status.MIN_EFFECTIVE_N", SMALL_N)
    monkeypatch.setattr("app.trading.ai_value_report.MIN_EFFECTIVE_N", SMALL_N)
    return SMALL_N


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_verdict.db"
    await create_schema(path)
    return path


def run_report(path):
    conn = open_read_only(path)
    try:
        return build_report(conn, frozen_hash=SYNTHETIC_HASH, allow_synthetic_for_tests=True)
    finally:
        conn.close()


# --- gate cerrado: nunca se leen resultados --------------------------------------


async def test_gate_closed_refuses_and_never_reads_outcomes(db_path, monkeypatch):
    insert_groups(db_path, scenario(299, 300))  # constantes REALES: 299 < 300

    def _boom(conn):
        raise AssertionError("se leyeron resultados con el gate cerrado")

    monkeypatch.setattr(ai_value_report, "_load_outcome_trades", _boom)

    result = run_report(db_path)

    assert result.state == STATE_GATE_CLOSED
    text = render_report(result)
    assert "GATE CERRADO" in text
    assert "299/300" in text
    for forbidden in (
        "Diferencia de esperanza", "IC 95", "APORTA_VALOR", "NO_APORTA", "INCONCLUSO", "Veredicto:",
    ):
        assert forbidden not in text


async def test_gate_closed_with_an_empty_database(db_path):
    insert_groups(db_path, [Group(key="x", opened_at=BASE, closed_at=None, label="SIN_LLM")])

    result = run_report(db_path)

    assert result.state == STATE_GATE_CLOSED


async def test_gate_opens_at_exactly_the_minimum_on_both_sides(db_path, small_n):
    insert_groups(db_path, scenario(small_n - 1, small_n))
    assert run_report(db_path).state == STATE_GATE_CLOSED

    insert_groups(db_path, scenario(1, 0, prefix="extra", start_hour=100_000))
    assert run_report(db_path).state == STATE_VERDICT


# --- guardia estricta: gate abierto pero N limpio insuficiente -----------------------


async def test_gate_open_but_clean_n_below_minimum_suppresses_every_outcome(db_path, small_n):
    groups = scenario(small_n, small_n)
    for g in groups[:3]:  # 3 conglomerados aprobados con reconciliacion fallida
        g.reconciliation_failed = True
    insert_groups(db_path, groups)

    result = run_report(db_path)

    assert result.state == STATE_POINT_NOT_REACHED
    assert result.status.n_eff_approved == small_n
    assert (result.clean_n_approved, result.clean_n_rejected) == (small_n - 3, small_n)
    assert result.verdict is None and result.sensitivity is None
    text = render_report(result)
    assert "PUNTO DE ANALISIS NO ALCANZADO" in text
    for forbidden in ("Diferencia", "IC 95", "Veredicto:", "Desglose"):
        assert forbidden not in text
    assert "con reconciliacion fallida, EXCLUIDOS" in text


# --- bandera de datos sinteticos ----------------------------------------------------------


async def test_synthetic_guard_accepts_a_purely_synthetic_database(db_path):
    insert_groups(db_path, scenario(2, 2))
    conn = open_read_only(db_path)
    try:
        assert_synthetic_database(conn)
    finally:
        conn.close()


async def test_synthetic_guard_rejects_a_row_from_the_real_model(db_path):
    groups = scenario(2, 2)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)
    conn = open_read_only(db_path)
    try:
        with pytest.raises(SyntheticGuardError, match="no es sintetica"):
            assert_synthetic_database(conn)
        with pytest.raises(SyntheticGuardError):
            build_report(conn, frozen_hash=SYNTHETIC_HASH, allow_synthetic_for_tests=True)
    finally:
        conn.close()


async def test_synthetic_guard_rejects_the_real_frozen_hash(db_path):
    from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256

    groups = scenario(2, 2)
    groups[0].prompt_hash = FROZEN_PROMPT_SHA256
    insert_groups(db_path, groups)
    conn = open_read_only(db_path)
    try:
        with pytest.raises(SyntheticGuardError, match="hash congelado real"):
            assert_synthetic_database(conn)
    finally:
        conn.close()


async def test_synthetic_guard_rejects_a_database_without_llm_logs(db_path):
    conn = open_read_only(db_path)
    try:
        with pytest.raises(SyntheticGuardError, match="no se puede probar"):
            assert_synthetic_database(conn)
    finally:
        conn.close()


async def test_a_non_frozen_hash_without_the_synthetic_flag_is_refused(db_path):
    insert_groups(db_path, scenario(2, 2))
    conn = open_read_only(db_path)
    try:
        with pytest.raises(ValueError, match="bandera sintetica"):
            build_report(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()


# --- veredicto y solo lectura --------------------------------------------------------------


async def test_full_report_reaches_a_verdict_and_never_writes_to_the_database(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.30, rejected_ret=0.0))
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    result = run_report(db_path)
    text = render_report(result)

    assert result.state == STATE_VERDICT
    assert result.verdict.verdict == "APORTA_VALOR"
    assert result.verdict.n_eff_approved == result.verdict.n_eff_rejected == small_n
    assert "Veredicto: APORTA_VALOR" in text
    assert "Diferencia de esperanza" in text
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


async def test_verdict_text_reminds_the_placebo_first_procedure(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.30))

    text = render_report(run_report(db_path))

    assert "Orden del procedimiento (decision del 2026-10-17" in text
    assert "NO INFORMATIVO" in text and "7 dias mas de datos" in text
    assert "ESA MISMA copia" in text and "una sola vez, con --out" in text
    assert "NO calibrado" in text
    assert "nunca de resultados" in text


async def test_gate_closed_text_does_not_carry_the_procedure_reminder(db_path):
    insert_groups(db_path, scenario(5, 5))

    text = render_report(run_report(db_path))

    assert "Orden del procedimiento" not in text


async def test_real_constants_300_per_side_reach_a_verdict_in_reasonable_time(db_path):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    result = run_report(db_path)

    assert result.state == STATE_VERDICT
    assert result.verdict.n_eff_approved == result.verdict.n_eff_rejected == 300
    assert result.verdict.verdict == "APORTA_VALOR"


async def test_report_is_deterministic(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.30))

    assert render_report(run_report(db_path)) == render_report(run_report(db_path))


async def test_no_value_scenario_does_not_produce_a_breakdown(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.0, rejected_ret=0.0))

    result = run_report(db_path)

    assert result.verdict.verdict != "APORTA_VALOR"
    assert result.by_strategy == [] and result.by_symbol == []
    assert "Desglose" not in render_report(result)


# --- seccion (m): particion y omitidos -------------------------------------------------------


async def test_partition_separates_pilot_other_hash_and_omitted_groups(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n))
    first_candle = BASE
    omitted_pre = Group(
        key="pre", opened_at=first_candle - timedelta(days=2),
        closed_at=first_candle - timedelta(days=2) + timedelta(hours=1), log=False,
    )
    omitted_post = [
        Group(key=f"post{i}", opened_at=BASE + timedelta(days=30 + i), log=False, label="SIN_LLM",
              ret=None, side="SHORT", strategy="trend_atr_stop")
        for i in range(2)
    ]
    piloto = Group(key="pil", opened_at=BASE - timedelta(days=5), fase="PILOTO",
                   closed_at=BASE - timedelta(days=5) + timedelta(hours=1))
    other_hash = Group(key="oh", opened_at=BASE - timedelta(days=4), prompt_hash="otro-hash",
                       closed_at=BASE - timedelta(days=4) + timedelta(hours=1))
    insert_groups(db_path, [omitted_pre, *omitted_post, piloto, other_hash])

    result = run_report(db_path)
    p = result.partition

    assert p.measurement == 2 * small_n
    assert p.piloto == 1 and p.other_hash == 1
    assert p.omitted_pre_activation == 1 and p.omitted_post_activation == 2
    assert p.total_groups == 2 * small_n + 5
    # coincide con el conteo del estado de la medicion (misma definicion)
    assert p.omitted_post_activation == _status_omitted(db_path)
    assert result.omitted_profile.omitted_by_side == {"SHORT": 2}
    assert result.omitted_profile.omitted_by_strategy == {"trend_atr_stop": 2}
    text = render_report(result)
    assert "ANTERIORES a la activacion: 1" in text
    assert "POSTERIORES a la activacion: 2" in text


def _status_omitted(path) -> int:
    from app.trading.measurement_status import compute_status

    conn = open_read_only(path)
    try:
        return compute_status(conn, frozen_hash=SYNTHETIC_HASH).omitted_without_row
    finally:
        conn.close()


async def test_omitted_groups_do_not_change_the_verdict(db_path, small_n, tmp_path):
    groups = scenario(small_n, small_n, approved_ret=0.25)
    insert_groups(db_path, groups)
    without = run_report(db_path)

    extra = [
        Group(key=f"o{i}", opened_at=BASE + timedelta(days=100 + i), log=False, label="SIN_LLM",
              closed_at=BASE + timedelta(days=100 + i, hours=1), ret=-5.0)
        for i in range(5)
    ]
    insert_groups(db_path, extra)
    with_omitted = run_report(db_path)

    assert with_omitted.partition.omitted_post_activation == 5
    assert with_omitted.verdict == without.verdict


async def test_sin_llm_above_the_cap_is_inconclusive_without_looking_at_the_interval(
    db_path, small_n,
):
    groups = scenario(small_n, small_n, approved_ret=0.30)
    failures = [
        Group(key=f"f{i}", opened_at=BASE + timedelta(days=200 + i), label="SIN_LLM", ret=None,
              status="TIMEOUT", hour_utc=2, atr_pct=None)
        for i in range(12)  # 12 / (80 + 12) = 13 % > 10 %
    ]
    insert_groups(db_path, groups + failures)

    result = run_report(db_path)

    assert result.state == STATE_VERDICT
    assert result.verdict.verdict == "INCONCLUSO"
    assert result.verdict.diff is None and result.verdict.ci_low is None
    assert "SIN_LLM" in result.verdict.reason
    assert result.diagnostics.sin_llm_by_cause == {"TIMEOUT": 12}
    text = render_report(result)
    assert "TIMEOUT: 12" in text
    assert "00-03 UTC" in text


def test_diagnostics_flag_a_concentration_of_failures_in_one_hour_bin():
    metas = []
    for i in range(20):
        metas.append(LogMeta(
            key=f"k{i}", fase="MEDICION", prompt_sha256="h", status="OK" if i >= 5 else "TIMEOUT",
            json_repaired=False, hour_utc=1 if i < 5 else 13, atr_pct=float(i),
            created_at=BASE,
        ))

    diag = compute_sin_llm_diagnostics(metas)

    by_bin = {s.label: s for s in diag.by_hour_bin}
    assert by_bin["00-03 UTC"].all_share == pytest.approx(0.25)
    assert by_bin["00-03 UTC"].sin_llm_share == pytest.approx(1.0)
    assert by_bin["00-03 UTC"].ratio == pytest.approx(4.0)
    assert by_bin["12-15 UTC"].sin_llm_share == pytest.approx(0.0)
    assert {s.label for s in diag.by_atr_tercile} >= {"bajo", "medio", "alto"}
    # las 5 fallas tienen atr bajo (0..4): caen en el tercil "bajo"
    assert {s.label: s for s in diag.by_atr_tercile}["bajo"].sin_llm_share == pytest.approx(1.0)


# --- sensibilidad con JSON reparado ---------------------------------------------------------------


async def test_sensitivity_without_repaired_rows_matches_the_main_verdict(db_path, small_n):
    insert_groups(db_path, scenario(small_n, small_n, approved_ret=0.30))

    result = run_report(db_path)

    assert result.sensitivity.excluded_groups == 0 and result.sensitivity.verdict is None
    assert "coincide con el veredicto principal" in render_report(result)


async def test_sensitivity_flags_a_verdict_that_depends_on_repaired_rows(db_path, small_n):
    groups = scenario(small_n, small_n, approved_ret=0.0, rejected_ret=0.0)
    approved = [g for g in groups if g.label == "APROBADA"]
    for g in approved[:10]:  # 10 aprobadas con retorno enorme, todas con JSON reparado
        g.ret = 1.0
        g.json_repaired = True
    insert_groups(db_path, groups)

    result = run_report(db_path)

    assert result.verdict.verdict == "APORTA_VALOR"
    assert result.sensitivity.excluded_groups == 10
    assert result.sensitivity.verdict.verdict != "APORTA_VALOR"
    assert result.sensitivity.robust is False
    text = render_report(result)
    assert "SENSIBLE" in text
    assert "ORIENTATIVO" in text  # N efectivo aprobado 30 < 40


async def test_sensitivity_is_robust_when_repaired_rows_look_like_the_rest(db_path, small_n):
    groups = scenario(small_n, small_n, approved_ret=0.30, rejected_ret=0.0)
    for g in [g for g in groups if g.label == "APROBADA"][:4]:
        g.json_repaired = True
    insert_groups(db_path, groups)

    result = run_report(db_path)

    assert result.verdict.verdict == "APORTA_VALOR"
    assert result.sensitivity.excluded_groups == 4
    assert result.sensitivity.robust is True
    assert "ROBUSTO" in render_report(result)


# --- desglose solo con APORTA_VALOR -----------------------------------------------


async def test_breakdown_by_strategy_and_symbol_partitions_every_labeled_trade(
    db_path, monkeypatch,
):
    monkeypatch.setattr("app.trading.measurement_status.MIN_EFFECTIVE_N", 45)
    monkeypatch.setattr("app.trading.ai_value_report.MIN_EFFECTIVE_N", 45)
    big = scenario(40, 40, approved_ret=0.30, prefix="big", strategy="ema_cross_9_21",
                   approved_symbol="BTCUSDT", rejected_symbol="BTCUSDT", start_hour=0)
    # misma simbolo pero lados/intervalos distintos: separar rechazadas en SHORT
    for g in big:
        if g.label == "RECHAZADA":
            g.side = "SHORT"
    small = scenario(10, 10, approved_ret=0.30, prefix="small", strategy="trend_atr_stop",
                     approved_symbol="ETHUSDT", rejected_symbol="ETHUSDT", start_hour=50_000)
    for g in small:
        if g.label == "RECHAZADA":
            g.side = "SHORT"
    small[0].contributing = ("trend_atr_stop", "ema_cross_9_21")  # grupo mixto
    insert_groups(db_path, big + small)

    result = run_report(db_path)

    assert result.verdict.verdict == "APORTA_VALOR"
    total_labeled = 40 + 40 + 10 + 10
    for buckets in (result.by_strategy, result.by_symbol):
        assert sum(b.n_trades for b in buckets) == total_labeled
    strat = {b.label: b for b in result.by_strategy}
    assert strat["ema_cross_9_21"].diff is not None
    assert strat["trend_atr_stop"].diff is None  # 10 < 30: sin IC
    assert strat["trend_atr_stop"].n_mixed == 1
    sym = {b.label: b for b in result.by_symbol}
    assert set(sym) == {"BTCUSDT", "ETHUSDT"}
    text = render_report(result)
    assert "EXPLORATORIO" in text and "N insuficiente" in text
