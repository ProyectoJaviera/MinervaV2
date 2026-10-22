"""`scripts/news_status.py`: estado de las noticias sobre una COPIA, como subproceso real contra una
base SINTETICA. Solo lectura, sin PnL, con la atribucion de cada fuente y negandose a abrir la base
de trabajo."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import settings
from app.persistence.database import Database
from app.persistence.repositories import health_repo, news_repo
from app.persistence.repositories.news_repo import FetchState, NewsRecord
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "news_status.py"
NOW = datetime.now(UTC)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120, creationflags=NO_WINDOW,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "news_status.db"
    database = Database(str(path))
    await database.connect()
    await database.close()
    return path


async def seed(path):
    database = Database(str(path))
    await database.connect()
    try:
        def rec(n, **kw):
            fields = dict(
                source="gdelt", fetched_at=NOW - timedelta(minutes=n), title=f"Titular {n}",
                url=f"https://example.com/{n}", url_hash=f"u{n}", title_hash=f"t{n}",
                published_at=NOW - timedelta(minutes=n + 5), domain="example.com",
                assets=["BTC"], sentiment=0.5, sentiment_method="LEXICON_V1",
            )
            fields.update(kw)
            return NewsRecord(**fields)

        await news_repo.insert_item(database, rec(1))
        await news_repo.insert_item(database, rec(2, assets=[], sentiment=-0.6))
        await news_repo.insert_item(
            database, rec(3, source="fed_monetary", domain="federalreserve.gov")
        )
        await news_repo.insert_item(database, rec(4, dup_of=1))
        await news_repo.insert_item(database, rec(5, fetched_at=NOW - timedelta(days=3)))
        await health_repo.record_success(database, "news:gdelt", NOW)
        await health_repo.record_failure(database, "news:fed_monetary", "FetchError: HTTP 500")
        await news_repo.save_state(database, FetchState(
            source="gdelt", next_allowed_at=NOW + timedelta(hours=1), last_status=200,
        ))
        await news_repo.upsert_fear_greed(
            database, ts=NOW - timedelta(hours=3), value=55, classification="Greed", fetched_at=NOW,
        )
    finally:
        await database.close()


async def test_reports_sources_counts_fear_greed_headlines_and_attribution(db_path):
    await seed(db_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    proc = run_cli("--db", str(db_path), "--last", "3")

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "- gdelt: ultimo exito" in out and "fallos seguidos 0" in out
    assert "- fed_monetary: ultimo exito nunca, fallos seguidos 1" in out
    assert "ultimo error: FetchError: HTTP 500" in out
    assert "- fear_greed: sin actividad registrada" in out
    assert "Titulares guardados: 4 (mas 1 casi duplicados marcados)" in out
    assert "gdelt 2" in out and "fed_monetary 1" in out  # ultimas 24 h (sin el de hace 3 dias)
    assert "Fear & Greed vigente: 55 (Greed)" in out
    assert "Ultimos 3 titulares (texto de terceros, no confiable):" in out
    assert "[BTC] POSITIVE: Titular 1" in out and "[-] NEGATIVE: Titular 2" in out
    for attribution in (
        "GDELT Project", "Federal Reserve", "alternative.me",
    ):
        assert attribution in out.split("Atribuciones:")[1]
    assert "alternative.me" in out.split("Fear & Greed vigente")[1].splitlines()[0]  # junto al dato
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before  # solo lectura


async def test_never_prints_any_trade_result(db_path):
    await seed(db_path)

    out = run_cli("--db", str(db_path)).stdout.lower()

    for forbidden in ("pnl", "ganancia", "perdida", "winrate", "exit_price", "close_reason"):
        assert forbidden not in out


async def test_an_empty_database_reports_zeros_without_failing(db_path):
    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 0, proc.stderr
    assert "Titulares guardados: 0" in proc.stdout
    assert "sin actividad registrada" in proc.stdout
    assert "Fear & Greed vigente: sin datos" in proc.stdout


async def test_a_copy_that_predates_phase_4_says_so(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()

    proc = run_cli("--db", str(path))

    assert proc.returncode == 0, proc.stderr
    assert "esta copia es anterior a la Fase 4" in proc.stdout


def test_the_script_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr and "base de trabajo" in proc.stderr
