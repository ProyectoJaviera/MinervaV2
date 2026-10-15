"""`scripts/ai_value_report.py`: CLI del veredicto, ejecutado como subproceso real
contra bases SINTETICAS (la consola de Windows usa cp1252 en un pipe: la prueba
cubre que el informe, con su delta, se pueda imprimir)."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from tests.unit.synthetic_measurement import (
    SYNTHETIC_HASH,
    create_schema,
    insert_groups,
    scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ai_value_report.py"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_cli.db"
    await create_schema(path)
    return path


async def test_cli_prints_the_verdict_for_a_synthetic_database_and_does_not_write(db_path):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    proc = run_cli(
        "--db", str(db_path), "--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Veredicto: APORTA_VALOR" in proc.stdout
    assert "δ = 3%" in proc.stdout
    assert "Desglose por estrategia" in proc.stdout
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


async def test_cli_refuses_with_the_gate_closed_and_prints_only_counters(db_path):
    insert_groups(db_path, scenario(299, 300, approved_ret=0.30))

    proc = run_cli(
        "--db", str(db_path), "--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH,
    )

    assert proc.returncode == 3, proc.stderr
    assert "GATE CERRADO" in proc.stdout
    assert "299/300" in proc.stdout
    for forbidden in ("Diferencia de esperanza", "IC 95", "Veredicto:", "APORTA_VALOR"):
        assert forbidden not in proc.stdout


async def test_cli_without_the_flag_ignores_a_synthetic_hash_and_stays_closed(db_path):
    # Sin la bandera se usa el hash congelado REAL: una base sintetica no tiene
    # ninguna fila con ese hash, asi que para el script no hay medicion.
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 3, proc.stderr
    assert "GATE CERRADO" in proc.stdout
    assert "Veredicto:" not in proc.stdout


async def test_cli_rejects_the_synthetic_flag_on_a_database_with_real_model_rows(db_path):
    groups = scenario(5, 5)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)

    proc = run_cli(
        "--db", str(db_path), "--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH,
    )

    assert proc.returncode == 4
    assert "RECHAZADO" in proc.stdout
    assert "Diferencia" not in proc.stdout


async def test_cli_rejects_frozen_hash_without_the_synthetic_flag(db_path):
    proc = run_cli("--db", str(db_path), "--frozen-hash", SYNTHETIC_HASH)

    assert proc.returncode == 2  # error de uso de argparse
    assert "--frozen-hash solo se acepta" in proc.stderr


def test_cli_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr
    assert "base de trabajo" in proc.stderr
