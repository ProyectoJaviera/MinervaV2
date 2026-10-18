"""`scripts/lessons_report.py`: informe de la memoria de lecciones, ejecutado como subproceso real
contra bases SINTETICAS. Lo que importa: sin la puerta (gate de la medicion + registro oficial del
veredicto) NO se muestra ningun resultado y la base ni siquiera se abre para escribir; con la puerta
abierta construye la memoria en la copia; y el script no toca la ejecucion real ni el LLM."""

from __future__ import annotations

import ast
import hashlib
import sqlite3
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
from tests.unit.test_real_money_report import REPORT_TEXT

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lessons_report.py"
FLAGS = ("--allow-synthetic-for-tests", "--frozen-hash", SYNTHETIC_HASH)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=180,
    )


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count(path: Path, table: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "synthetic_lessons.db"
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


async def test_gate_closed_hides_every_result_and_never_writes_to_the_database(db_path, docs):
    insert_groups(db_path, scenario(299, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")  # aunque exista el registro, el gate esta cerrado
    before = sha(db_path)

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 3, proc.stderr
    assert "Operaciones cerradas en la base: 599" in proc.stdout
    assert "ocultos hasta el registro oficial" in proc.stdout
    assert "299/300" in proc.stdout
    for forbidden in ("Tasa de acierto base", "Lecciones ACTIVE", "Entradas de memoria",
                      "post-mortem", "Calibracion"):
        assert forbidden not in proc.stdout
    assert sha(db_path) == before  # ni un byte: no se abrio para escribir
    assert count(db_path, "memory_entries") == 0 and count(db_path, "lessons") == 0


async def test_gate_open_without_the_official_record_still_hides_the_results(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    before = sha(db_path)

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 3, proc.stderr
    assert "no existe el registro oficial" in proc.stdout
    assert "Tasa de acierto base" not in proc.stdout and "Lecciones ACTIVE" not in proc.stdout
    assert "CERRADO" not in proc.stdout  # el gate si esta abierto: solo falta el registro
    assert sha(db_path) == before


async def test_an_unparseable_record_is_treated_as_missing(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    (docs / "VEREDICTO_3_6_REGISTRO_2026-12-05.txt").write_text("sin veredicto", encoding="utf-8")

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 3 and "Lecciones ACTIVE" not in proc.stdout


async def test_with_the_gate_open_and_the_record_it_builds_the_memory_in_the_copy(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "APORTA_VALOR")

    proc = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert proc.returncode == 0, proc.stderr
    for expected in (
        "=== Memoria de lecciones (Fase 4) ===",
        "Entradas de memoria (operaciones cerradas fiables): 600 (nuevas ahora: 600)",
        "Tasa de acierto base:", "## Lecciones ACTIVE", "## Ultimo post-mortem",
        "## Calibracion de la confianza del LLM", "no garantiza resultados",
    ):
        assert expected in proc.stdout, expected
    assert count(db_path, "memory_entries") == 600
    assert count(db_path, "lessons") > 0


async def test_running_it_twice_does_not_duplicate_the_memory(db_path, docs):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))
    write_record(docs, "INCONCLUSO")  # cualquier veredicto oficial abre la puerta de lectura

    first = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))
    second = run_cli("--db", str(db_path), *FLAGS, "--docs-dir", str(docs))

    assert first.returncode == 0 and second.returncode == 0, second.stderr
    assert "(nuevas ahora: 0)" in second.stdout
    assert count(db_path, "memory_entries") == 600


async def test_the_synthetic_flag_is_rejected_on_a_database_with_real_model_rows(db_path):
    groups = scenario(5, 5)
    groups[0].model = "claude-sonnet-5-5"
    insert_groups(db_path, groups)
    before = sha(db_path)

    proc = run_cli("--db", str(db_path), *FLAGS)

    assert proc.returncode == 4
    assert "RECHAZADO" in proc.stdout and "Lecciones ACTIVE" not in proc.stdout
    assert sha(db_path) == before


async def test_without_the_flag_a_synthetic_database_has_no_measurement_and_stays_closed(db_path):
    insert_groups(db_path, scenario(300, 300, approved_ret=0.30))

    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 3, proc.stderr
    assert "Lecciones ACTIVE" not in proc.stdout


async def test_test_only_flags_are_rejected_without_the_synthetic_flag(db_path, docs):
    frozen = run_cli("--db", str(db_path), "--frozen-hash", SYNTHETIC_HASH)
    docs_dir = run_cli("--db", str(db_path), "--docs-dir", str(docs))

    assert frozen.returncode == 2 and "--frozen-hash solo se acepta" in frozen.stderr
    assert docs_dir.returncode == 2 and "--docs-dir solo se acepta" in docs_dir.stderr


def test_the_script_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr and "base de trabajo" in proc.stderr


# --- el script no toca la ejecucion real ni el LLM ----------------------------------------------


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
                 "websockets", "urllib", "socket", "anthropic", "app.news", "scheduler")

    offenders = [m for m in _direct_imports(SCRIPT) if any(w in m.lower() for w in forbidden)]

    assert offenders == []


def test_no_execution_backend_llm_client_nor_news_network_code_is_loaded_even_transitively():
    """`app.news.assets` (funcion pura `base_ticker`) entra de rebote desde la memoria; lo que NO
    puede cargarse es el cliente HTTP / colector / ejecutor de noticias, el LLM ni un backend."""
    code = (
        "import sys; sys.path.insert(0, '.'); import scripts.lessons_report; "
        "bad = [m for m in sys.modules if m.startswith(('anthropic', "
        "'app.execution.bitunix_backend', 'app.execution.paper_backend', "
        "'app.execution.backend_base', 'app.news.fetch', 'app.news.collector', "
        "'app.news.runtime', 'app.memory.runtime'))]; print(bad)"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"
