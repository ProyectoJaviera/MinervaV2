"""Fase 6: `scripts/backup_db.py` y `scripts/restore_db.py`, las herramientas de respaldo y de
mudanza del manual de operacion. Lo importante: el respaldo es CONSISTENTE con el bot escribiendo
(modo WAL), nunca toca el origen, nunca deja un respaldo a medias; y la restauracion se niega a
pisar nada sin `--force`, no borra lo anterior (lo aparta) y lo deja intacto si algo falla."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.persistence.database import Database
from scripts import backup_db, restore_db

REPO_ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)


def make_minerva_db(path: Path, rows: int = 3) -> None:
    """Una base con las tablas minimas de Minerva y `rows` filas en `trades`/`system_state`."""
    conn = sqlite3.connect(str(path))
    try:
        for table in restore_db.REQUIRED_TABLES:
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, valor TEXT)")
        for i in range(rows):
            conn.execute("INSERT INTO trades (valor) VALUES (?)", (f"fila-{i}",))
        conn.commit()
    finally:
        conn.close()


def count(path: Path, table: str = "trades") -> int:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table_names(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


# --- respaldo ------------------------------------------------------------------------------


def test_the_backup_includes_what_is_still_only_in_the_wal_and_leaves_the_source_alone(tmp_path):
    source = tmp_path / "bot.db"
    live = sqlite3.connect(str(source))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, valor TEXT)")
    live.executemany("INSERT INTO trades (valor) VALUES (?)", [(str(i),) for i in range(50)])
    live.commit()  # confirmado, pero todavia en el -wal (el bot sigue con la conexion abierta)
    try:
        assert (tmp_path / "bot.db-wal").exists()
        before = sha(source)

        code, message = backup_db.run(str(source), str(tmp_path / "resp"), "minerva", 0, T0)

        assert code == backup_db.EXIT_OK, message
        backup = tmp_path / "resp" / "minerva_20261018_120000.db"
        assert backup.exists() and count(backup) == 50  # nada se perdio por estar en el WAL
        assert sha(source) == before  # el origen no se modifico
        assert "Verificacion de integridad: ok" in message
    finally:
        live.close()


def test_the_backup_name_is_the_utc_timestamp(tmp_path):
    make_minerva_db(tmp_path / "bot.db")
    chile = datetime(2026, 10, 18, 9, 0, 0, tzinfo=timezone(timedelta(hours=-3)))  # 12:00 UTC

    code, _ = backup_db.run(str(tmp_path / "bot.db"), str(tmp_path / "r"), "mia", 0, chile)

    assert code == 0 and [p.name for p in (tmp_path / "r").iterdir()] == ["mia_20261018_120000.db"]


def test_a_missing_source_or_an_existing_backup_are_refused_without_side_effects(tmp_path):
    code, message = backup_db.run(str(tmp_path / "nada.db"), str(tmp_path / "r"), "minerva", 0, T0)
    assert code == backup_db.EXIT_NO_SOURCE and "No existe" in message
    assert not (tmp_path / "r").exists()  # ni siquiera crea la carpeta

    make_minerva_db(tmp_path / "bot.db")
    assert backup_db.run(str(tmp_path / "bot.db"), str(tmp_path / "r"), "minerva", 0, T0)[0] == 0
    first = sha(tmp_path / "r" / "minerva_20261018_120000.db")
    code, message = backup_db.run(str(tmp_path / "bot.db"), str(tmp_path / "r"), "minerva", 0, T0)
    assert code == backup_db.EXIT_EXISTS and "ya existe" in message
    assert sha(tmp_path / "r" / "minerva_20261018_120000.db") == first


def test_a_copy_that_fails_verification_is_not_kept_under_any_name(tmp_path, monkeypatch):
    make_minerva_db(tmp_path / "bot.db")
    monkeypatch.setattr(backup_db, "integrity_check", lambda path: "*** in database main ***")

    code, message = backup_db.run(str(tmp_path / "bot.db"), str(tmp_path / "r"), "minerva", 0, T0)

    assert code == backup_db.EXIT_BAD_COPY and "no paso la verificacion" in message
    assert list((tmp_path / "r").iterdir()) == []  # ni el definitivo ni el temporal


def test_a_source_that_is_not_a_database_fails_cleanly(tmp_path):
    (tmp_path / "basura.db").write_bytes(b"esto no es sqlite" * 100)

    code, message = backup_db.run(
        str(tmp_path / "basura.db"), str(tmp_path / "r"), "minerva", 0, T0,
    )

    assert code == backup_db.EXIT_BAD_COPY
    assert list((tmp_path / "r").iterdir()) == []
    assert "Traceback" not in message


def test_retention_keeps_the_newest_n_and_touches_nothing_else(tmp_path):
    make_minerva_db(tmp_path / "bot.db")
    out = tmp_path / "r"
    for hours in range(5):
        assert backup_db.run(str(tmp_path / "bot.db"), str(out), "minerva", 0,
                             T0 + timedelta(hours=hours))[0] == 0
    (out / "notas.txt").write_text("no me borres")
    (out / "otro_20200101_000000.db").write_bytes(b"de otro prefijo")
    (out / "minerva_manual.db").write_bytes(b"nombre que no sigue el patron")

    deleted = backup_db.prune(out, "minerva", keep=3)

    assert [p.name for p in deleted] == ["minerva_20261018_120000.db", "minerva_20261018_130000.db"]
    kept = sorted(p.name for p in out.iterdir())
    assert kept == [
        "minerva_20261018_140000.db", "minerva_20261018_150000.db", "minerva_20261018_160000.db",
        "minerva_manual.db", "notas.txt", "otro_20200101_000000.db",
    ]


def test_keep_zero_never_deletes_and_the_run_reports_what_it_deleted(tmp_path):
    make_minerva_db(tmp_path / "bot.db")
    out = tmp_path / "r"
    for hours in range(3):
        backup_db.run(str(tmp_path / "bot.db"), str(out), "minerva", 0, T0 + timedelta(hours=hours))
    assert backup_db.prune(out, "minerva", keep=0) == []
    assert len(list(out.iterdir())) == 3

    code, message = backup_db.run(str(tmp_path / "bot.db"), str(out), "minerva", 2,
                                  T0 + timedelta(hours=3))

    assert code == 0 and message.count("Borrado por antiguedad") == 2
    assert len(list(out.iterdir())) == 2


@pytest.mark.parametrize("args", [["--keep", "-1"], ["--prefix", "../fuera"], ["--prefix", ""]])
def test_the_cli_rejects_dangerous_or_invalid_arguments(args):
    with pytest.raises(SystemExit) as exc:
        backup_db.main(args)

    assert exc.value.code == 2


def test_the_backup_script_runs_standalone_with_only_the_standard_library(tmp_path):
    make_minerva_db(tmp_path / "bot.db")

    result = subprocess.run(
        [sys.executable, "-I", str(REPO_ROOT / "scripts" / "backup_db.py"),
         "--db", str(tmp_path / "bot.db"), "--out-dir", str(tmp_path / "r")],
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "Respaldo:" in result.stdout and len(list((tmp_path / "r").iterdir())) == 1


# --- restauracion --------------------------------------------------------------------------


def test_restore_installs_a_verified_copy_and_leaves_the_backup_untouched(tmp_path):
    make_minerva_db(tmp_path / "resp.db", rows=7)
    before = sha(tmp_path / "resp.db")

    code, message = restore_db.run(str(tmp_path / "resp.db"), str(tmp_path / "data" / "bot.db"))

    assert code == restore_db.EXIT_OK, message
    assert count(tmp_path / "data" / "bot.db") == 7 and sha(tmp_path / "resp.db") == before
    assert "frozen_hash_ok" in message  # recuerda la verificacion de despues de mudarse
    assert not list((tmp_path / "data").glob("*.restaurando"))  # sin temporales


@pytest.mark.parametrize("kind", ["sin_tablas", "no_sqlite", "vacio"])
def test_restore_rejects_what_is_not_a_minerva_database(tmp_path, kind):
    bad = tmp_path / "mal.db"
    if kind == "sin_tablas":
        conn = sqlite3.connect(str(bad))
        conn.execute("CREATE TABLE otra_cosa (x)")
        conn.commit()
        conn.close()
    elif kind == "no_sqlite":
        bad.write_bytes(b"texto que no es una base" * 50)
    else:
        bad.write_bytes(b"")

    code, message = restore_db.run(str(bad), str(tmp_path / "bot.db"))

    assert code == restore_db.EXIT_INVALID and "rechazado" in message
    assert not (tmp_path / "bot.db").exists()


def test_restore_refuses_to_overwrite_without_force(tmp_path):
    make_minerva_db(tmp_path / "resp.db", rows=9)
    make_minerva_db(tmp_path / "bot.db", rows=2)
    before = sha(tmp_path / "bot.db")

    code, message = restore_db.run(str(tmp_path / "resp.db"), str(tmp_path / "bot.db"))

    assert code == restore_db.EXIT_EXISTS and "--force" in message
    assert sha(tmp_path / "bot.db") == before and count(tmp_path / "bot.db") == 2


def test_force_moves_the_current_database_aside_with_its_wal_files_and_never_deletes_it(tmp_path):
    make_minerva_db(tmp_path / "resp.db", rows=9)
    make_minerva_db(tmp_path / "bot.db", rows=2)
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"bot.db{suffix}").write_bytes(b"restos del wal")

    code, message = restore_db.run(
        str(tmp_path / "resp.db"), str(tmp_path / "bot.db"), force=True, now=T0,
    )

    assert code == restore_db.EXIT_OK, message
    assert count(tmp_path / "bot.db") == 9  # el respaldo quedo instalado
    previous = tmp_path / "bot.db.previo_20261018_120000"
    assert count(previous) == 2  # y lo anterior sigue ahi, entero
    assert (tmp_path / "bot.db.previo_20261018_120000-wal").read_bytes() == b"restos del wal"
    assert not (tmp_path / "bot.db-wal").exists()  # el -wal viejo no se pega a la base nueva
    assert "Base anterior movida a" in message


def test_a_failure_while_installing_puts_the_previous_database_back(tmp_path, monkeypatch):
    make_minerva_db(tmp_path / "resp.db", rows=9)
    make_minerva_db(tmp_path / "bot.db", rows=2)
    before = sha(tmp_path / "bot.db")
    real_replace = os.replace

    def failing_replace(src, dst):
        if str(src).endswith(".restaurando"):
            raise PermissionError("archivo en uso")
        return real_replace(src, dst)

    monkeypatch.setattr(restore_db.os, "replace", failing_replace)

    code, message = restore_db.run(
        str(tmp_path / "resp.db"), str(tmp_path / "bot.db"), force=True, now=T0,
    )

    assert code == restore_db.EXIT_WRITE_FAILED and "quedo como estaba" in message
    assert sha(tmp_path / "bot.db") == before  # la base anterior volvio a su sitio
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bot.db", "resp.db"]  # sin restos


def test_restore_refuses_a_missing_backup_and_the_same_file(tmp_path):
    assert restore_db.run(str(tmp_path / "no.db"), str(tmp_path / "bot.db"))[0] == 3
    make_minerva_db(tmp_path / "bot.db")

    code, message = restore_db.run(str(tmp_path / "bot.db"), str(tmp_path / "bot.db"), force=True)

    assert code == restore_db.EXIT_USAGE and "mismo archivo" in message


def test_backup_then_restore_round_trips_a_real_minerva_schema(tmp_path):
    import asyncio

    async def build() -> None:
        db = Database(str(tmp_path / "real.db"))
        await db.connect()
        await db.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('k', 'v', 'ahora')"
        )
        await db.close()

    asyncio.run(build())
    assert backup_db.run(str(tmp_path / "real.db"), str(tmp_path / "r"), "minerva", 0, T0)[0] == 0
    backup = tmp_path / "r" / "minerva_20261018_120000.db"
    assert restore_db.validate_backup(backup) is None  # el respaldo pasa la validacion del restore

    code, message = restore_db.run(str(backup), str(tmp_path / "nuevo" / "bot.db"))

    assert code == 0, message
    assert count(tmp_path / "nuevo" / "bot.db", "system_state") == 1
    assert table_names(tmp_path / "nuevo" / "bot.db") == table_names(tmp_path / "real.db")


def test_the_restore_script_runs_standalone(tmp_path):
    make_minerva_db(tmp_path / "resp.db", rows=4)

    result = subprocess.run(
        [sys.executable, "-I", str(REPO_ROOT / "scripts" / "restore_db.py"),
         str(tmp_path / "resp.db"), "--db", str(tmp_path / "out" / "bot.db")],
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert count(tmp_path / "out" / "bot.db") == 4
