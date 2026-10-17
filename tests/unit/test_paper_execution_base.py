"""Subfase 3.9, base del brazo ejecutado de la cuenta PAPER: interruptor apagado por defecto
(solo `start_bot.ps1` lo activa y solo con el puerto 8000), tabla `paper_execution_log`
aditiva e idempotente, y su repositorio (reclamo idempotente, resolucion, conteos)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.persistence.database import Database
from app.persistence.repositories import paper_execution_repo as repo
from tests.unit.synthetic_measurement import insert_groups, scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


# --- interruptor -------------------------------------------------------------------------------


def test_the_switch_is_off_by_default_and_follows_the_env_var():
    assert Settings(_env_file=None).paper_execution_enabled is False  # type: ignore[call-arg]
    on = Settings(_env_file=None, PAPER_EXECUTION_ENABLED=True)  # type: ignore[call-arg]
    assert on.paper_execution_enabled is True


def test_it_is_a_separate_switch_from_auto_open_without_llm():
    settings = Settings(_env_file=None, PAPER_EXECUTION_ENABLED=True)  # type: ignore[call-arg]

    assert settings.auto_open_without_llm is False  # activar el brazo no abre "sin LLM"


def test_start_bot_activates_the_arm_only_for_port_8000():
    text = (REPO_ROOT / "scripts" / "start_bot.ps1").read_text(encoding="utf-8")

    assert '$isRealBot = ($Port -eq 8000)' in text
    assert '$paperExecution = if ($isRealBot) { "true" } else { "false" }' in text
    assert '$env:PAPER_EXECUTION_ENABLED = $paperExecution' in text
    assert "PAPER_EXECUTION_ENABLED=$paperExecution" in text  # queda en el diario operativo
    # nunca se fija a true sin pasar por la condicion del puerto
    assert 'PAPER_EXECUTION_ENABLED = "true"' not in text


def test_the_dev_server_and_the_env_example_keep_it_off():
    dev = (REPO_ROOT / "scripts" / "run_dev_server.py").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert 'os.environ["PAPER_EXECUTION_ENABLED"] = "false"' in dev
    assert "PAPER_EXECUTION_ENABLED=false" in example.splitlines()


# --- tabla y repositorio ------------------------------------------------------------------


@pytest.fixture
async def file_db(tmp_path):
    database = Database(str(tmp_path / "paper_exec.db"))
    await database.connect()
    yield database, tmp_path / "paper_exec.db"
    await database.close()


async def claim(db, key="BTCUSDT|LONG|c1", **kw):
    fields = dict(
        signal_group_key=key, shadow_trade_id=7, symbol="BTCUSDT", side="LONG",
        strategy="ema_cross_9_21", decision_delay_s=21.0, created_at=T0,
    )
    fields.update(kw)
    return await repo.claim(db, **fields)


async def test_claim_is_idempotent_per_signal_group(db):
    assert await claim(db) is True
    assert await claim(db) is False  # el mismo grupo nunca se reclama dos veces
    assert await claim(db, key="BTCUSDT|LONG|c2") is True

    pending = await repo.get_pending(db)
    assert [e.signal_group_key for e in pending] == ["BTCUSDT|LONG|c1", "BTCUSDT|LONG|c2"]
    assert all(e.outcome == "PENDING" and e.reason is None for e in pending)


async def test_resolve_opened_links_the_trade_and_clears_the_reason(db):
    await claim(db)

    await repo.resolve_opened(
        db, "BTCUSDT|LONG|c1", trade_id=5, details={"entry_delay_s": 24.5}, resolved_at=T0,
    )

    entry = await repo.get_by_key(db, "BTCUSDT|LONG|c1")
    assert entry.outcome == "OPENED" and entry.trade_id == 5 and entry.reason is None
    assert entry.details == {"entry_delay_s": 24.5} and entry.resolved_at == T0
    assert await repo.get_pending(db) == []


async def test_resolve_skipped_keeps_a_queryable_reason_and_details(db):
    await claim(db, key="TRXUSDT|LONG|c1", symbol="TRXUSDT")

    await repo.resolve_skipped(
        db, "TRXUSDT|LONG|c1", reason="BELOW_MIN_ORDER",
        details={"qty": 148.9, "min_trade_volume": 150.0},
    )

    entry = await repo.get_by_key(db, "TRXUSDT|LONG|c1")
    assert entry.outcome == "SKIPPED" and entry.reason == "BELOW_MIN_ORDER"
    assert entry.details["min_trade_volume"] == 150.0 and entry.trade_id is None
    assert await repo.count_by_reason(db) == {"BELOW_MIN_ORDER": 1}


async def test_counts_by_outcome_since_a_moment_and_by_reason(db):
    await claim(db, key="a", created_at=T0 - timedelta(days=2))
    await claim(db, key="b", created_at=T0)
    await claim(db, key="c", created_at=T0 + timedelta(hours=1))
    await claim(db, key="d", created_at=T0 + timedelta(hours=2))
    await repo.resolve_opened(db, "b", trade_id=1, details={})
    await repo.resolve_skipped(db, "c", reason="LATE_DECISION")
    await repo.resolve_skipped(db, "a", reason="RISK_KILL_SWITCH_ACTIVE")

    since_today = await repo.count_outcomes_since(db, T0)
    assert since_today == {"OPENED": 1, "SKIPPED": 1, "PENDING": 1}
    assert await repo.count_by_reason(db) == {"LATE_DECISION": 1, "RISK_KILL_SWITCH_ACTIVE": 1}
    assert [e.signal_group_key for e in await repo.get_recent(db, limit=2)] == ["d", "c"]


async def test_the_table_only_accepts_valid_sides_and_outcomes(db):
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO paper_execution_log (signal_group_key, created_at, symbol, side, "
            "decision_delay_s, outcome) VALUES ('x', 'y', 'BTCUSDT', 'ARRIBA', 1.0, 'PENDING')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await db.execute(
            "INSERT INTO paper_execution_log (signal_group_key, created_at, symbol, side, "
            "decision_delay_s, outcome) VALUES ('x', 'y', 'BTCUSDT', 'LONG', 1.0, 'HECHO')"
        )


async def test_the_table_stores_no_result_columns(db):
    rows = await db.fetch_all("PRAGMA table_info(paper_execution_log)")
    columns = {r["name"] for r in rows}

    assert not any("pnl" in c or "exit" in c or "close" in c for c in columns), columns
    assert columns == {
        "id", "signal_group_key", "created_at", "resolved_at", "shadow_trade_id", "symbol", "side",
        "strategy", "decision_delay_s", "outcome", "reason", "details_json", "trade_id",
    }


# --- migracion aditiva sobre una base ya existente ----------------------------------------


def _row_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        names = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {n: conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}
    finally:
        conn.close()


REAL_DB = REPO_ROOT / "data" / "minerva_estabilidad.db"


@pytest.mark.skipif(not REAL_DB.exists(), reason="no hay copia local de la base real")
async def test_the_migration_on_a_copy_of_the_real_database_changes_nothing_else(tmp_path):
    """Smoke sobre datos reales: la base real se abre en SOLO LECTURA y se copia con la API de
    backup a `tmp_path`; solo la copia se migra (nunca la de `data/`)."""
    copy = tmp_path / "real_copy.db"
    assert copy.resolve() != REAL_DB.resolve()
    source = sqlite3.connect(f"file:{REAL_DB.as_posix()}?mode=ro", uri=True)
    destination = sqlite3.connect(str(copy))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    before = _row_counts(copy)

    for _ in range(2):  # el segundo arranque debe ser inocuo
        database = Database(str(copy))
        await database.connect()
        await database.close()

    after = _row_counts(copy)
    created = set(after) - set(before)
    # solo tablas NUEVAS y vacias (esta copia es previa a la 3.9 y a la Fase 4)
    assert "paper_execution_log" in created or "paper_execution_log" in before
    assert all(after[t] == 0 for t in created)
    assert after.get("paper_execution_log", 0) == 0
    assert {k: v for k, v in after.items() if k in before} == before
    conn = sqlite3.connect(str(copy))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


async def test_connecting_to_a_database_without_the_table_adds_only_that_table(tmp_path):
    path = tmp_path / "old_shape.db"
    database = Database(str(path))
    await database.connect()
    await database.close()
    insert_groups(path, scenario(5, 5, approved_ret=0.2))  # llm_logs + shadow_trades con datos
    conn = sqlite3.connect(str(path))
    conn.execute("DROP TABLE paper_execution_log")  # una base de ANTES de la 3.9
    conn.commit()
    conn.close()
    before = _row_counts(path)
    assert "paper_execution_log" not in before

    reopened = Database(str(path))
    await reopened.connect()
    await reopened.close()
    again = Database(str(path))  # idempotente: un segundo arranque no cambia nada
    await again.connect()
    await again.close()

    after = _row_counts(path)
    assert set(after) - set(before) == {"paper_execution_log"}
    assert after.pop("paper_execution_log") == 0
    assert after == before  # ninguna otra tabla cambio
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
