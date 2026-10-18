"""El etiquetador en vivo no rellena `llm_logs.atr_pct`; el informe del veredicto (seccion m,
SIN_LLM por tercil de ATR) lo recupera del mensaje guardado en `llm_logs.prompt`. Solo lectura:
no cambia la medicion, el prompt ni lo que se escribe. Datos sinteticos con el esquema real."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import timedelta

import pytest

from app.llm.prompts import SYSTEM_PROMPT, build_user_message
from app.persistence.llm_log_fields import atr_from_prompt, atr_value
from app.trading import ai_value_report
from app.trading.ai_value_report import (
    STATE_VERDICT,
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


def prompt_with(atr, extra=None) -> str:
    """Un prompt con el MISMO formato que guarda `LlmDecisionService`: sistema, dos saltos de
    linea y el mensaje como JSON de una sola linea."""
    features = {"symbol": "BTCUSDT", "side": "LONG", "volatility_atr14_pct": atr, **(extra or {})}
    return f"{SYSTEM_PROMPT}\n\n{build_user_message(features)}"


# --- el helper puro ----------------------------------------------------------------------------


@pytest.mark.parametrize(("atr", "expected"), [
    (0.93, 0.93), (2, 2.0), (0, 0.0), (None, None), (-0.5, None), (True, None), ("alto", None),
    (float("nan"), None), (float("inf"), None), ([1.0], None),
])
def test_the_atr_is_read_from_a_real_format_prompt(atr, expected):
    assert atr_from_prompt(prompt_with(atr)) == expected


@pytest.mark.parametrize("prompt", [
    None, "", "p", "sin mensaje", "SISTEMA\n\n{roto", "SISTEMA\n\n[1, 2]", "SISTEMA\n\n{}",
    "SISTEMA\n\n{\"otra_clave\": 1.0}", "\n\n{",
])
def test_unreadable_or_incomplete_prompts_give_none_and_never_raise(prompt):
    assert atr_from_prompt(prompt) is None


def test_the_system_prompt_and_other_braces_do_not_confuse_the_parser():
    system = 'SISTEMA con {llaves} y "comillas"\nuna linea\n\nparrafo {aparte}'
    message = json.dumps({"a": "texto con \n salto", "volatility_atr14_pct": 1.234,
                          "anidado": {"volatility_atr14_pct": 99.0}}, sort_keys=True)

    assert atr_from_prompt(f"{system}\n\n{message}") == pytest.approx(1.234)


def test_a_headline_inside_the_message_cannot_inject_a_different_atr():
    """Un texto de terceros dentro del mensaje queda como valor de cadena escapado: no puede abrir
    una clave nueva ni un segundo separador."""
    hostile = 'x\n\n{"volatility_atr14_pct": 99.0}'
    prompt = prompt_with(0.5, {"news": hostile})

    assert atr_from_prompt(prompt) == pytest.approx(0.5)


def test_the_column_wins_over_the_prompt_and_the_prompt_fills_a_null_column():
    assert atr_value(1.7, prompt_with(0.5)) == 1.7
    assert atr_value(0.0, prompt_with(0.5)) == 0.0  # un cero real no es "falta"
    assert atr_value(None, prompt_with(0.5)) == pytest.approx(0.5)
    assert atr_value(None, None) is None
    assert math.isfinite(atr_value(None, prompt_with(3)))


# --- el informe del veredicto -------------------------------------------------------------------


@pytest.fixture
def small_n(monkeypatch):
    monkeypatch.setattr("app.trading.measurement_status.MIN_EFFECTIVE_N", SMALL_N)
    monkeypatch.setattr("app.trading.ai_value_report.MIN_EFFECTIVE_N", SMALL_N)
    return SMALL_N


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "atr_report.db"
    await create_schema(path)
    return path


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def varied_groups(small_n: int) -> list[Group]:
    """Medicion limpia mas 12 fallas de SIN_LLM concentradas en ATR bajo y en la franja 00-03."""
    groups = scenario(small_n, small_n, approved_ret=0.30)
    for i, group in enumerate(groups):
        group.atr_pct = 0.5 + (i % 9) * 0.2  # 0.5 ... 2.1
    failures = [
        Group(key=f"f{i}", opened_at=BASE + timedelta(days=200 + i), label="SIN_LLM", ret=None,
              status="TIMEOUT", hour_utc=2, atr_pct=0.3 + i * 0.01)
        for i in range(12)
    ]
    return groups + failures


def move_the_atr_into_the_prompt(path) -> None:
    """Reproduce lo que hace el etiquetador en vivo: la columna `atr_pct` queda nula y el valor
    solo esta en el mensaje guardado en `prompt`."""
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT id, atr_pct FROM llm_logs").fetchall()
        for row_id, atr in rows:
            features = {"symbol": "AAAUSDT", "volatility_atr14_pct": atr}
            conn.execute(
                "UPDATE llm_logs SET atr_pct = NULL, prompt = ? WHERE id = ?",
                (f"{SYSTEM_PROMPT}\n\n{build_user_message(features)}", row_id),
            )
        conn.commit()
    finally:
        conn.close()


def run_report(path):
    conn = open_read_only(path)
    try:
        return build_report(conn, frozen_hash=SYNTHETIC_HASH, allow_synthetic_for_tests=True)
    finally:
        conn.close()


async def test_without_the_fix_the_atr_strata_would_be_empty_with_it_they_are_not(
    db_path, small_n,
):
    insert_groups(db_path, varied_groups(small_n))
    move_the_atr_into_the_prompt(db_path)
    conn = open_read_only(db_path)
    try:
        assert conn.execute("SELECT COUNT(atr_pct) FROM llm_logs").fetchone()[0] == 0
        metas = ai_value_report._load_log_metas(conn)
    finally:
        conn.close()

    assert metas and all(m.atr_pct is not None for m in metas)
    diagnostics = compute_sin_llm_diagnostics(metas)
    assert {s.label for s in diagnostics.by_atr_tercile} >= {"bajo", "medio", "alto"}
    # las 12 fallas tienen el ATR mas bajo: caen todas en el tercil "bajo"
    assert {s.label: s for s in diagnostics.by_atr_tercile}["bajo"].sin_llm_share == (
        pytest.approx(1.0))


async def test_the_whole_report_is_identical_whether_the_atr_is_in_the_column_or_in_the_prompt(
    tmp_path, small_n,
):
    column_db = tmp_path / "column.db"
    prompt_db = tmp_path / "prompt.db"
    for path in (column_db, prompt_db):
        await create_schema(path)
        insert_groups(path, varied_groups(small_n))
    move_the_atr_into_the_prompt(prompt_db)

    from_column = run_report(column_db)
    from_prompt = run_report(prompt_db)

    assert from_column.state == from_prompt.state == STATE_VERDICT
    assert render_report(from_column) == render_report(from_prompt)
    assert "tercil de atr_pct" in render_report(from_prompt)
    assert from_column.diagnostics == from_prompt.diagnostics
    assert from_column.verdict == from_prompt.verdict  # el veredicto no depende del ATR


async def test_loading_the_metas_is_read_only_and_does_not_fetch_prompts_it_does_not_need(
    db_path, small_n,
):
    insert_groups(db_path, varied_groups(small_n))
    before = sha(db_path)
    seen: list[str] = []
    conn = open_read_only(db_path)
    conn.set_trace_callback(seen.append)
    try:
        ai_value_report._load_log_metas(conn)  # conexion `mode=ro`: escribir fallaria
    finally:
        conn.close()

    assert sha(db_path) == before
    [select] = [s for s in seen if "FROM llm_logs" in s]
    assert "CASE WHEN atr_pct IS NULL THEN prompt END" in select  # sin traer prompts de mas


async def test_rows_with_a_value_in_the_column_keep_it_even_if_the_prompt_disagrees(
    db_path, small_n,
):
    insert_groups(db_path, scenario(small_n, small_n))
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE llm_logs SET atr_pct = 1.5, prompt = ?",
            (f"{SYSTEM_PROMPT}\n\n{build_user_message({'volatility_atr14_pct': 9.9})}",),
        )
        conn.commit()
    finally:
        conn.close()
    ro = open_read_only(db_path)
    try:
        metas = ai_value_report._load_log_metas(ro)
    finally:
        ro.close()

    assert {m.atr_pct for m in metas} == {1.5}
