"""`scripts/placebo_check.py`: CLI como subproceso real contra bases SINTETICAS."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from tests.unit.synthetic_measurement import (
    BASE,
    SYNTHETIC_HASH,
    Group,
    create_schema,
    insert_groups,
    scenario,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "placebo_check.py"
FLAGS = ("--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=300,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_placebo_cli.db"
    await create_schema(path)
    return path


async def test_cli_prints_the_result_and_the_record_block_without_writing(db_path):
    insert_groups(db_path, scenario(310, 310, approved_ret=0.0, rejected_ret=0.0))
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    proc = run_cli("--db", str(db_path), "--n-repeats", "3", *FLAGS)

    assert proc.returncode == 0, proc.stderr
    assert "Calibrado (frecuencia < 5%): SI" in proc.stdout
    assert "δ=3%" in proc.stdout  # se puede imprimir por un pipe cp1252
    assert "Para copiar al registro de docs/FASE3_6_LLM.md" in proc.stdout
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


async def test_cli_out_saves_utf8_once_and_never_overwrites(db_path, tmp_path):
    insert_groups(db_path, scenario(310, 310, approved_ret=0.0, rejected_ret=0.0))
    out = tmp_path / "placebo.txt"

    first = run_cli("--db", str(db_path), "--n-repeats", "2", *FLAGS, "--out", str(out))
    saved = out.read_text(encoding="utf-8")
    second = run_cli("--db", str(db_path), "--n-repeats", "2", *FLAGS, "--out", str(out))

    assert first.returncode == 0, first.stderr
    assert "δ=3%" in saved and "Para copiar al registro" in saved
    assert second.returncode == 5 and "no sobrescribe" in second.stdout
    assert out.read_text(encoding="utf-8") == saved


async def test_cli_with_nothing_to_calibrate_exits_3_and_writes_no_record(db_path, tmp_path):
    insert_groups(db_path, [Group(key="a", opened_at=BASE, label="SIN_LLM")])
    out = tmp_path / "placebo.txt"

    proc = run_cli("--db", str(db_path), "--n-repeats", "2", *FLAGS, "--out", str(out))

    assert proc.returncode == 3, proc.stderr
    assert "nada que calibrar" in proc.stdout
    assert not out.exists()


async def test_cli_without_the_flag_uses_the_real_hash_and_sees_no_measurement(db_path):
    insert_groups(db_path, scenario(310, 310))

    proc = run_cli("--db", str(db_path), "--n-repeats", "2")

    assert proc.returncode == 3, proc.stderr
    assert "nada que calibrar" in proc.stdout


async def test_cli_rejects_the_synthetic_flag_on_a_database_with_real_model_rows(db_path):
    groups = scenario(5, 5)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)

    proc = run_cli("--db", str(db_path), "--n-repeats", "2", *FLAGS)

    assert proc.returncode == 4
    assert "RECHAZADO" in proc.stdout


async def test_cli_usage_errors(db_path):
    no_flag = run_cli("--db", str(db_path), "--frozen-hash", SYNTHETIC_HASH)
    zero = run_cli("--db", str(db_path), "--n-repeats", "0")

    assert no_flag.returncode == 2 and "--frozen-hash solo se acepta" in no_flag.stderr
    assert zero.returncode == 2 and "--n-repeats" in zero.stderr


def test_cli_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr or "No existe la base" in proc.stderr
