"""Fase 7: tras la mudanza a Docker existe la marca `data/MIGRADO_A_DOCKER.txt` y la base nativa
es una copia VIEJA. Las herramientas que la usarian POR DEFECTO se niegan a tocarla en silencio
(darian por respaldado o por arreglado algo que no es la base viva), y el resumen diario saca su
copia del contenedor. Pasar `--db` explicito esquiva la proteccion a proposito."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.persistence.database import Database
from scripts import backup_db, restore_db, resume_drawdown_stop, telegram_daily
from scripts.docker_support import DockerError
from tests.support.fakes import FakeDocker, make_native_db, table_counts

T0 = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    """Un directorio de trabajo con `data/` y la marca de la mudanza (como tras migrar)."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "MIGRADO_A_DOCKER.txt").write_text("migrado", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def not_migrated(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- backup_db -------------------------------------------------------------------------------


def test_backup_refuses_the_default_database_after_the_migration(migrated, capsys):
    make_native_db(migrated / "data" / "minerva_estabilidad.db")

    code = backup_db.main([])

    assert code == backup_db.EXIT_USAGE
    error = capsys.readouterr().err
    assert "corre en Docker" in error and "python scripts/docker_db.py backup" in error
    assert not (migrated / "data" / "backups").exists()  # no hizo ningun respaldo "falso"


def test_backup_with_an_explicit_database_is_not_refused(migrated):
    other = make_native_db(migrated / "otra.db")

    code = backup_db.main(["--db", str(other), "--out-dir", str(migrated / "r")])

    assert code == 0 and len(list((migrated / "r").glob("minerva_*.db"))) == 1


def test_backup_works_as_always_when_there_was_no_migration(not_migrated):
    make_native_db(not_migrated / "data" / "minerva_estabilidad.db")

    code = backup_db.main(["--out-dir", str(not_migrated / "data" / "backups")])

    assert code == 0


# --- restore_db ------------------------------------------------------------------------------


def test_restore_refuses_the_default_destination_after_the_migration(migrated, capsys):
    backup = make_native_db(migrated / "respaldo.db")

    code = restore_db.main([str(backup)])

    assert code == restore_db.EXIT_USAGE
    error = capsys.readouterr().err
    assert "no cambia la base del bot" in error and "rollback_from_docker.ps1" in error
    assert not (migrated / "data" / "minerva_estabilidad.db").exists()


def test_restore_with_an_explicit_destination_is_not_refused(migrated):
    backup = make_native_db(migrated / "respaldo.db")
    destination = migrated / "data" / "minerva_estabilidad.db"  # explicito: lo hace el rollback

    code = restore_db.main([str(backup), "--db", str(destination)])

    assert code == 0 and destination.exists()


# --- resume_drawdown_stop --------------------------------------------------------------------


def test_resuming_the_drawdown_stop_is_refused_on_the_old_copy_and_points_to_the_container(
    migrated, capsys,
):
    code = resume_drawdown_stop.main(["--confirm"])

    assert code == resume_drawdown_stop.EXIT_USAGE
    error = capsys.readouterr().err
    assert "docker compose exec minerva python scripts/resume_drawdown_stop.py --confirm" in error


def test_resuming_the_drawdown_stop_with_an_explicit_database_still_works(migrated, capsys):
    async def build() -> None:
        db = Database(str(migrated / "otra.db"))
        await db.connect()
        await db.close()

    asyncio.run(build())

    code = resume_drawdown_stop.main(["--db", str(migrated / "otra.db")])

    assert code == resume_drawdown_stop.EXIT_NOT_ACTIVE or code == 0  # solo mira; no se niega
    assert "corre en Docker" not in capsys.readouterr().err


# --- el resumen diario -----------------------------------------------------------------------


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fd = FakeDocker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fd.state_dir))
    monkeypatch.setenv("MINERVA_DOCKER_EXE", str(fd.cmd))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    yield fd
    fd.shutdown()


def real_schema_db(path: Path) -> Path:
    async def build() -> None:
        db = Database(str(path))
        await db.connect()
        await db.close()

    asyncio.run(build())
    return path


def test_the_daily_summary_reads_the_container_database_in_docker_mode(fake, tmp_path):
    fake.seed_volume(real_schema_db(tmp_path / "volumen.db"))
    fake.update(service="running")

    snapshot = telegram_daily.collect_snapshot_from_docker("ignorada.db", T0)

    assert snapshot.status.total_medicion_rows == 0  # la base del contenedor (esquema real, vacia)
    assert snapshot.approved_24h == 0 and snapshot.cost_24h_usd == 0.0
    assert any("backup_db.py" in c for c in fake.calls())  # la copia salio del contenedor


def test_the_daily_summary_does_not_leave_the_exported_copy_behind(fake, tmp_path, monkeypatch):
    fake.seed_volume(real_schema_db(tmp_path / "volumen.db"))
    fake.update(service="running")
    scratch = tmp_path / "temporal"
    scratch.mkdir()
    monkeypatch.setattr(telegram_daily.tempfile, "tempdir", str(scratch))

    telegram_daily.collect_snapshot_from_docker("ignorada.db", T0)

    assert list(scratch.iterdir()) == []


def test_when_docker_fails_the_summary_is_still_delivered_and_says_it_could_not_read_the_base(
    fake, tmp_path, capsys,
):
    fake.seed_volume(real_schema_db(tmp_path / "volumen.db"))
    fake.update(service="running")
    fake.fail_when("backup_db.py")
    sent = []

    delivered = telegram_daily.run(
        host="127.0.0.1", port=1, timeout=1.0, db_path="ignorada.db", token="t", chat_id="c",
        now=T0, snapshot_fn=telegram_daily.collect_snapshot_from_docker,
        fetch_health_fn=lambda host, port, timeout: (None, "sin conexion"),
        send_fn=lambda token, chat, message, timeout: sent.append(message) or True,
    )

    assert delivered is True and len(sent) == 1
    assert "BOT SIN RESPUESTA" in sent[0]
    assert "Base: no se pudo leer" in sent[0] and "DockerError" in sent[0]


def test_docker_trouble_raises_a_docker_error_the_summary_can_report(fake, tmp_path, monkeypatch):
    fake.seed_volume(real_schema_db(tmp_path / "volumen.db"))
    fake.update(service="running", engine="down")
    real_export = telegram_daily.export_db_copy
    monkeypatch.setattr(
        telegram_daily, "export_db_copy",
        lambda out_dir, prefix: real_export(out_dir, prefix, engine_timeout=0),
    )

    with pytest.raises(DockerError):
        telegram_daily.collect_snapshot_from_docker("ignorada.db", T0)


def test_the_database_inside_the_container_is_what_the_summary_counts(fake, tmp_path):
    # no la copia vieja `source`: aunque exista y diga otra cosa, manda el contenedor
    old = make_native_db(tmp_path / "vieja.db", {"llm_logs": 99})
    fake.seed_volume(real_schema_db(tmp_path / "volumen.db"))
    fake.update(service="running")

    snapshot = telegram_daily.collect_snapshot_from_docker(str(old), T0)

    assert table_counts(old)["llm_logs"] == 99 and snapshot.status.total_medicion_rows == 0
