"""Fase 6: `scripts/measurement_identity.py`. El hash congelado cubre el prompt y las features pero
NO el modelo; esta herramienta cuenta (solo conteos y fechas) las filas de la medicion por modelo,
prompt, hash y features, y avisa si la medicion mezcla identidades o si el modelo configurado ahora
es otro que el medido. Todo con datos sinteticos."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.persistence.database import Database
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256
from scripts import measurement_identity as script

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "claude-sonnet-5-5"


def make_db(path: Path, rows: list[dict]) -> Path:
    async def create() -> None:
        db = Database(str(path))
        await db.connect()
        await db.close()

    asyncio.run(create())
    conn = sqlite3.connect(str(path))
    try:
        for number, row in enumerate(rows):
            values = {
                "created_at": f"2026-10-{10 + number:02d}T10:00:00+00:00",
                "signal_group_key": f"grupo-{number}", "fase": "MEDICION",
                "candle_close_time": "2026-10-10T10:00:00+00:00", "decision_delay_s": 20.0,
                "hour_utc": 10, "model": MODEL, "prompt_version": "signal_review_v4",
                "prompt_sha256": FROZEN_PROMPT_SHA256, "prompt": "{}", "status": "OK",
                "decision": "APROBAR", "features_version": "features_v2", **row,
            }
            conn.execute(
                f"INSERT INTO llm_logs ({', '.join(values)}) "
                f"VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def groups_of(path: Path) -> list[script.Group]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return script.load_groups(conn)
    finally:
        conn.close()


def test_a_single_model_with_the_frozen_prompt_is_ok(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {}, {}])

    code, lines = script.assess(groups_of(db), MODEL)

    assert code == script.EXIT_OK
    assert any("OK: un solo modelo" in line for line in lines)
    assert any("3 filas" in line and "coincide con el congelado" in line for line in lines)


def test_two_models_in_the_measurement_are_an_anomaly(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {}, {"model": "otro-modelo"}])

    code, lines = script.assess(groups_of(db), MODEL)

    assert code == script.EXIT_ANOMALY
    text = "\n".join(lines)
    assert "mas de una combinacion" in text
    assert MODEL in text and "otro-modelo" in text  # se ve CUAL y cuantas filas cada uno
    assert "2 filas" in text and "1 filas" in text


def test_a_row_that_is_not_the_frozen_prompt_is_an_anomaly(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {"prompt_sha256": "0" * 64}])

    code, lines = script.assess(groups_of(db), MODEL)

    assert code == script.EXIT_ANOMALY
    assert "no usan el prompt congelado" in "\n".join(lines)


def test_a_different_features_version_is_a_second_combination(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {"features_version": "features_v3"}])

    code, _ = script.assess(groups_of(db), MODEL)

    assert code == script.EXIT_ANOMALY


def test_a_configured_model_that_differs_from_the_measured_one_is_a_pending_change(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {}])

    code, lines = script.assess(groups_of(db), "modelo-nuevo")

    assert code == script.EXIT_ANOMALY
    text = "\n".join(lines)
    assert "modelo-nuevo" in text and "al reiniciar" in text


def test_pilot_rows_are_not_part_of_the_measurement(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {"fase": "PILOTO", "model": "modelo-del-piloto"}])

    groups = groups_of(db)
    code, _ = script.assess(groups, MODEL)

    assert [g.model for g in groups] == [MODEL]
    assert code == script.EXIT_OK


def test_without_measurement_rows_there_is_nothing_to_check(tmp_path):
    db = make_db(tmp_path / "copia.db", [{"fase": "PILOTO"}])

    code, lines = script.assess(groups_of(db), MODEL)

    assert code == script.EXIT_OK
    assert any("Todavia no hay filas de MEDICION" in line for line in lines)


def test_null_features_version_is_shown_not_hidden(tmp_path):
    db = make_db(tmp_path / "copia.db", [{"features_version": None}])

    groups = groups_of(db)

    assert groups[0].features_version == script.NO_DATA


def test_the_report_has_counts_and_dates_only(tmp_path):
    db = make_db(tmp_path / "copia.db", [{"decision": "RECHAZAR", "cost_usd": 0.0123}, {}])

    _, lines = script.assess(groups_of(db), MODEL)

    text = "\n".join(lines).lower()
    for forbidden in ("aprobar", "rechazar", "0.0123", "cost", "pnl", "tasa"):
        assert forbidden not in text


def run_cli(db: Path, **env: str) -> subprocess.CompletedProcess:
    environment = {
        **os.environ, "ANTHROPIC_SONNET_MODEL": MODEL,
        "DATABASE_PATH": str(db.parent / "otra_base_de_trabajo.db"), **env,
    }
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "measurement_identity.py"),
         "--db", str(db)],
        capture_output=True, text=True, timeout=120, cwd=db.parent, env=environment,
    )


def test_the_cli_exits_zero_on_a_clean_copy_even_from_another_directory(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {}])

    result = run_cli(db)

    assert result.returncode == 0, result.stderr
    assert "OK: un solo modelo" in result.stdout


def test_the_cli_exits_one_on_an_anomaly(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {"model": "otro-modelo"}])

    result = run_cli(db)

    assert result.returncode == 1
    assert "ANOMALIA" in result.stdout


def test_the_cli_reports_a_missing_database_with_code_three(tmp_path):
    result = run_cli(tmp_path / "no_existe.db")

    assert result.returncode == 3
    assert "No existe la base" in result.stderr


def test_the_cli_refuses_the_working_database(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}])

    result = run_cli(db, DATABASE_PATH=str(db))

    assert result.returncode != 0
    assert "no puede ser la base de trabajo" in result.stderr


def test_the_cli_never_writes_the_database(tmp_path):
    db = make_db(tmp_path / "copia.db", [{}, {}])
    before = db.read_bytes()

    result = run_cli(db)

    assert result.returncode == 0
    assert db.read_bytes() == before


def test_main_rejects_a_missing_required_argument():
    with pytest.raises(SystemExit) as exc:
        script.main([])

    assert exc.value.code == 2
