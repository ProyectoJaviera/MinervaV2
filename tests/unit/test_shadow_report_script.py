"""`scripts/shadow_report.py`: ya no imprime PnL ni el criterio de valor de la IA
mientras el gate de la medicion este cerrado."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from scripts import shadow_report
from tests.unit.synthetic_measurement import create_schema, insert_groups, scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "shadow_report.py"


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_shadow_report.db"
    await create_schema(path)
    insert_groups(path, scenario(5, 5, approved_ret=0.3))
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )


async def test_cli_refuses_to_print_with_the_gate_closed(db_path):
    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 3, proc.stderr
    assert "REPORTE NO MOSTRADO" in proc.stdout
    for forbidden in ("Total de operaciones sombra", "PnL neto total", "Criterio de valor"):
        assert forbidden not in proc.stdout


async def test_main_prints_the_report_when_the_gate_is_open(db_path, monkeypatch, capsys):
    monkeypatch.setattr(shadow_report, "is_measurement_gate_open", lambda status: True)

    code = await shadow_report.main(str(db_path))

    assert code == 0
    assert "Total de operaciones sombra" in capsys.readouterr().out


def test_cli_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    # Si el archivo no existe falla antes con "No existe"; si existe, con el assert.
    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr or "No existe" in proc.stderr
