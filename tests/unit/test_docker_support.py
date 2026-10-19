"""Fase 7: `scripts/docker_support.py`, el soporte para operar Minerva con el bot en Docker.

Dos piezas: la MARCA de la mudanza (`data/MIGRADO_A_DOCKER.txt`, que protege la base nativa
vieja de usarse por error) y `export_db_copy`, que saca del volumen una copia consistente y
VERIFICADA. Todo contra un `docker` falso (tests/support/fake_docker.py) que ejecuta los scripts
reales de respaldo sobre un "volumen" en una carpeta temporal: sin Docker, sin red y sin tocar
`data/`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import docker_support
from tests.support.fakes import FakeDocker, make_native_db, table_counts


@pytest.fixture
def fake(tmp_path, monkeypatch):
    fd = FakeDocker(tmp_path)
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fd.state_dir))
    monkeypatch.setenv("MINERVA_DOCKER_EXE", str(fd.cmd))
    monkeypatch.delenv("COMPOSE_PROJECT_NAME", raising=False)
    yield fd
    fd.shutdown()


def seeded(fake: FakeDocker, tmp_path: Path, service: str, rows: dict[str, int] | None = None):
    """Un volumen con una base de `rows` filas y el servicio en el estado `service`."""
    source = make_native_db(tmp_path / "origen.db", rows or {"shadow_trades": 7, "llm_logs": 5})
    fake.seed_volume(source)
    fake.update(service=service)
    return source


# --- la marca de la mudanza ------------------------------------------------------------------


def test_the_marker_lives_next_to_the_native_database_and_turns_docker_mode_on(tmp_path):
    db = tmp_path / "data" / "minerva_estabilidad.db"
    db.parent.mkdir()
    assert docker_support.marker_path(db) == db.parent / "MIGRADO_A_DOCKER.txt"
    assert docker_support.docker_mode_active(db) is False

    docker_support.marker_path(db).write_text("migrado", encoding="utf-8")

    assert docker_support.docker_mode_active(db) is True


def test_nothing_is_refused_without_the_marker(tmp_path):
    assert docker_support.docker_mode_refusal(tmp_path / "data" / "x.db", "pista") is None


def test_the_refusal_says_why_what_to_do_and_how_to_force_it(tmp_path):
    db = tmp_path / "x.db"
    docker_support.marker_path(db).write_text("migrado", encoding="utf-8")

    message = docker_support.docker_mode_refusal(db, "Usa docker_db.py.")

    assert "corre en Docker" in message and "VIEJA" in message
    assert "Usa docker_db.py." in message
    assert "--db explicitamente" in message
    assert str(docker_support.marker_path(db)) in message


def test_a_directory_with_the_marker_name_is_not_a_marker(tmp_path):
    db = tmp_path / "x.db"
    docker_support.marker_path(db).mkdir()  # una CARPETA con ese nombre no cuenta

    assert docker_support.docker_mode_active(db) is False


# --- interpretar `docker compose ps` ---------------------------------------------------------


def test_compose_ps_output_is_read_as_json_lines_as_a_list_and_as_nothing():
    one = {"State": "running", "Service": "minerva"}
    two = {"State": "exited", "Service": "minerva"}

    assert docker_support.parse_compose_ps("") == []
    assert docker_support.parse_compose_ps("  \n") == []
    assert docker_support.parse_compose_ps(json.dumps(one)) == [one]
    assert docker_support.parse_compose_ps(json.dumps(one) + "\n" + json.dumps(two)) == [one, two]
    assert docker_support.parse_compose_ps(json.dumps([one, two])) == [one, two]


def test_the_service_state_ignores_one_off_containers(monkeypatch):
    entries = [
        {"State": "running", "Labels": "com.docker.compose.oneoff=True"},
        {"State": "exited", "Labels": "com.docker.compose.oneoff=False"},
    ]

    class Result:
        stdout = "\n".join(json.dumps(e) for e in entries)
        returncode = 0

    monkeypatch.setattr(docker_support, "compose", lambda *a, **k: Result())

    assert docker_support.service_state() == "exited"  # el de un solo uso no cuenta


def test_the_service_state_is_absent_when_there_is_no_container(fake):
    assert docker_support.service_state() == "absent"


@pytest.mark.parametrize("service", ["running", "exited"])
def test_the_service_state_reports_what_docker_says(fake, service):
    fake.update(service=service)

    assert docker_support.service_state() == service


# --- sacar una copia consistente del volumen -------------------------------------------------


def test_a_running_service_is_copied_with_exec_and_cp_and_the_copy_is_exactly_the_volume(
    fake, tmp_path,
):
    seeded(fake, tmp_path, "running", {"shadow_trades": 7, "llm_logs": 5, "paper_execution_log": 2})

    copy = docker_support.export_db_copy(tmp_path / "salida", "informe")

    assert copy.parent == tmp_path / "salida" and copy.name.startswith("informe_")
    counts = table_counts(copy)
    assert counts == table_counts(fake.volume_db)
    assert counts["shadow_trades"] == 7 and counts["paper_execution_log"] == 2
    calls = fake.calls()
    assert any(" exec -T minerva python scripts/backup_db.py " in f" {c} " for c in calls)
    assert any(f" cp minerva:/app/data/backups/{copy.name} " in f" {c} " for c in calls)
    # con el servicio vivo no hace falta otro contenedor
    assert not any(" run " in f" {c} " for c in calls)
    # el archivo no se queda en el volumen: las copias para informes no se acumulan
    assert not list((fake.volume_dir / "backups").glob("*.db"))


def test_the_exported_copy_is_a_standalone_file_that_opens_anywhere(fake, tmp_path):
    seeded(fake, tmp_path, "running")

    copy = docker_support.export_db_copy(tmp_path / "salida")

    header = copy.read_bytes()[:100]
    assert (header[18], header[19]) == (1, 1)  # diario clasico, no WAL
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]  # sin -wal ni -shm


def test_a_stopped_service_is_copied_through_a_one_off_container(fake, tmp_path):
    seeded(fake, tmp_path, "exited")

    copy = docker_support.export_db_copy(tmp_path / "salida")

    assert table_counts(copy) == table_counts(fake.volume_db)
    calls = fake.calls()
    one_off = " run --rm --no-deps -T minerva python scripts/backup_db.py "
    assert any(one_off in f" {c} " for c in calls)
    assert not any(" exec " in f" {c} " for c in calls)  # un servicio detenido no admite exec
    assert any(" cp minerva:" in c for c in calls)  # el contenedor detenido sirve para `cp`


def test_with_no_container_at_all_a_mounted_folder_brings_the_file_out(fake, tmp_path):
    seeded(fake, tmp_path, "absent")

    copy = docker_support.export_db_copy(tmp_path / "salida")

    assert table_counts(copy) == table_counts(fake.volume_db)
    calls = fake.calls()
    assert not any(" cp minerva:" in c for c in calls)  # no hay contenedor del servicio
    mounted = [c for c in calls if " cp /app/data/backups/" in c]
    assert mounted and ":/out" in mounted[0]  # un contenedor de un solo uso con la carpeta montada


def test_keep_in_volume_leaves_the_file_and_prunes_the_oldest(fake, tmp_path):
    seeded(fake, tmp_path, "running")
    backups = fake.volume_dir / "backups"
    backups.mkdir()
    for name in ("docker_20200101_000000.db", "docker_20200102_000000.db"):
        (backups / name).write_bytes(b"viejo")

    copy = docker_support.export_db_copy(
        tmp_path / "salida", "docker", keep_in_volume=True, volume_keep=2,
    )

    left = sorted(p.name for p in backups.glob("docker_*.db"))
    assert len(left) == 2 and copy.name in left  # el nuevo y UNO de los viejos
    assert "docker_20200101_000000.db" not in left  # el mas antiguo se borro


def test_a_copy_that_does_not_match_its_hash_is_discarded(fake, tmp_path, monkeypatch):
    seeded(fake, tmp_path, "running")
    monkeypatch.setattr(docker_support, "sha256_of", lambda path: "0" * 64)

    with pytest.raises(docker_support.DockerError, match="SHA-256"):
        docker_support.export_db_copy(tmp_path / "salida")

    assert not list((tmp_path / "salida").glob("*.db"))  # nada a medias


def test_an_existing_copy_is_never_overwritten(fake, tmp_path, monkeypatch):
    seeded(fake, tmp_path, "running")
    first = docker_support.export_db_copy(tmp_path / "salida")
    monkeypatch.setattr(docker_support.posixpath, "basename", lambda path: first.name)

    with pytest.raises(docker_support.DockerError, match="Ya existe"):
        docker_support.export_db_copy(tmp_path / "salida")

    assert first.exists()


def test_a_failing_backup_inside_the_container_is_reported_with_its_message(fake, tmp_path):
    seeded(fake, tmp_path, "running")
    fake.fail_when("backup_db.py")

    with pytest.raises(docker_support.DockerError, match="fallo"):
        docker_support.export_db_copy(tmp_path / "salida")


def test_a_docker_engine_that_does_not_answer_is_reported_clearly(fake, tmp_path):
    seeded(fake, tmp_path, "running")
    fake.update(engine="down")

    with pytest.raises(docker_support.DockerError, match="Docker no responde"):
        docker_support.export_db_copy(tmp_path / "salida", engine_timeout=0)


def test_engine_ready_is_true_when_docker_answers_and_false_when_it_does_not(
    fake, monkeypatch,
):
    monkeypatch.setattr(docker_support.time, "sleep", lambda s: None)
    assert docker_support.engine_ready(timeout=5) is True

    fake.update(engine="down")

    assert docker_support.engine_ready(timeout=0) is False


def test_a_missing_docker_command_says_to_install_docker_desktop(monkeypatch):
    monkeypatch.setenv("MINERVA_DOCKER_EXE", "no-existe-este-docker-xyz")

    with pytest.raises(docker_support.DockerError, match="Docker Desktop"):
        docker_support.run_docker(["info"])


def test_compose_never_interpolates_the_real_env_file(fake):
    docker_support.compose(["ps"], check=False)

    argv = fake.state()["calls"][-1]["argv"]
    assert "--env-file" in argv
    assert argv[argv.index("--env-file") + 1].endswith("compose.vars")
    assert all(not a.endswith(".env") for a in argv)


def test_the_exported_copy_survives_the_source_being_a_wal_database_in_use(fake, tmp_path):
    # el volumen tiene una base en modo WAL con una conexion abierta escribiendo: el respaldo la ve
    source = make_native_db(tmp_path / "origen.db", {"shadow_trades": 4})
    fake.seed_volume(source)
    fake.update(service="running")
    live = sqlite3.connect(str(fake.volume_db))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO shadow_trades (valor) VALUES ('a medias en el wal')")
    live.commit()
    try:
        copy = docker_support.export_db_copy(tmp_path / "salida")
    finally:
        live.close()

    assert table_counts(copy)["shadow_trades"] == 5  # incluye lo que aun estaba en el -wal
