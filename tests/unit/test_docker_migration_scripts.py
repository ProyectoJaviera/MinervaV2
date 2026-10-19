"""Fase 7: `scripts/migrate_to_docker.ps1` y `scripts/rollback_from_docker.ps1`, la mudanza del
bot real a Docker y su vuelta atras. Se ejecutan DE VERDAD con PowerShell contra un `docker`, un
`schtasks` y un bot nativo FALSOS (tests/support) en puertos libres de verdad: la base nativa
es sintetica, el bot real (puerto 8000), `data/` del repo y Docker no se tocan nunca.

Lo importante: que las comprobaciones previas no cambien nada, que respeten la ventana segura,
que la mudanza verifique el volumen tabla por tabla, y que CUALQUIER fallo a partir de detener el
bot nativo (o un Ctrl+C) deje el sistema como estaba: bot nativo corriendo con la base de antes,
tarea reactivada, sin marca y sin contenedor ni volumen sobrantes."""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.support.fakes import FakeNativeBot, MigrationWorld, free_port, port_open, table_counts

REPO_ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="son scripts de Windows PowerShell"),
    pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="falta PowerShell"),
    pytest.mark.skipif(
        not (REPO_ROOT / ".venv" / "Scripts" / "python.exe").exists(), reason="falta el .venv",
    ),
]

MUTATING = ("build", "up", "run", "stop", "down", "start", "restart", "cp", "exec", "rm ")


@pytest.fixture
def world(tmp_path):
    w = MigrationWorld(tmp_path)
    yield w
    w.shutdown()


def mutating_docker_calls(w: MigrationWorld) -> list[str]:
    """Las llamadas a `docker compose` que cambian algo (las de solo lectura son info, config, ps y
    volume ls)."""
    return [c for c in w.docker.calls() if c.startswith("compose") and any(
        f" {word} " in f" {c} " for word in MUTATING
    )]


def assert_back_to_the_start(w: MigrationWorld, *, native_was_running: bool = True,
                             task: str = "Enabled") -> None:
    """El sistema quedo como antes de empezar la mudanza."""
    assert w.native.running() is native_was_running  # el bot nativo
    assert w.tasks.state(w.TASK) == task  # la tarea
    assert not w.marker.exists()  # sin marca
    assert w.docker.state()["service"] == "absent"  # sin contenedor


def count_rows(db: Path, table: str) -> int:
    return table_counts(db)[table]


def previous_databases(w: MigrationWorld) -> list[Path]:
    """Las bases apartadas por restore_db.py (`*.previo_<fecha>`), sin sus -wal/-shm."""
    return [p for p in w.data.glob("minerva_estabilidad.db.previo_*")
            if not p.name.endswith(("-wal", "-shm"))]


# --- ensayo y comprobaciones previas: no cambian nada -----------------------------------------


def test_a_dry_run_checks_everything_and_changes_nothing(world):
    result = world.migrate("-DryRun")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ENSAYO TERMINADO" in result.stdout
    for check in ("Ventana segura", "Docker responde", "Archivo de credenciales", "Base nativa",
                  "Bot nativo en el puerto", "docker-compose.yml valido",
                  "Volumenes de Docker libres", "Prompt congelado intacto"):
        assert f"[ OK  ] {check}" in result.stdout, check
    assert mutating_docker_calls(world) == []  # ni build, ni run, ni up
    assert world.native.calls() == []  # el bot nativo no se toco
    assert world.native.running()
    assert world.tasks.state(world.TASK) == "Enabled"
    assert not world.marker.exists() and not world.backups.exists()


def test_the_migration_refuses_outside_the_safe_window_and_names_the_next_one(world):
    for now, why in (
        ("2026-10-18T11:05:00Z", "minuto :05"),
        ("2026-10-18T11:41:00Z", "minuto :41"),
        ("2026-10-18T12:20:00Z", "cierre de vela de 4 h"),
    ):
        result = world.migrate("-Yes", now=now)

        assert result.returncode == 1, now
        assert "[FALLA] Ventana segura" in result.stdout and why.split()[0] in result.stdout
        assert "la proxima abre a las" in result.stdout
        assert "NO SE CAMBIO NADA" in result.stdout
    assert mutating_docker_calls(world) == [] and world.native.calls() == []
    assert world.native.running() and not world.marker.exists()


def test_the_edges_of_the_safe_window_are_inclusive(world):
    for now in ("2026-10-18T11:10:00Z", "2026-10-18T11:40:59Z"):
        result = world.migrate("-DryRun", now=now)

        assert result.returncode == 0, (now, result.stdout)


def test_a_missing_credentials_file_stops_everything_before_any_docker_command(world):
    world.env_file.unlink()

    result = world.migrate("-Yes")

    assert result.returncode == 1
    assert "[FALLA] Archivo de credenciales" in result.stdout
    assert "carpeta" in result.stdout  # explica el riesgo de que Docker Desktop cree una carpeta
    assert mutating_docker_calls(world) == []


@pytest.mark.parametrize(("line", "warns"), [
    ("AUTH_PASSWORD_HASH=$2b$12$IWDfragmento_de_prueba", True),
    ('AUTH_PASSWORD_HASH="$2b$12$IWDfragmento_de_prueba"', True),  # Compose SI interpola las dobles
    ("export AUTH_PASSWORD_HASH=$2b$12$IWDfragmento_de_prueba", True),
    ("AUTH_PASSWORD_HASH='$2b$12$IWDfragmento_de_prueba'", False),  # entre simples nadie lo toca
    ("# AUTH_PASSWORD_HASH=$2b$12$IWDfragmento_de_prueba", False),  # un comentario no es un valor
    ("AUTH_PASSWORD_HASH=", False),
    ("JWT_SECRET=0123456789abcdef", False),
])
def test_it_warns_about_a_dollar_value_without_single_quotes_and_never_prints_the_value(
    world, line, warns,
):
    # Compose lee el `.env` de la raiz en cada comando y escribiria trozos del hash en pantalla.
    world.env_file.write_text(f"{line}\nOTRA=1\n", encoding="utf-8")

    result = world.migrate("-DryRun")

    assert result.returncode == 0, result.stdout  # es un aviso, no una falla
    assert "IWDfragmento" not in result.stdout + result.stderr  # solo el NOMBRE de la variable
    assert ("AUTH_PASSWORD_HASH lleva signos $ sin comillas simples" in result.stdout) is warns
    assert "OTRA lleva" not in result.stdout


def test_a_paused_native_bot_is_not_migrated(world):
    world.native.set_health({
        "status": "stale", "control": {"paused": True, "kill_switch_active": False},
        "llm_labeling": {"enabled": True, "frozen_hash_ok": True},
    })

    result = world.migrate("-DryRun")

    assert result.returncode == 1 and "PAUSADA" in result.stdout


def test_something_listening_that_is_not_minerva_is_never_touched(world):
    world.native.shutdown()
    blocker = subprocess.Popen(
        [sys.executable, "-c",
         f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{world.native_port})); "
         "s.listen(); time.sleep(60)"],
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not port_open(world.native_port):
            time.sleep(0.1)

        result = world.migrate("-Yes")

        assert result.returncode == 1
        assert "algo escucha pero no responde /health como Minerva" in result.stdout
        assert world.native.calls() == []  # no se llamo a stop_bot
    finally:
        blocker.kill()


def test_docker_not_running_is_reported_and_nothing_changes(world):
    world.docker.update(engine="down")

    result = world.migrate("-DryRun")

    assert result.returncode == 1 and "[FALLA] Docker responde" in result.stdout
    assert world.native.calls() == []


def test_an_already_migrated_system_is_not_migrated_again(world):
    world.marker.write_text("migrado", encoding="utf-8")

    result = world.migrate("-DryRun")

    assert result.returncode == 1 and "[FALLA] Aun no migrado" in result.stdout


def test_existing_docker_volumes_are_never_overwritten_by_default(world):
    world.docker.update(volumes=[f"{world.PROJECT}_data"])

    result = world.migrate("-DryRun")

    assert result.returncode == 1
    assert "[FALLA] Volumenes de Docker libres" in result.stdout
    assert "-ReplaceVolume" in result.stdout


def test_the_docker_port_must_be_free_when_it_differs_from_the_native_port(world):
    other = FakeNativeBot(world.root / "otro", world.docker_port)
    other.start()
    try:
        result = world.migrate("-DryRun")

        assert result.returncode == 1
        assert f"[FALLA] Puerto {world.docker_port} libre" in result.stdout
    finally:
        other.shutdown()


def test_it_warns_but_proceeds_when_the_native_bot_is_not_running(tmp_path):
    w = MigrationWorld(tmp_path, native_running=False)
    try:
        result = w.migrate("-DryRun")

        assert result.returncode == 0, result.stdout
        assert "[AVISO] no hay bot nativo corriendo" in result.stdout
    finally:
        w.shutdown()


def test_without_confirmation_and_without_a_terminal_nothing_is_changed(world):
    result = world.migrate()  # sin -Yes y sin teclado: no hay quien escriba MIGRAR

    assert result.returncode == 1
    assert "Cancelado" in result.stdout and "usa -Yes" in result.stdout
    assert mutating_docker_calls(world) == [] and world.native.calls() == []
    assert world.native.running() and world.tasks.state(world.TASK) == "Enabled"


# --- la mudanza completa ----------------------------------------------------------------------


def test_the_full_migration_moves_the_database_verifies_it_and_starts_the_container(world):
    before = table_counts(world.db)

    result = world.migrate("-Yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "MUDANZA COMPLETA" in result.stdout
    # la base del volumen tiene EXACTAMENTE las mismas filas en todas las tablas
    assert table_counts(world.docker.volume_db) == before
    assert "IGUALES" in result.stdout
    for table in ("shadow_trades", "llm_logs", "paper_execution_log"):
        assert f"{table}={before[table]}" in result.stdout
    # la base nativa vieja no se modifico (la mudanza solo la LEE)
    assert table_counts(world.db) == before
    # dos respaldos: uno con el bot corriendo y el definitivo con el bot detenido
    assert len(list(world.backups.glob("pre_docker_*.db"))) == 1
    assert len(list(world.backups.glob("migracion_*.db"))) == 1
    # el bot nativo se detuvo, la tarea se deshabilito y quedo la marca
    assert world.native.calls() == [f"stop {world.native_port}"]
    assert not world.native.running()
    assert world.tasks.state(world.TASK) == "Disabled"
    marker = world.marker.read_text(encoding="ascii")
    assert f"task_name={world.TASK}" in marker and "task_state_before=Enabled" in marker
    assert f"project={world.PROJECT}" in marker
    # el contenedor corre y /health pasa las tres condiciones
    assert world.docker.state()["service"] == "running"
    assert "status=ok frozen_hash_ok=True paused=False" in result.stdout

    # --- el orden: construir ANTES de detener el bot; verificar ANTES de levantar el contenedor
    calls = world.docker.calls()
    index = {name: next(i for i, c in enumerate(calls) if name in c) for name in (
        " build", "restore_db.py", "db_fingerprint.py", " up -d")}
    assert index[" build"] < index["restore_db.py"] < index["db_fingerprint.py"] < index[" up -d"]
    log = world.log_text
    order = [log.index(f"PASO {n}/10") for n in range(1, 11)]
    assert order == sorted(order)
    assert log.index("PASO 2/10") < log.index("PASO 3/10") < log.index("PASO 4/10")

    # --- una instancia de PRUEBA (puerto distinto del 8000) nunca recibe los interruptores reales
    up = world.docker.calls_matching(" up -d")[-1]["env"]
    assert up["MINERVA_REAL_BOT"] == "false"
    assert up["MINERVA_RESTART"] == "no"
    assert up["MINERVA_PORT"] == str(world.docker_port)
    assert up["COMPOSE_PROJECT_NAME"] == world.PROJECT
    assert up["MINERVA_ENV_FILE"] == str(world.env_file)


def test_migration_then_rollback_with_the_native_bot_and_the_container_on_the_same_port(tmp_path):
    # Es la configuracion REAL: el bot nativo y el contenedor, ambos en el 8000. El contenedor
    # ocupa el puerto que el bot nativo acaba de dejar libre, y la vuelta atras hace lo inverso.
    # (Un puerto distinto para cada uno, como en el resto de las pruebas, no ejercita ninguna de
    # esas esperas.)
    w = MigrationWorld(tmp_path, same_port=True)
    try:
        before = table_counts(w.db)

        result = w.migrate("-Yes")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "MUDANZA COMPLETA" in result.stdout
        # con un solo puerto la comprobacion «puerto libre para Docker» no aplica
        assert "libre para Docker" not in result.stdout
        assert w.native.calls() == [f"stop {w.native_port}"]
        assert w.docker.state()["service"] == "running" and port_open(w.docker_port)
        assert table_counts(w.docker.volume_db) == before

        back = w.rollback("-Yes")

        assert back.returncode == 0, back.stdout + back.stderr
        assert "VUELTA COMPLETA" in back.stdout
        assert w.native.calls()[-1] == f"start {w.native_port} {w.db}"
        assert w.docker.state()["service"] == "absent"
        assert w.native.running() and not w.marker.exists()
        assert table_counts(w.db) == before
    finally:
        w.shutdown()


def test_a_failed_migration_on_the_same_port_hands_the_port_back_to_the_native_bot(tmp_path):
    w = MigrationWorld(tmp_path, same_port=True)
    w.docker.set_health({"status": "stale", "control": {"paused": False},
                         "llm_labeling": {"frozen_hash_ok": True}})
    w.health_timeout = 6
    try:
        before = table_counts(w.db)

        result = w.migrate("-Yes")

        assert result.returncode == 2, result.stdout
        assert "VUELTA ATRAS COMPLETA" in result.stdout
        # el contenedor suelta el puerto ANTES de que el bot nativo lo vuelva a pedir
        assert w.docker.state()["service"] == "absent"
        assert w.native.calls() == [f"stop {w.native_port}", f"start {w.native_port} {w.db}"]
        assert w.native.running() and not w.marker.exists()
        assert w.tasks.state(w.TASK) == "Enabled"
        assert table_counts(w.db) == before
    finally:
        w.shutdown()


def test_the_real_bot_switches_are_tied_to_port_8000_in_the_script_itself():
    # No se puede ejecutar con el puerto 8000 (es el del bot real), asi que se comprueba el texto:
    # solo DockerPort 8000 enciende los interruptores reales y el reinicio automatico, igual que
    # start_bot.ps1 solo los enciende con -Port 8000.
    for name in ("migrate_to_docker.ps1", "rollback_from_docker.ps1"):
        text = (REPO_ROOT / "scripts" / name).read_text(encoding="ascii")

        assert "$IsRealBot = ($DockerPort -eq 8000)" in text, name
        assert '$env:MINERVA_REAL_BOT = if ($IsRealBot) { "true" } else { "false" }' in text, name
        restart = '$env:MINERVA_RESTART = if ($IsRealBot) { "unless-stopped" } else { "no" }'
        assert restart in text, name


@pytest.mark.parametrize(("task_state", "expected_marker"), [
    ("Missing", "task_state_before=Missing"), ("Disabled", "task_state_before=Disabled"),
])
def test_a_task_that_is_missing_or_already_disabled_is_left_alone(
    tmp_path, task_state, expected_marker,
):
    w = MigrationWorld(tmp_path, task_state=task_state)
    try:
        result = w.migrate("-Yes")

        assert result.returncode == 0, result.stdout
        assert not any("/Change" in c for c in w.tasks.calls())  # nada que cambiar
        assert expected_marker in w.marker.read_text(encoding="ascii")
        assert w.tasks.state(w.TASK) == task_state
    finally:
        w.shutdown()


def test_replace_volume_saves_a_copy_of_the_existing_volume_before_deleting_it(world):
    world.docker.seed_volume(world.db, project=world.PROJECT)
    conn = sqlite3.connect(str(world.docker.volume_db))
    conn.execute("INSERT INTO shadow_trades (valor) VALUES ('del intento anterior')")
    conn.commit()
    conn.close()

    result = world.migrate("-Yes", "-ReplaceVolume")

    assert result.returncode == 0, result.stdout
    saved = list(world.backups.glob("volumen_reemplazado_*.db"))
    assert len(saved) == 1 and count_rows(saved[0], "shadow_trades") == 7  # 6 + la del intento
    assert "PASO 0" in world.log_text
    assert count_rows(world.docker.volume_db, "shadow_trades") == 6  # ahora, la base nativa


# --- si algo falla, todo vuelve a como estaba -------------------------------------------------


def test_a_failing_image_build_changes_nothing(world):
    world.docker.fail_when(" build")

    result = world.migrate("-Yes")

    assert result.returncode == 1, result.stdout
    assert "FALLO:" in result.stdout and "No se cambio nada del sistema" in result.stdout
    assert world.native.calls() == []  # el bot nativo siguio midiendo todo el rato
    assert_back_to_the_start(world)


def test_a_task_that_cannot_be_disabled_aborts_before_touching_the_bot(world):
    world.tasks.fail_when("/DISABLE")

    result = world.migrate("-Yes")

    assert result.returncode == 1 and "Acceso denegado" in result.stdout
    assert world.native.calls() == []
    assert_back_to_the_start(world)


def test_a_bot_that_will_not_stop_is_left_running_and_the_task_is_reenabled(world):
    world.native.fail("stop")

    result = world.migrate("-Yes")

    assert result.returncode == 2, result.stdout
    assert "stop_bot.ps1 termino con codigo 1" in result.stdout
    assert "VUELTA ATRAS COMPLETA" in result.stdout
    assert world.native.running()  # nunca se detuvo, y no se arranca otro encima
    assert world.native.calls() == [f"stop {world.native_port}"]
    assert world.tasks.state(world.TASK) == "Enabled"
    assert not world.marker.exists()
    assert world.docker.state()["service"] == "absent"


@pytest.mark.parametrize(("inject", "message"), [
    ("restore", "docker compose run fallo"),
    ("tamper", "el volumen NO coincide con la base de origen"),
])
def test_a_failed_or_inexact_import_restarts_the_native_bot_with_the_original_database(
    world, inject, message,
):
    before = table_counts(world.db)
    if inject == "restore":
        world.docker.fail_when("restore_db.py")
    else:
        world.docker.update(tamper=True)

    result = world.migrate("-Yes")

    assert result.returncode == 2, result.stdout
    assert message in result.stdout
    if inject == "tamper":
        # Cada tabla distinta se informa con SUS cifras (no con las de la primera ni "no existe")
        for table in ("trades", "llm_logs"):
            expected = f"{table}: origen {before[table]} filas, destino {before[table] + 1} filas"
            assert expected in result.stdout
    assert "VUELTA ATRAS COMPLETA" in result.stdout
    assert world.native.calls() == [
        f"stop {world.native_port}", f"start {world.native_port} {world.db}",
    ]
    assert_back_to_the_start(world)
    assert table_counts(world.db) == before  # la base nativa no se toco
    assert world.docker.state()["volumes"] == []  # el contenedor nunca arranco: el volumen se borra


def test_a_container_that_does_not_start_leaves_no_volume_marker_or_container(world):
    world.docker.fail_when(" up -d")

    result = world.migrate("-Yes")

    assert result.returncode == 2, result.stdout
    assert_back_to_the_start(world)
    assert world.docker.state()["volumes"] == []  # nunca llego a escribir: se borra
    assert not world.marker.exists()


@pytest.mark.parametrize(("health", "expected"), [
    ({"status": "stale", "control": {"paused": False}, "llm_labeling": {"frozen_hash_ok": True}},
     "no llego a status=ok"),
    ({"status": "ok", "control": {"paused": False}, "llm_labeling": {"frozen_hash_ok": False}},
     "frozen_hash_ok=false"),
    ({"status": "ok", "control": {"paused": True}, "llm_labeling": {"frozen_hash_ok": True}},
     "PAUSADA"),
])
def test_a_container_that_is_not_healthy_is_rolled_back_but_its_volume_is_kept(
    world, health, expected,
):
    world.docker.set_health(health)
    world.health_timeout = 8

    result = world.migrate("-Yes")

    assert result.returncode == 2, result.stdout
    assert "el contenedor no quedo sano" in result.stdout and expected in result.stdout
    assert "el volumen se CONSERVA" in result.stdout  # el contenedor llego a arrancar
    assert world.docker.state()["volumes"] != []
    assert world.native.calls()[-1].startswith("start ")  # el bot nativo volvio
    assert_back_to_the_start(world)


def test_when_the_rollback_itself_fails_the_script_says_so_and_exits_with_three(world):
    world.docker.set_health({"status": "stale", "control": {"paused": False},
                             "llm_labeling": {"frozen_hash_ok": True}})
    world.health_timeout = 6
    world.native.fail("start")  # al deshacer, el bot nativo no arranca

    result = world.migrate("-Yes")

    assert result.returncode == 3, result.stdout
    assert "FALLO al deshacer 'volver a arrancar el bot nativo" in result.stdout
    assert "ATENCION: la vuelta atras NO termino" in result.stdout
    assert "MUDANZA COMPLETA" not in result.stdout
    # lo demas SI se deshizo
    assert world.tasks.state(world.TASK) == "Enabled" and not world.marker.exists()
    assert world.docker.state()["service"] == "absent"


def test_if_the_native_bot_was_not_running_a_failure_does_not_start_it(tmp_path):
    w = MigrationWorld(tmp_path, native_running=False)
    w.docker.fail_when("restore_db.py")
    try:
        result = w.migrate("-Yes")

        assert result.returncode == 2, result.stdout
        assert w.native.calls() == []  # no estaba corriendo: vuelve a como estaba
        assert_back_to_the_start(w, native_was_running=False)
    finally:
        w.shutdown()


# --- la vuelta atras --------------------------------------------------------------------------


@pytest.fixture
def migrated(tmp_path):
    w = MigrationWorld(tmp_path)
    w.simulate_migrated(extra_rows={"shadow_trades": 5, "llm_logs": 3})
    yield w
    w.shutdown()


def test_the_rollback_brings_the_container_database_back_to_the_native_folder(migrated):
    w = migrated
    volume_counts = table_counts(w.docker.volume_db)
    old_native = table_counts(w.db)
    # el bot siguio midiendo en Docker
    assert volume_counts["shadow_trades"] == old_native["shadow_trades"] + 5

    result = w.rollback("-Yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VUELTA COMPLETA" in result.stdout
    # la base nativa ahora es la del contenedor, con lo que midio en Docker
    assert table_counts(w.db) == volume_counts
    # y la vieja se conserva, intacta, como *.previo_*
    previous = previous_databases(w)
    assert len(previous) == 1 and table_counts(previous[0]) == old_native
    # copias de seguridad en el host: antes de parar y la final
    assert len(list(w.backups.glob("pre_rollback_*.db"))) == 1
    assert len(list(w.backups.glob("rollback_*.db"))) >= 1
    # marca fuera, tarea reactivada, contenedor bajado y volumenes conservados
    assert not w.marker.exists()
    assert w.tasks.state(w.TASK) == "Enabled"
    assert w.docker.state()["service"] == "absent"
    assert w.docker.state()["volumes"] == [f"{w.PROJECT}_data", f"{w.PROJECT}_logs"]
    assert not any(" down -v" in c for c in w.docker.calls())  # no borra los volumenes
    # el bot nativo corre con /health sano
    assert w.native.running() and w.native.calls() == [f"start {w.native_port} {w.db}"]
    assert "status=ok frozen_hash_ok=True paused=False" in result.stdout
    # el orden: copiar ANTES de parar el contenedor; instalar ANTES de bajarlo
    log = w.log_text
    assert [log.index(f"PASO {n}/8") for n in range(1, 9)] == sorted(
        log.index(f"PASO {n}/8") for n in range(1, 9))


def test_the_rollback_does_nothing_if_there_is_no_migration_to_undo(world):
    result = world.rollback("-DryRun")

    assert result.returncode == 1
    assert "[FALLA] El bot esta migrado a Docker" in result.stdout
    assert world.native.calls() == [] and mutating_docker_calls(world) == []


def test_the_rollback_dry_run_changes_nothing(migrated):
    w = migrated

    result = w.rollback("-DryRun")

    assert result.returncode == 0, result.stdout
    assert "ENSAYO TERMINADO" in result.stdout
    assert w.marker.exists() and w.docker.state()["service"] == "running"
    assert w.native.calls() == [] and w.tasks.state(w.TASK) == "Disabled"
    assert not w.backups.exists()


def test_the_rollback_respects_the_safe_window_unless_told_otherwise(migrated):
    w = migrated

    refused = w.rollback("-Yes", now="2026-10-18T12:20:00Z")
    allowed = w.rollback("-DryRun", "-IgnoreWindow", now="2026-10-18T12:20:00Z")

    assert refused.returncode == 1 and "[FALLA] Ventana segura" in refused.stdout
    assert "-IgnoreWindow" in refused.stdout
    assert allowed.returncode == 0 and "[AVISO] fuera de la ventana segura" in allowed.stdout
    assert w.marker.exists() and w.docker.state()["service"] == "running"


def test_if_the_native_bot_will_not_start_the_container_comes_back_exactly_as_it_was(migrated):
    w = migrated
    w.native.fail("start")
    old_native = table_counts(w.db)

    result = w.rollback("-Yes")

    assert result.returncode == 2, result.stdout
    assert "El bot quedo en Docker, como estaba" in result.stdout
    assert w.docker.state()["service"] == "running"  # el contenedor volvio a levantarse
    assert w.marker.exists()  # y la marca, para que start_bot.ps1 siga rehusando
    assert w.tasks.state(w.TASK) == "Disabled"
    assert not w.native.running()
    # la base nativa vieja sigue disponible aparte
    assert len(previous_databases(w)) == 1
    assert table_counts(previous_databases(w)[0]) == old_native


def test_if_the_copy_cannot_be_verified_nothing_is_restored_and_the_container_resumes(migrated):
    w = migrated
    before = table_counts(w.db)
    w.docker.fail_when("db_fingerprint.py")

    result = w.rollback("-Yes")

    assert result.returncode == 2, result.stdout
    assert w.docker.state()["service"] == "running" and w.marker.exists()
    assert table_counts(w.db) == before  # la base nativa vieja no se toco
    assert previous_databases(w) == []


def test_the_rollback_restores_the_task_only_if_it_was_enabled_before_the_migration(tmp_path):
    w = MigrationWorld(tmp_path)
    w.simulate_migrated()
    w.marker.write_text(f"task_name={w.TASK}\ntask_state_before=Disabled\nproject={w.PROJECT}\n",
                        encoding="ascii")
    try:
        result = w.rollback("-Yes")

        assert result.returncode == 0, result.stdout
        assert w.tasks.state(w.TASK) == "Disabled"  # como estaba antes de la mudanza
    finally:
        w.shutdown()


# --- start_bot.ps1 rehusa con la marca -------------------------------------------------------


def test_start_bot_refuses_the_native_bot_on_the_old_database_after_the_migration(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "minerva_estabilidad.db").write_bytes(b"")
    (data / "MIGRADO_A_DOCKER.txt").write_text("migrado", encoding="ascii")

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(REPO_ROOT / "scripts" / "start_bot.ps1"), "-Port", str(free_port()),
         "-DatabasePath", str(data / "minerva_estabilidad.db"), "-LogDir", str(tmp_path / "logs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert "RECHAZADO: el bot corre en Docker" in result.stdout
    assert "rollback_from_docker.ps1" in result.stdout
    assert not re.search(r"Arrancando con DATABASE_PATH", result.stdout)  # ni siquiera lo intento
