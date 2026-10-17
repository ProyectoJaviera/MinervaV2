"""`scripts/real_money_report.py`: CLI del reporte de paso a dinero real, ejecutado como
subproceso real contra bases SINTETICAS. Cubre el codigo de salida de cada camino, que la
copia no cambie, que `--out` no pueda fabricar el registro oficial y que ni el script ni el
modulo toquen la ejecucion real."""

from __future__ import annotations

import ast
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.trading.verdict_record import find_verdict_record
from tests.unit.synthetic_measurement import (
    SYNTHETIC_HASH,
    create_schema,
    insert_groups,
    scenario,
)
from tests.unit.test_real_money_report import (
    REPORT_TEXT,
    insert_trades,
    pattern_trades,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "real_money_report.py"
FLAGS = ("--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_cli.db"
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


async def test_gate_closed_prints_only_what_is_missing_and_counters(db_path, docs, tmp_path):
    insert_groups(db_path, scenario(299, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")
    out = tmp_path / "informe.txt"
    before = sha(db_path)

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs), "--out", str(out))

    assert proc.returncode == 3, proc.stderr
    assert "NO SE MUESTRA NINGUN RESULTADO" in proc.stdout
    assert "299/300" in proc.stdout
    for forbidden in ("Criterios del brazo", "IC 95", "RECOMENDACION", "Veredicto oficial"):
        assert forbidden not in proc.stdout
    assert not out.exists()  # lo de "falta algo" nunca se guarda como informe
    assert sha(db_path) == before


async def test_gate_open_without_the_official_record_says_the_record_is_missing(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 3, proc.stderr
    assert "no existe el registro oficial" in proc.stdout
    assert "Criterios del brazo" not in proc.stdout


async def test_a_verdict_other_than_aporta_valor_concludes_no_and_saves_the_report_once(
    db_path, docs, tmp_path,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "INCONCLUSO")
    out_dir = tmp_path / "salida"
    out_dir.mkdir()
    out = out_dir / "PASO_DINERO_REAL_2026-12-05.txt"

    first = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs), "--out", str(out))
    saved = out.read_text(encoding="utf-8")
    second = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs), "--out", str(out))

    assert first.returncode == 0, first.stderr
    assert "NO PASAR A DINERO REAL" in first.stdout and "NO PASAR A DINERO REAL" in saved
    assert "Criterios del brazo ejecutado" not in first.stdout
    assert second.returncode == 5 and "ya existe" in second.stdout
    assert out.read_text(encoding="utf-8") == saved  # no se sobrescribe
    # el informe guardado NO es (ni puede confundirse con) el registro oficial del veredicto
    assert find_verdict_record(out_dir) is None


async def test_aporta_valor_evaluates_the_executed_arm_and_never_enables_anything(
    db_path, docs,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    insert_trades(db_path, pattern_trades(100, 3))
    write_record(docs, "APORTA_VALOR")
    before = sha(db_path)

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 0, proc.stderr
    for expected in (
        "Criterios del brazo ejecutado", "[PASA] PF > 1.3", "[SIN DATOS]", "Riesgos operativos",
        "RECOMENDACION: NO PASAR A DINERO REAL todavia", "v1 es 100 % paper",
    ):
        assert expected in proc.stdout, expected
    assert sha(db_path) == before


async def test_log_file_adds_the_critical_reconciliation_lines(db_path, docs, tmp_path):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    insert_trades(db_path, pattern_trades(100, 3))
    write_record(docs, "APORTA_VALOR")
    log = tmp_path / "bot.log"
    log.write_text("2099-01-01 00:00:00 CRITICAL Reconciliacion fallida de X\n", encoding="utf-8")

    proc = run_cli(
        "--db", str(db_path), *FLAGS, "--docs-dir", str(docs), "--log-file", str(log),
    )

    assert proc.returncode == 0, proc.stderr
    assert "[NO PASA] sin errores criticos de reconciliacion" in proc.stdout
    assert "1 lineas CRITICAL en el log" in proc.stdout


async def test_out_cannot_use_the_name_of_the_official_record(db_path, docs, tmp_path):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "INCONCLUSO")
    forged = tmp_path / "VEREDICTO_3_6_REGISTRO_2026-12-06.txt"

    proc = run_cli(
        "--db", str(db_path), *FLAGS, "--docs-dir", str(docs), "--out", str(forged),
    )

    assert proc.returncode == 2
    assert "no puede llamarse como el registro oficial" in proc.stderr
    assert not forged.exists()


async def test_test_only_flags_are_rejected_without_the_synthetic_flag(db_path, docs):
    frozen = run_cli("--db", str(db_path), "--frozen-hash", SYNTHETIC_HASH)
    docs_dir = run_cli("--db", str(db_path), "--docs-dir", str(docs))

    assert frozen.returncode == 2 and "--frozen-hash solo se acepta" in frozen.stderr
    assert docs_dir.returncode == 2 and "--docs-dir solo se acepta" in docs_dir.stderr


async def test_a_missing_log_file_is_a_usage_error(db_path, tmp_path):
    proc = run_cli("--db", str(db_path), "--log-file", str(tmp_path / "no_existe.log"))

    assert proc.returncode == 2 and "--log-file no existe" in proc.stderr


async def test_the_synthetic_flag_is_rejected_on_a_database_with_real_model_rows(db_path):
    groups = scenario(5, 5)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)

    proc = run_cli("--db", str(db_path), *FLAGS)

    assert proc.returncode == 4
    assert "RECHAZADO" in proc.stdout and "Criterios" not in proc.stdout


async def test_without_the_flag_a_synthetic_database_has_no_measurement_and_stays_closed(
    db_path,
):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 3, proc.stderr
    assert "NO SE MUESTRA NINGUN RESULTADO" in proc.stdout


def test_the_script_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr and "base de trabajo" in proc.stderr


# --- v1 es 100 % paper: ni el script ni el modulo tocan la ejecucion real ----------------------


def _direct_imports(path: Path) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def test_the_script_does_not_import_any_execution_backend_or_network_client():
    forbidden = ("bitunix", "paper_backend", "backend_base", "httpx", "requests", "aiohttp",
                 "websockets", "urllib", "socket", "anthropic")

    offenders = [m for m in _direct_imports(SCRIPT) if any(w in m.lower() for w in forbidden)]

    assert offenders == []


def test_no_execution_backend_nor_llm_client_is_loaded_even_transitively():
    """El cliente PUBLICO de mercado (`app.market.bitunix_rest`) y la matematica pura de
    PnL (`app.execution.pnl`) entran de rebote, pero ningun backend de ejecucion ni el
    cliente del LLM."""
    code = (
        "import sys; sys.path.insert(0, '.'); import scripts.real_money_report; "
        "bad = [m for m in sys.modules if m.startswith(('anthropic', "
        "'app.execution.bitunix_backend', 'app.execution.paper_backend', "
        "'app.execution.backend_base'))]; print(bad)"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
