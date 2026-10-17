"""Ritmo de la medicion sobre tiempo EN MARCHA (ademas del de calendario): el hueco de
mas de `OFFLINE_GAP_HOURS` sin ciclos cuenta como apagado. Cubre la funcion pura con
huecos sinteticos, `compute_status` (sync y async) sobre una base sintetica con el
esquema real, y los cuatro consumidores: CLI, API, `/metrics` y resumen diario. Sin PnL."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.database import Database
from app.telegram.commands import CommandRouter
from app.trading.measurement_status import (
    OFFLINE_GAP_HOURS,
    MeasurementStatus,
    compute_status,
    compute_status_async,
    eval_marks_lower_bound,
    pace_lines,
    parse_eval_marks,
    running_seconds,
)
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256
from scripts.measurement_status import format_report
from scripts.telegram_daily import build_message, read_snapshot
from tests.unit.synthetic_measurement import (
    BASE,
    SYNTHETIC_HASH,
    create_schema,
    insert_eval_marks,
    insert_groups,
    open_read_only,
    scenario,
)
from tests.unit.test_readonly_api_routes import make_authenticated_client
from tests.unit.test_telegram_commands import make_ctx

H = timedelta(hours=1)
T0 = datetime(2026, 11, 1, tzinfo=UTC)
FORBIDDEN_PNL = ("pnl", "Diferencia de esperanza", "APORTA_VALOR", "IC 95")


def hourly(start: datetime, end: datetime) -> list[datetime]:
    out, t = [], start
    while t <= end:
        out.append(t)
        t += H
    return out


# --- running_seconds: la funcion pura con huecos sinteticos ------------------------------


def test_continuous_hourly_marks_are_entirely_running():
    marks = hourly(T0, T0 + 10 * H)

    assert running_seconds(marks, T0, T0 + 10 * H) == 10 * 3600


def test_a_gap_over_the_threshold_counts_entirely_as_off():
    # 0-10 h evaluando, 58 h de hueco, 68-78 h evaluando (el hueco real del 15-17 de octubre)
    marks = hourly(T0, T0 + 10 * H) + hourly(T0 + 68 * H, T0 + 78 * H)

    assert running_seconds(marks, T0, T0 + 78 * H) == 20 * 3600


def test_a_gap_of_exactly_the_threshold_still_counts_as_running_but_a_minute_more_does_not():
    gap = timedelta(hours=OFFLINE_GAP_HOURS)
    at_limit = [T0, T0 + gap]
    just_over = [T0, T0 + gap + timedelta(minutes=1)]

    assert running_seconds(at_limit, T0, T0 + gap) == gap.total_seconds()
    assert running_seconds(just_over, T0, T0 + gap + timedelta(minutes=1)) == 0.0


def test_the_window_clips_the_segments_on_both_sides():
    marks = hourly(T0, T0 + 10 * H)

    assert running_seconds(marks, T0 + 2.5 * H, T0 + 7.25 * H) == pytest.approx(4.75 * 3600)


def test_the_mark_before_the_window_keeps_the_leading_segment_running():
    # la ultima marca previa a la ventana esta a 1 h del inicio: el tramo inicial sigue en marcha
    marks = [T0, T0 + 1.5 * H]

    assert running_seconds(marks, T0 + 1 * H, T0 + 1.5 * H) == pytest.approx(0.5 * 3600)
    # ...pero con la marca previa a mas del umbral (5 h de hueco), todo el tramo cuenta apagado
    assert running_seconds([T0, T0 + 5 * H], T0 + 3 * H, T0 + 5 * H) == 0.0


def test_the_window_end_counts_as_a_mark_so_a_running_bot_is_not_cut_short():
    marks = [T0, T0 + H]

    assert running_seconds(marks, T0, T0 + 1.5 * H) == pytest.approx(1.5 * 3600)  # fin a 30 min
    assert running_seconds(marks, T0, T0 + 4 * H) == pytest.approx(1 * 3600)  # fin a 3 h: apagado


def test_marks_after_the_window_end_are_ignored():
    marks = hourly(T0, T0 + 20 * H)

    assert running_seconds(marks, T0, T0 + 5 * H) == 5 * 3600


def test_no_marks_or_an_empty_window_means_zero_and_naive_datetimes_are_accepted():
    assert running_seconds([], T0, T0 + H) == 0.0
    assert running_seconds([T0], T0, T0) == 0.0
    assert running_seconds([T0], T0 + H, T0) == 0.0
    naive = [T0.replace(tzinfo=None), (T0 + H).replace(tzinfo=None)]
    assert running_seconds(naive, T0.replace(tzinfo=None), T0 + H) == 3600


def test_duplicate_marks_from_one_cycle_burst_add_nothing():
    marks = [T0, T0, T0 + H, T0 + H]

    assert running_seconds(marks, T0, T0 + H) == 3600


def test_eval_marks_are_minute_resolution_utc():
    parsed = parse_eval_marks([{"m": "2026-11-01T03:07"}, {"m": None}])

    assert parsed == [datetime(2026, 11, 1, 3, 7, tzinfo=UTC)]
    assert eval_marks_lower_bound(datetime(2026, 11, 1, 3, 7, 30, tzinfo=UTC)) == "2026-11-01T01:07"


# --- compute_status sobre una base sintetica con el esquema real ------------------------------

# 12 aprobadas (horas 0,10,..,110) y 12 rechazadas (2,12,..,112): la medicion dura 112 h.
# Marcas horarias de 0 a 40 h, 40 h apagado, y de 80 a 113 h => 72 h en marcha de 112 h.
RUNNING_H = 72
CALENDAR_H = 112


def gap_marks() -> list[datetime]:
    return hourly(BASE, BASE + 40 * H) + hourly(BASE + 80 * H, BASE + 113 * H)


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_uptime.db"
    await create_schema(path)
    return path


def seed(path, marks, *, per_mark=1):
    insert_groups(path, scenario(12, 12, approved_ret=0.3))
    if marks:
        insert_eval_marks(path, marks, per_mark=per_mark)


async def test_running_rate_excludes_the_offline_gap_and_is_higher_than_the_calendar_rate(db_path):
    seed(db_path, gap_marks(), per_mark=3)
    conn = open_read_only(db_path)
    try:
        status = compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()

    assert (status.n_eff_approved, status.n_eff_rejected) == (12, 12)
    assert status.window_days == pytest.approx(CALENDAR_H / 24)
    assert status.running_window_days == pytest.approx(RUNNING_H / 24)
    assert status.uptime_pct_window == pytest.approx(RUNNING_H / CALENDAR_H)
    assert status.uptime_pct_total == pytest.approx(RUNNING_H / CALENDAR_H)
    assert status.rate_approved_per_day == pytest.approx(12 / (CALENDAR_H / 24))
    assert status.rate_approved_running_per_day == pytest.approx(12 / (RUNNING_H / 24))
    assert status.rate_approved_running_per_day > status.rate_approved_per_day
    assert status.days_needed_running("APROBADA") < status.days_needed("APROBADA")
    assert status.days_needed_running("APROBADA") == pytest.approx(288 / 4.0)  # 288 faltan, 4/dia


async def test_without_a_gap_both_rates_are_the_same(db_path):
    seed(db_path, hourly(BASE, BASE + 113 * H))
    conn = open_read_only(db_path)
    try:
        status = compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()

    assert status.uptime_pct_window == pytest.approx(1.0)
    assert status.rate_approved_running_per_day == pytest.approx(status.rate_approved_per_day)
    assert status.days_needed_running("RECHAZADA") == pytest.approx(status.days_needed("RECHAZADA"))


async def test_a_database_without_evaluation_marks_has_no_running_rate_not_zero_percent(db_path):
    seed(db_path, [])
    conn = open_read_only(db_path)
    try:
        status = compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()

    assert status.running_window_days is None
    assert status.uptime_pct_window is None and status.uptime_pct_total is None
    assert status.rate_approved_running_per_day is None
    assert status.days_needed_running("APROBADA") is None
    assert status.rate_approved_per_day is not None  # el de calendario sigue intacto
    assert "sin marcas de evaluacion" in "\n".join(pace_lines(status))


async def test_marks_older_than_the_measurement_minus_the_threshold_are_not_read(db_path):
    # una marca MUY anterior a la medicion no cambia nada: ya habria un hueco > umbral
    seed(db_path, [BASE - 300 * H, *hourly(BASE + 1 * H, BASE + 113 * H)])
    conn = open_read_only(db_path)
    try:
        status = compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()

    # el tramo [inicio de la medicion, BASE+1h] queda como apagado
    assert status.running_window_days == pytest.approx((CALENDAR_H - 1) / 24, abs=1e-3)


async def test_sync_and_async_variants_agree(db_path):
    seed(db_path, gap_marks(), per_mark=2)
    conn = open_read_only(db_path)
    try:
        sync_status = compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()
    database = Database(str(db_path))
    await database.connect()
    try:
        async_status = await compute_status_async(database, frozen_hash=SYNTHETIC_HASH)
    finally:
        await database.close()

    assert async_status == sync_status


async def test_the_status_queries_the_signal_marks_without_touching_any_result_column(db_path):
    seed(db_path, gap_marks())
    seen: list[str] = []
    conn = open_read_only(db_path)
    conn.set_trace_callback(seen.append)
    try:
        compute_status(conn, frozen_hash=SYNTHETIC_HASH)
    finally:
        conn.close()

    signal_queries = [s.lower() for s in seen if "from signals" in s.lower()]
    assert signal_queries and all("evaluated_at" in s for s in signal_queries)
    for sql in seen:
        for forbidden in ("pnl", "exit_price", "close_reason"):
            assert forbidden not in sql.lower(), sql


# --- textos: script, API, /metrics y resumen diario ----------------------------------------------


def labeled_status() -> MeasurementStatus:
    return MeasurementStatus(
        frozen_hash="h", total_medicion_rows=24, n_eff_approved=12, n_eff_rejected=12,
        first_medicion_row_at=BASE, data_now=BASE + 112 * H,
        window_days=4.67, rate_approved_per_day=2.4, rate_rejected_per_day=2.4,
        running_window_days=3.0, uptime_pct_window=0.64, uptime_pct_total=0.64,
        rate_approved_running_per_day=4.0, rate_rejected_running_per_day=4.0,
    )


def test_pace_lines_label_both_rates_and_show_the_uptime_percentage():
    text = "\n".join(pace_lines(labeled_status()))

    assert "Ritmo por CALENDARIO" in text and "INCLUYE el tiempo con el bot apagado" in text
    assert "Ritmo EN MARCHA" in text and "mas de 2 h sin ciclos cuenta como apagado" in text
    assert "APROBADA 2.40/dia -> ~120.0 dias de calendario" in text  # 288 / 2.4
    assert "APROBADA 4.00/dia en marcha -> ~72.0 dias EN MARCHA" in text  # 288 / 4.0
    assert (
        "Tiempo en marcha: 64% de los ultimos 4.7 dias (3.0 dias en marcha); 64% desde el inicio"
        in text
    )
    assert "equivalen a dias de calendario solo si el bot no vuelve a apagarse" in text
    assert "aviso" not in text


def test_pace_lines_warn_when_there_is_less_than_a_day_running():
    status = MeasurementStatus(
        frozen_hash="h", total_medicion_rows=2, window_days=3.2, rate_approved_per_day=0.9,
        running_window_days=0.8, uptime_pct_window=0.25, uptime_pct_total=0.62,
        rate_approved_running_per_day=3.7,
    )

    assert "menos de 1 dia en marcha" in "\n".join(pace_lines(status))


def test_pace_lines_say_no_data_instead_of_zero_when_a_rate_is_missing():
    status = MeasurementStatus(
        frozen_hash="h", total_medicion_rows=2, window_days=1.0, running_window_days=0.0,
        uptime_pct_window=0.0, uptime_pct_total=0.0,
    )

    text = "\n".join(pace_lines(status))
    assert "APROBADA n/d -> sin datos para estimar" in text
    assert "n/d/dia" not in text


def test_the_cli_report_shows_both_rates():
    report = format_report(labeled_status())

    assert "Ritmo por CALENDARIO" in report and "Ritmo EN MARCHA" in report
    assert "Se muestran DOS ritmos" in report


async def test_the_api_returns_both_rates_and_the_uptime(db_path):
    seed(db_path, gap_marks())
    database = Database(str(db_path))
    await database.connect()
    try:
        # la API usa el hash congelado real: la base sintetica solo prueba la forma de la respuesta
        client = make_authenticated_client(database)
        body = client.get("/api/measurement/status").json()
    finally:
        await database.close()

    assert body["offline_gap_hours"] == OFFLINE_GAP_HOURS
    for key in (
        "running_window_days", "uptime_pct_window", "uptime_pct_total",
        "rate_approved_running_per_day", "rate_rejected_running_per_day",
        "days_needed_running_approved", "days_needed_running_rejected",
    ):
        assert key in body, key
    assert "days_needed_approved" in body and "rate_approved_per_day" in body  # calendario intacto


async def test_the_api_fields_carry_the_numbers_for_the_frozen_hash(db_path):
    seed(db_path, gap_marks())
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE llm_logs SET prompt_sha256 = ?", (FROZEN_PROMPT_SHA256,))
    conn.commit()
    conn.close()
    database = Database(str(db_path))
    await database.connect()
    try:
        body = make_authenticated_client(database).get("/api/measurement/status").json()
    finally:
        await database.close()

    assert body["uptime_pct_window"] == pytest.approx(RUNNING_H / CALENDAR_H)
    assert body["running_window_days"] == pytest.approx(RUNNING_H / 24)
    assert body["days_needed_running_approved"] == pytest.approx(288 / 4.0)
    assert body["days_needed_approved"] > body["days_needed_running_approved"]


async def test_metrics_in_telegram_show_both_rates_without_any_pnl(db_path):
    seed(db_path, gap_marks())
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE llm_logs SET prompt_sha256 = ?", (FROZEN_PROMPT_SHA256,))
    conn.commit()
    conn.close()
    database = Database(str(db_path))
    await database.connect()
    try:
        ctx, _ = make_ctx(database)
        text = await CommandRouter(ctx)("/metrics")
    finally:
        await database.close()

    assert "Ritmo por CALENDARIO" in text and "Ritmo EN MARCHA" in text
    assert "Tiempo en marcha: 64% " in text
    assert "N efectivo APROBADA: 12/300 (faltan 288)" in text
    for forbidden in FORBIDDEN_PNL:
        assert forbidden not in text


async def test_the_daily_summary_shows_both_rates_without_any_pnl(db_path):
    seed(db_path, gap_marks())
    conn = open_read_only(db_path)
    try:
        snapshot = read_snapshot(conn, BASE + 113 * H, SYNTHETIC_HASH)
    finally:
        conn.close()

    message = build_message(
        health=None, health_error="sin bot", snapshot=snapshot, db_error=None,
        now=BASE + 113 * H,
    )

    assert "Ritmo por CALENDARIO" in message and "Ritmo EN MARCHA" in message
    assert "Tiempo en marcha: 64% " in message
    for forbidden in FORBIDDEN_PNL:
        assert forbidden not in message
