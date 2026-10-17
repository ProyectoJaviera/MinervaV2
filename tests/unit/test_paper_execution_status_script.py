"""`scripts/paper_execution_status.py`: estado del brazo ejecutado sobre una COPIA, ejecutado
como subproceso contra una base SINTETICA. Solo lectura, sin PnL, y se niega a abrir la base de
trabajo."""

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
from app.persistence.repositories import paper_execution_repo as repo

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "paper_execution_status.py"
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )


@pytest.fixture
async def db_path(tmp_path):
    path = tmp_path / "arm_status.db"
    database = Database(str(path))
    await database.connect()
    await database.close()
    return path


async def seed(path):
    database = Database(str(path))
    await database.connect()
    try:
        async def claim(key, symbol, side="LONG", strategy="ema_cross_9_21", delay=25.0, minute=0):
            await repo.claim(
                database, signal_group_key=key, shadow_trade_id=1, symbol=symbol, side=side,
                strategy=strategy, decision_delay_s=delay,
                created_at=T0 + timedelta(minutes=minute),
            )

        await claim("a", "BTCUSDT", minute=0)
        await repo.resolve_opened(database, "a", trade_id=11, details={
            "entry_delay_s": 31.0, "adverse_gap_bps": 4.0, "shadow_entry_price": 100.0,
        })
        await claim("b", "ETHUSDT", minute=1, delay=22.0)
        await repo.resolve_opened(database, "b", trade_id=12, details={
            "entry_delay_s": 45.0, "adverse_gap_bps": -2.0,
        })
        await claim("c", "TRXUSDT", minute=2)
        await repo.resolve_skipped(database, "c", reason="BELOW_MIN_ORDER", details={"qty": 148.9})
        await claim("d", "SOLUSDT", strategy="donchian_breakout_20", minute=3)
        await repo.resolve_skipped(database, "d", reason="RISK_STRATEGY_NOT_ELIGIBLE")
        await claim("e", "XRPUSDT", minute=4, delay=74.1)
        await repo.resolve_skipped(database, "e", reason="LATE_DECISION")
        await claim("f", "TRXUSDT", minute=5)
        await repo.resolve_skipped(database, "f", reason="BELOW_MIN_ORDER")
        await claim("g", "LINKUSDT", minute=6)  # PENDING
        await database.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES "
            "('real_arm_started_at', ?, ?), ('real_arm_drawdown_baseline', '2', ?)",
            (T0.isoformat(), T0.isoformat(), T0.isoformat()),
        )
    finally:
        await database.close()


async def test_reports_counts_reasons_entry_quality_and_the_latest_decisions(db_path):
    await seed(db_path)
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()

    proc = run_cli("--db", str(db_path), "--last", "3")

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "Armado desde: 2026-11-01 12:00:00 UTC" in out
    assert "Linea base del contador de drawdown: 2" in out
    assert "Decisiones APROBADA consideradas: 7" in out
    assert "- Abiertas (OPENED): 2" in out and "- Omitidas (SKIPPED): 4" in out
    assert "- Pendientes (PENDING): 1" in out
    assert "- BELOW_MIN_ORDER: 2" in out
    assert "- RISK_STRATEGY_NOT_ELIGIBLE: 1" in out and "- LATE_DECISION: 1" in out
    assert "mediana 38.0 s" in out  # (31 + 45) / 2
    assert "mediana 1.0" in out  # (4 - 2) / 2 bps
    assert "- TRXUSDT: 2" in out  # no ejecutable por tamano minimo
    assert "Ultimas 3 decisiones:" in out
    last_block = out.split("Ultimas 3 decisiones:")[1]
    assert last_block.count("\n- ") == 3 and "BTCUSDT" not in last_block  # solo las 3 ultimas
    # de la mas reciente a la mas antigua: LINK (pendiente), TRX (2.a), XRP
    assert last_block.index("LINKUSDT") < last_block.index("TRXUSDT") < last_block.index("XRPUSDT")
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before  # no escribe


async def test_never_prints_any_pnl_or_result(db_path):
    await seed(db_path)

    out = run_cli("--db", str(db_path)).stdout.lower()

    for forbidden in ("pnl", "ganancia", "perdida", "exit_price", "close_reason"):
        assert forbidden not in out


async def test_a_copy_that_predates_the_arm_says_so(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.commit()
    conn.close()

    proc = run_cli("--db", str(path))

    assert proc.returncode == 0, proc.stderr
    assert "esta copia es anterior a la 3.9" in proc.stdout
    assert "Armado desde: nunca" in proc.stdout


async def test_an_empty_log_reports_zero_without_failing(db_path):
    proc = run_cli("--db", str(db_path))

    assert proc.returncode == 0, proc.stderr
    assert "Decisiones APROBADA consideradas: 0" in proc.stdout
    assert "Armado desde: nunca" in proc.stdout


def test_the_script_refuses_the_working_database_path():
    proc = run_cli("--db", settings.database_path)

    assert proc.returncode != 0
    assert "AssertionError" in proc.stderr and "base de trabajo" in proc.stderr
