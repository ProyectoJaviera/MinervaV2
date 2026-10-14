"""Avance de la medicion en vivo (TAREA D, docs/FASE3_6_LLM.md seccion k):
`scripts/measurement_status.py`. Todo con datos SINTETICOS (nunca la base
real) -- la verificacion manual contra una copia de la base de estabilidad
con datos reales (CLAUDE.md) se hace aparte, fuera de pytest."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.database import Database
from app.persistence.models import ShadowTrade, Side, TradeStatus
from app.persistence.repositories import llm_logs_repo, shadow_repo
from scripts.measurement_status import (
    APPROVAL_BAND,
    ClosedLabeledTrade,
    MeasurementStatus,
    cluster_effective_counts,
    compute_status,
    format_report,
)

FROZEN = "hash_congelado_de_prueba"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(
    symbol="BTCUSDT", side="LONG", opened_at=T0, closed_at=None,
    llm_decision="APROBADA", created_at=T0,
) -> ClosedLabeledTrade:
    return ClosedLabeledTrade(
        symbol=symbol, side=side, opened_at=opened_at,
        closed_at=closed_at or opened_at + timedelta(hours=1),
        llm_decision=llm_decision, created_at=created_at,
    )


# --- cluster_effective_counts: agrupa por (simbolo, direccion), solapes ---------


def test_non_overlapping_trades_of_the_same_side_are_separate_clusters():
    trades = [
        _trade(opened_at=T0, closed_at=T0 + timedelta(hours=1)),
        _trade(opened_at=T0 + timedelta(hours=2), closed_at=T0 + timedelta(hours=3)),
    ]
    assert cluster_effective_counts(trades) == (2, 0)


def test_overlapping_trades_of_the_same_symbol_and_side_merge_into_one_cluster():
    trades = [
        _trade(opened_at=T0, closed_at=T0 + timedelta(hours=2)),
        _trade(opened_at=T0 + timedelta(hours=1), closed_at=T0 + timedelta(hours=3)),
    ]
    assert cluster_effective_counts(trades) == (1, 0)


def test_a_cluster_with_both_labels_counts_toward_both_sides():
    trades = [
        _trade(opened_at=T0, closed_at=T0 + timedelta(hours=2), llm_decision="APROBADA"),
        _trade(
            opened_at=T0 + timedelta(hours=1), closed_at=T0 + timedelta(hours=3),
            llm_decision="RECHAZADA",
        ),
    ]
    assert cluster_effective_counts(trades) == (1, 1)


def test_different_symbol_never_merges_even_if_overlapping():
    trades = [
        _trade(symbol="BTCUSDT", opened_at=T0, closed_at=T0 + timedelta(hours=2)),
        _trade(symbol="ETHUSDT", opened_at=T0, closed_at=T0 + timedelta(hours=2)),
    ]
    assert cluster_effective_counts(trades) == (2, 0)


def test_different_side_never_merges_even_if_overlapping():
    trades = [
        _trade(side="LONG", opened_at=T0, closed_at=T0 + timedelta(hours=2)),
        _trade(side="SHORT", opened_at=T0, closed_at=T0 + timedelta(hours=2)),
    ]
    assert cluster_effective_counts(trades) == (2, 0)


def test_empty_input_returns_zero_zero():
    assert cluster_effective_counts([]) == (0, 0)


# --- MeasurementStatus: shortfall / days_needed / porcentajes -------------------


def test_shortfall_is_zero_once_min_effective_n_is_reached():
    status = MeasurementStatus(frozen_hash=FROZEN, n_eff_approved=300, n_eff_rejected=10)
    assert status.shortfall("APROBADA") == 0
    assert status.shortfall("RECHAZADA") == 290


def test_days_needed_is_zero_when_shortfall_is_already_zero():
    status = MeasurementStatus(frozen_hash=FROZEN, n_eff_approved=300, rate_approved_per_day=5.0)
    assert status.days_needed("APROBADA") == 0.0


def test_days_needed_is_none_without_a_usable_rate():
    status = MeasurementStatus(frozen_hash=FROZEN, n_eff_rejected=10, rate_rejected_per_day=None)
    assert status.days_needed("RECHAZADA") is None

    status_zero_rate = MeasurementStatus(
        frozen_hash=FROZEN, n_eff_rejected=10, rate_rejected_per_day=0.0
    )
    assert status_zero_rate.days_needed("RECHAZADA") is None


def test_days_needed_divides_shortfall_by_rate():
    status = MeasurementStatus(frozen_hash=FROZEN, n_eff_approved=250, rate_approved_per_day=10.0)
    assert status.days_needed("APROBADA") == pytest.approx(5.0)  # (300-250)/10


def test_non_ok_pct_and_cost_per_row_are_none_without_any_rows():
    status = MeasurementStatus(frozen_hash=FROZEN)
    assert status.non_ok_pct("TIMEOUT") is None
    assert status.cost_per_row_usd() is None


def test_format_report_on_the_empty_state_never_raises():
    report = format_report(MeasurementStatus(frozen_hash=FROZEN))
    assert "Sin filas en llm_logs" in report


# --- compute_status: integracion contra una base real (sintetica), solo lectura --


@pytest.fixture
async def ro_conn(tmp_path):
    """Base SQLite de archivo real (no `:memory:`, que es privada por
    conexion) poblada con datos SINTETICOS via los repos de produccion
    (mismo esquema/migraciones que la base real), cerrada y reabierta en
    modo solo lectura -- igual que hace el script contra una copia real."""
    db_path = tmp_path / "synthetic_measurement.db"
    database = Database(str(db_path))
    await database.connect()
    yield database, db_path
    await database.close()


async def _seed_group(
    db, *, key, symbol="BTCUSDT", side=Side.LONG, opened_at, closed_at=None,
    candle_close_time=None, llm_decision=None, log=None,
):
    """Siembra una sombra y, opcionalmente, su fila en llm_logs y su cierre --
    mismo orden que el flujo real (`ShadowLiveLabeler` abre, `decide_group`
    logea y etiqueta, `PositionMonitor` cierra)."""
    trade = ShadowTrade(
        symbol=symbol, side=side, strategy="ema_cross_9_21", status=TradeStatus.OPEN,
        leverage=10, margin_usdt=5.0, notional_usdt=50.0, qty=1.0, entry_price=100.0,
        opened_at=opened_at, contributing_strategies=["ema_cross_9_21"], signal_group_key=key,
        candle_close_time=candle_close_time or opened_at,
    )
    trade_id = await shadow_repo.insert_if_new(db, trade)
    if closed_at is not None:
        await shadow_repo.close(
            db, trade_id, exit_price=100.0, fee_exit_usdt=0.0, slippage_exit_usdt=0.0,
            pnl_gross_usdt=0.0, pnl_net_usdt=0.0, close_reason="TP", fill_source="TICK",
            closed_at=closed_at,
        )
    if llm_decision is not None:
        await shadow_repo.set_llm_decision(db, trade_id, llm_decision)
    if log is not None:
        await llm_logs_repo.insert(
            db, signal_group_key=key, shadow_trade_id=trade_id,
            fase=log.get("fase", "MEDICION"), candle_close_time=candle_close_time or opened_at,
            decision_delay_s=5.0, hour_utc=opened_at.hour, atr_pct=None,
            model="claude-sonnet-5-5", prompt_version="signal_review_v4",
            prompt_sha256=log.get("prompt_sha256", FROZEN), prompt="p",
            response_raw=log.get("response_raw"), status=log.get("status", "OK"), error=None,
            decision=log.get("decision"), input_tokens=log.get("input_tokens", 100),
            output_tokens=log.get("output_tokens", 20), cost_usd=log.get("cost_usd", 0.001),
            latency_ms=log.get("latency_ms", 500), created_at=log.get("created_at", opened_at),
            json_repaired=log.get("json_repaired", False),
        )
    return trade_id


def _reopen_ro(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.asyncio
async def test_compute_status_on_an_empty_database_has_zero_rows(ro_conn):
    database, db_path = ro_conn
    await database.close()  # cierra la conexion de escritura antes de reabrir en RO

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.total_medicion_rows == 0
    assert status.n_eff_approved == 0 and status.n_eff_rejected == 0


@pytest.mark.asyncio
async def test_compute_status_counts_ok_rows_by_label_and_the_approval_band(ro_conn):
    database, db_path = ro_conn
    for i in range(6):
        await _seed_group(
            database, key=f"approved-{i}", opened_at=T0 + timedelta(hours=i),
            llm_decision="APROBADA",
            log={"decision": "APROBAR", "created_at": T0 + timedelta(hours=i)},
        )
    for i in range(4):
        await _seed_group(
            database, key=f"rejected-{i}", opened_at=T0 + timedelta(hours=10 + i),
            llm_decision="RECHAZADA",
            log={"decision": "RECHAZAR", "created_at": T0 + timedelta(hours=10 + i)},
        )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.ok_by_label == {"APROBADA": 6, "RECHAZADA": 4}
    assert status.total_ok == 10
    assert status.approval_rate == pytest.approx(0.6)
    assert status.within_band == (APPROVAL_BAND[0] <= 0.6 <= APPROVAL_BAND[1])


@pytest.mark.asyncio
async def test_compute_status_separates_non_ok_causes_and_their_percentage(ro_conn):
    database, db_path = ro_conn
    await _seed_group(
        database, key="ok-1", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0},
    )
    await _seed_group(
        database, key="timeout-1", opened_at=T0 + timedelta(hours=1),
        log={"status": "TIMEOUT", "decision": None, "created_at": T0 + timedelta(hours=1)},
    )
    await _seed_group(
        database, key="budget-1", opened_at=T0 + timedelta(hours=2),
        log={"status": "BUDGET_EXCEEDED", "decision": None,
             "created_at": T0 + timedelta(hours=2), "cost_usd": 0.0},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.total_medicion_rows == 3
    assert status.non_ok_counts["TIMEOUT"] == 1
    assert status.non_ok_counts["BUDGET_EXCEEDED"] == 1
    assert status.non_ok_counts["ERROR_HTTP"] == 0
    assert status.non_ok_pct("TIMEOUT") == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_compute_status_ignores_rows_under_a_different_prompt_hash(ro_conn):
    database, db_path = ro_conn
    await _seed_group(
        database, key="old-hash", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "prompt_sha256": "hash_viejo_distinto", "created_at": T0},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.total_medicion_rows == 0  # el hash viejo no cuenta para el congelado


@pytest.mark.asyncio
async def test_compute_status_counts_json_repaired_and_accumulated_cost(ro_conn):
    database, db_path = ro_conn
    await _seed_group(
        database, key="g1", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0, "cost_usd": 0.01, "json_repaired": True},
    )
    await _seed_group(
        database, key="g2", opened_at=T0 + timedelta(hours=1), llm_decision="RECHAZADA",
        log={"decision": "RECHAZAR", "created_at": T0 + timedelta(hours=1), "cost_usd": 0.02},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.json_repaired_count == 1
    assert status.total_cost_usd == pytest.approx(0.03)
    assert status.cost_per_row_usd() == pytest.approx(0.015)


@pytest.mark.asyncio
async def test_compute_status_counts_groups_omitted_without_any_log_row(ro_conn):
    """Punto 4: una sombra POSTERIOR a la primera fila MEDICION sin ninguna
    fila en llm_logs cuenta como omitida; una ANTERIOR (previa a la
    medicion) no cuenta -- mismo criterio que `measurement_keys`."""
    database, db_path = ro_conn
    await _seed_group(
        database, key="first-medicion-row", opened_at=T0, llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0},
    )
    # Anterior a la medicion, sin fila -- no deberia contarse como "omitida".
    await _seed_group(
        database, key="before-measurement", opened_at=T0 - timedelta(hours=5),
    )
    # Posterior a la primera fila MEDICION, nunca intentada -- SI cuenta.
    await _seed_group(
        database, key="omitted-1", opened_at=T0 + timedelta(hours=1),
    )
    await _seed_group(
        database, key="omitted-2", opened_at=T0 + timedelta(hours=2),
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.omitted_without_row == 2


@pytest.mark.asyncio
async def test_compute_status_effective_n_only_counts_closed_trades(ro_conn):
    database, db_path = ro_conn
    await _seed_group(
        database, key="closed-1", opened_at=T0, closed_at=T0 + timedelta(hours=1),
        llm_decision="APROBADA", log={"decision": "APROBAR", "created_at": T0},
    )
    # Abierta todavia (sin closed_at): no debe contar para N efectivo.
    await _seed_group(
        database, key="still-open", opened_at=T0 + timedelta(hours=5),
        llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": T0 + timedelta(hours=5)},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    assert status.n_eff_approved == 1
    assert status.total_ok == 2  # punto 1 SI cuenta la abierta, punto 7 no


@pytest.mark.asyncio
async def test_compute_status_rate_window_uses_the_most_recent_data_as_now(ro_conn):
    """El ritmo se ancla al dato MAS RECIENTE de la propia copia, no al reloj
    de pared -- una copia de hace dias no debe salir con ritmo cero solo por
    ser vieja."""
    database, db_path = ro_conn
    old_now = T0  # muy anterior al reloj de pared real
    await _seed_group(
        database, key="g1", opened_at=old_now, closed_at=old_now + timedelta(hours=1),
        llm_decision="APROBADA", log={"decision": "APROBAR", "created_at": old_now},
    )
    await _seed_group(
        database, key="g2", opened_at=old_now + timedelta(days=1),
        closed_at=old_now + timedelta(days=1, hours=1), llm_decision="APROBADA",
        log={"decision": "APROBAR", "created_at": old_now + timedelta(days=1)},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN, rate_window_days=7.0)
    finally:
        conn.close()

    assert status.data_now == old_now + timedelta(days=1)
    assert status.rate_approved_per_day is not None and status.rate_approved_per_day > 0


@pytest.mark.asyncio
async def test_format_report_with_real_data_never_raises_and_has_all_sections(ro_conn):
    database, db_path = ro_conn
    await _seed_group(
        database, key="g1", opened_at=T0, closed_at=T0 + timedelta(hours=1),
        llm_decision="APROBADA", log={"decision": "APROBAR", "created_at": T0},
    )
    await database.close()

    conn = _reopen_ro(db_path)
    try:
        status = compute_status(conn, frozen_hash=FROZEN)
    finally:
        conn.close()

    report = format_report(status)
    for heading in ("1) Grupos medidos", "2) Tasa de aprobacion", "3) Filas MEDICION no OK",
                    "4) Grupos omitidos", "5) Filas con JSON reparado", "6) Coste",
                    "7) Camino a MIN_EFFECTIVE_N"):
        assert heading in report
