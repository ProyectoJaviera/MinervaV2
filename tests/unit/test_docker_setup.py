"""Fase 6, Docker (docs/FASE6_PLAN.md). Sin demonio de Docker (el PC de desarrollo no lo tiene
encendido): se verifica lo que SI se puede sin construir la imagen -- el healthcheck contra un
servidor local, la paridad de interruptores del compose con `scripts/start_bot.ps1`, las
propiedades de seguridad del Dockerfile / compose / .dockerignore (puerto en loopback, `.env`
fuera de la imagen, usuario sin privilegios, sin secretos), el archivo de versiones exactas y,
si el CLI de Docker esta instalado, la validacion real de `docker compose config` en una
carpeta temporal con un `.env` FALSO (nunca el `.env` del repo). Lo que falta (build y arranque
reales) esta en docs/FASE6_PLAN.md."""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

from scripts import export_docker_requirements as export_lock
from tests.support.winproc import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
LOCK = REPO_ROOT / "docker" / "requirements.lock"
START_BOT = REPO_ROOT / "scripts" / "start_bot.ps1"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


healthcheck = load_module("minerva_docker_healthcheck", REPO_ROOT / "docker" / "healthcheck.py")


# --- healthcheck ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "healthy", "fragment"),
    [
        ({"status": "ok", "llm_labeling": {"frozen_hash_ok": True}}, True, "ok"),
        ({"status": "ok"}, True, "ok"),  # un backend sin el bloque llm_labeling sigue siendo sano
        ({"status": "stale", "control": {"paused": True}}, True, "pausa deliberada"),
        ({"status": "stale", "control": {"paused": False}}, False, "stale"),
        ({"status": "stale"}, False, "stale"),  # un backend viejo sin el bloque control
        ({"status": "stale", "control": {"paused": "true"}}, False, "stale"),  # no es un booleano
        ({"status": "ok", "llm_labeling": {"frozen_hash_ok": False}}, False, "frozen_hash_ok"),
        ({"status": "stale", "control": {"paused": True},
          "llm_labeling": {"frozen_hash_ok": False}}, False, "frozen_hash_ok"),
        ({"status": "otra-cosa"}, False, "desconocido"),
        ({}, False, "desconocido"),
        (["ok"], False, "no es un objeto"),
        ("ok", False, "no es un objeto"),
        (None, False, "no es un objeto"),
    ],
)
def test_the_health_body_is_judged_by_status_pause_and_the_frozen_hash(body, healthy, fragment):
    ok, reason = healthcheck.evaluate(body)

    assert ok is healthy
    assert fragment in reason


class _HealthHandler(BaseHTTPRequestHandler):
    status = 200
    payload: bytes = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - nombre fijado por http.server
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).payload)

    def log_message(self, *args) -> None:  # silencio
        pass


@pytest.fixture
def health_server() -> Iterator:
    """Servidor local de /health con respuesta configurable (puerto efimero, solo loopback)."""
    handler = type("Handler", (_HealthHandler,), {"status": 200, "payload": b"{}"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield NS(handler=handler, url=f"http://127.0.0.1:{server.server_address[1]}/health")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _serve(server, *, status: int = 200, body: object | bytes = None) -> None:
    server.handler.status = status
    server.handler.payload = body if isinstance(body, bytes) else json.dumps(body).encode()


def test_the_check_is_healthy_against_a_server_that_answers_ok(health_server):
    _serve(health_server, body={"status": "ok", "llm_labeling": {"frozen_hash_ok": True}})

    assert healthcheck.check(health_server.url) == (True, "ok")


def test_the_check_accepts_a_deliberate_pause(health_server):
    _serve(health_server, body={"status": "stale", "control": {"paused": True}})

    ok, reason = healthcheck.check(health_server.url)

    assert ok is True and "pausa" in reason


@pytest.mark.parametrize("status", [500, 503, 404])
def test_the_check_fails_on_a_non_200_answer(health_server, status):
    _serve(health_server, status=status, body={"status": "ok"})

    assert healthcheck.check(health_server.url) == (False, f"HTTP {status}")


def test_the_check_fails_on_a_body_that_is_not_json(health_server):
    _serve(health_server, body=b"<html>no es json</html>")

    ok, reason = healthcheck.check(health_server.url)

    assert ok is False and "no es JSON" in reason


def test_the_check_ignores_proxy_variables_because_the_target_is_always_local(
    health_server, monkeypatch,
):
    # Tras un proxy corporativo el contenedor tiene HTTP_PROXY (lo necesita para salir a internet).
    # Un proxy que no responde no debe tumbar el healthcheck de un servicio que esta en 127.0.0.1.
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    _serve(health_server, body={"status": "ok"})

    assert healthcheck.check(health_server.url) == (True, "ok")


def test_the_check_fails_when_nothing_listens():
    # Puerto efimero recien liberado: nadie escucha.
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = server.server_address[1]
    server.server_close()

    ok, reason = healthcheck.check(f"http://127.0.0.1:{port}/health", timeout=1.0)

    assert ok is False and reason.startswith("sin respuesta")


def test_main_exits_0_when_healthy_and_1_otherwise(health_server, monkeypatch, capsys):
    monkeypatch.setenv("MINERVA_HEALTH_URL", health_server.url)
    _serve(health_server, body={"status": "ok"})
    assert healthcheck.main() == 0
    assert capsys.readouterr().out.strip() == "ok"

    _serve(health_server, body={"status": "stale"})
    assert healthcheck.main() == 1
    assert "stale" in capsys.readouterr().out


def test_the_healthcheck_uses_only_the_standard_library():
    source = (REPO_ROOT / "docker" / "healthcheck.py").read_text(encoding="utf-8")
    imported = set(re.findall(r"^(?:from|import) (\w+)", source, flags=re.MULTILINE))

    assert imported <= {"json", "os", "sys", "urllib", "__future__"}


# --- paridad de interruptores con start_bot.ps1 --------------------------------------------------


def _start_bot_env(real_bot: bool) -> dict[str, str]:
    """Variables FIJAS de `start_bot.ps1` para el puerto 8000 (real) o uno de prueba."""
    text = START_BOT.read_text(encoding="utf-8")
    conditional = {
        m.group(1): (m.group(2), m.group(3)) for m in re.finditer(
            r'^\$(\w+) = if \(\$isRealBot\) \{ "(\w+)" \} else \{ "(\w+)" \}', text, re.MULTILINE,
        )
    }
    env: dict[str, str] = {}
    for m in re.finditer(r"^\$env:(\w+) = (.+)$", text, re.MULTILINE):
        name, value = m.group(1), m.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            env[name] = value.strip('"')
        elif value.lstrip("$") in conditional:
            on, off = conditional[value.lstrip("$")]
            env[name] = on if real_bot else off
        else:
            env[name] = f"<dinamico:{value}>"  # DATABASE_PATH y LOG_DIR: rutas, se comparan aparte
    return env


def _resolve(value: object, *, real_bot: bool) -> str:
    """Resuelve `${MINERVA_REAL_BOT:-true}` como compose (las demas variables, su defecto)."""
    def substitute(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name == "MINERVA_REAL_BOT":
            return "true" if real_bot else "false"
        return default

    return re.sub(r"\$\{(\w+):-([^}]*)\}", substitute, str(value))


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _service() -> dict:
    return _compose()["services"]["minerva"]


@pytest.mark.parametrize("real_bot", [True, False])
def test_the_compose_switches_match_start_bot_for_the_real_bot_and_for_a_test_instance(real_bot):
    expected = {
        k: v for k, v in _start_bot_env(real_bot).items() if not v.startswith("<dinamico")
    }
    environment = {k: _resolve(v, real_bot=real_bot) for k, v in _service()["environment"].items()}

    assert expected, "no se pudo leer ninguna variable de start_bot.ps1"
    for name, value in expected.items():
        got = environment.get(name)
        assert got == value, f"{name}: el compose tiene {got!r} y start_bot.ps1 {value!r}"


def test_start_bot_still_sets_exactly_the_switches_this_test_knows_about():
    # Si start_bot.ps1 agrega un interruptor nuevo, el compose debe agregarlo tambien: este test
    # falla para obligar a decidirlo.
    assert set(_start_bot_env(True)) == {
        "DATABASE_PATH", "AUTO_OPEN_WITHOUT_LLM", "LOG_LEVEL", "LLM_LIVE_LABELING_ENABLED",
        "TELEGRAM_COMMANDS_ENABLED", "PAPER_EXECUTION_ENABLED", "NEWS_COLLECTION_ENABLED",
        "LESSONS_MEMORY_ENABLED", "TELEGRAM_PUSH_ENABLED", "LOG_DIR",
    }


def test_the_compose_paths_are_start_bots_defaults_under_app():
    text = START_BOT.read_text(encoding="utf-8")
    db = re.search(r'\[string\]\$DatabasePath = "([^"]+)"', text)
    logs = re.search(r'\[string\]\$LogDir = "([^"]+)"', text)
    assert db and logs
    environment = _service()["environment"]

    assert environment["DATABASE_PATH"] == f"/app/{db.group(1)}"
    assert environment["LOG_DIR"] == f"/app/{logs.group(1)}"


def test_the_test_mode_switch_turns_off_every_switch_start_bot_ties_to_port_8000():
    on = {k: _resolve(v, real_bot=True) for k, v in _service()["environment"].items()}
    off = {k: _resolve(v, real_bot=False) for k, v in _service()["environment"].items()}
    changed = {k for k in on if on[k] != off[k]}

    assert changed == {
        "LLM_LIVE_LABELING_ENABLED", "TELEGRAM_COMMANDS_ENABLED", "PAPER_EXECUTION_ENABLED",
        "NEWS_COLLECTION_ENABLED", "TELEGRAM_PUSH_ENABLED",
    }
    assert all(off[k] == "false" for k in changed)  # una instancia de prueba no gasta ni notifica


# --- compose: seguridad -------------------------------------------------------------------------


def test_the_compose_publishes_the_port_only_on_loopback():
    ports = _service()["ports"]

    assert ports
    for entry in ports:
        assert str(entry).startswith("127.0.0.1:"), entry
        assert str(entry).endswith(":8000")  # el puerto interno de la imagen


def test_the_compose_keeps_secrets_out_of_the_file_and_mounts_the_env_file_read_only():
    service = _service()

    secret_like = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|HASH|CHAT_ID", re.IGNORECASE)
    assert [k for k in service["environment"] if secret_like.search(k)] == []
    mounts = [v for v in service["volumes"] if isinstance(v, dict)]
    env_mount = next(m for m in mounts if m["target"] == "/app/.env")
    assert env_mount["type"] == "bind" and env_mount["source"] == "${MINERVA_ENV_FILE:-.env}"
    assert env_mount["read_only"] is True


def test_the_compose_never_uses_env_file_because_compose_mangles_bcrypt_hashes():
    # Hallazgo de la prueba real (Fase 7): Compose interpola los `$` de un env_file y el hash
    # bcrypt de AUTH_PASSWORD_HASH (`$2b$12$...`) llegaba al contenedor de 56 caracteres en vez de
    # 60: el login del dashboard fallaba con la contrasena correcta. El `.env` se monta y lo lee la
    # aplicacion con el mismo analizador que en modo nativo.
    assert "env_file" not in _service()


def test_the_compose_restarts_unless_stopped_by_default():
    # El bot real corre aqui tras la mudanza: vuelve solo tras reiniciar Docker o el PC, salvo que
    # alguien lo haya detenido a mano. Las instancias de prueba pasan MINERVA_RESTART=no.
    assert _service()["restart"] == "${MINERVA_RESTART:-unless-stopped}"


def test_the_compose_uses_named_volumes_for_data_and_logs_never_the_hosts_folders():
    service = _service()
    strings = [str(v) for v in service["volumes"] if isinstance(v, str)]

    assert "data:/app/data" in strings and "logs:/app/logs" in strings
    assert set(_compose()["volumes"]) == {"data", "logs"}
    # Ningun montaje de carpeta del host sobre los datos: abriria la base real de data/ (y SQLite
    # WAL no es fiable sobre el sistema de archivos compartido de Docker Desktop). Las UNICAS
    # rutas del host son docs/ (solo lectura) y el archivo de credenciales (solo lectura).
    host_binds = [v for v in strings if v.startswith(("./", "../", "/", "~"))]
    assert host_binds == ["./docs:/app/docs:ro"]
    binds = [v for v in service["volumes"] if isinstance(v, dict) and v["type"] == "bind"]
    assert [b["target"] for b in binds] == ["/app/.env"]
    assert all(b["read_only"] is True for b in binds)


def test_the_compose_hardens_the_container():
    service = _service()

    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["init"] is True
    assert str(service["stop_grace_period"]).endswith("s")
    assert service["logging"]["options"]["max-size"]  # los logs de Docker no crecen sin limite


def test_the_compose_healthcheck_runs_the_script_with_a_long_start_period():
    health = _service()["healthcheck"]

    assert health["test"] == ["CMD", "python", "docker/healthcheck.py"]
    assert int(str(health["start_period"]).rstrip("s")) >= 120
    assert int(health["retries"]) >= 2


def test_the_default_port_collides_with_the_real_bot_on_purpose():
    # El 8000 por defecto es el del bot real de este PC: levantarlo por error falla al enlazar el
    # puerto, ANTES de arrancar el proceso (no hay segundo bot).
    assert "127.0.0.1:${MINERVA_PORT:-8000}:8000" in [str(p) for p in _service()["ports"]]


# --- Dockerfile ----------------------------------------------------------------------------------


def _dockerfile_lines() -> list[str]:
    """Instrucciones del Dockerfile con las continuaciones de linea unidas y sin comentarios."""
    joined = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    return [
        line.strip() for line in joined.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_dockerfile_has_three_named_stages_in_order():
    froms = [line for line in _dockerfile_lines() if line.startswith("FROM ")]

    assert [line.rsplit(" AS ", 1)[1] for line in froms] == ["frontend", "python-deps", "runtime"]
    assert froms[0].startswith("FROM node:") and froms[1].startswith("FROM python:")
    assert froms[2].startswith("FROM python:")


def test_the_final_image_runs_as_an_unprivileged_user_and_never_switches_back_to_root():
    lines = _dockerfile_lines()
    runtime = lines[max(i for i, line in enumerate(lines) if line.startswith("FROM ")):]
    users = [line for line in runtime if line.startswith("USER ")]

    assert users == ["USER minerva"]
    assert "USER root" not in " ".join(lines)
    assert any("useradd" in line and "--uid" in line for line in runtime)
    # /app/data y /app/logs nacen con su dueno: un volumen nuevo hereda ese dueno.
    assert any("chown minerva:minerva /app/data /app/logs" in line for line in runtime)


def test_the_application_code_is_owned_by_root_and_only_data_and_logs_belong_to_the_user():
    # Hallazgo de la prueba real (Fase 7): con `chown -R minerva /app` el proceso podia reescribir
    # su propio codigo. Ahora solo /app/data y /app/logs son suyos.
    lines = _dockerfile_lines()
    runtime = lines[max(i for i, line in enumerate(lines) if line.startswith("FROM ")):]

    assert not any("--chown" in line for line in runtime if line.startswith("COPY "))
    assert not any(re.search(r"chown\s+-R", line) for line in runtime)
    chowned = [line for line in runtime if "chown " in line]
    assert len(chowned) == 1 and chowned[0].count("minerva:minerva") == 1
    assert "/app/data /app/logs" in chowned[0] and "/app " not in chowned[0].split("chown")[1]


def test_the_image_never_receives_the_env_file_nor_any_secret():
    text = DOCKERFILE.read_text(encoding="utf-8")
    lines = _dockerfile_lines()

    assert not re.search(r"^(COPY|ADD)\b.*\.env", text, flags=re.MULTILINE)
    # ADD descarga y descomprime por su cuenta: no se usa
    assert not any(line.startswith("ADD ") for line in lines)
    secret_like = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|CHAT_ID", re.IGNORECASE)
    assert [line for line in lines if line.split(" ", 1)[0] in {"ENV", "ARG"}
            and secret_like.search(line)] == []


def test_the_dependencies_come_from_the_pinned_lock_with_binary_wheels_only():
    install = [line for line in _dockerfile_lines() if "pip install" in line]

    assert install and all(
        "--only-binary=:all:" in line and "-r /tmp/requirements.lock" in line for line in install
    )
    assert "COPY docker/requirements.lock /tmp/requirements.lock" in _dockerfile_lines()
    # El proyecto NO se instala (editable ni no): sus rutas (frontend/dist, docs/) son relativas
    # al paquete.
    installs = [line for line in _dockerfile_lines() if "pip install" in line]
    assert not [line for line in installs if " -e " in line]
    assert not [line for line in installs if re.search(r"pip install\b.*\s\.(\s|$)", line)]


def test_the_layout_under_app_matches_what_the_application_resolves_at_runtime():
    lines = _dockerfile_lines()

    assert "WORKDIR /app" in lines
    copies = " ".join(line for line in lines if line.startswith("COPY "))
    for needle in (
        "app/ ./app/", "scripts/ ./scripts/", "./frontend/dist", "./docker/healthcheck.py",
    ):
        assert needle in copies, needle
    assert any("mkdir -p /app/data /app/logs /app/docs" in line for line in lines)
    # `app/main.py` busca el frontend en <raiz>/frontend/dist y verdict_record en <raiz>/docs.
    main_py = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert '/ "frontend" / "dist"' in main_py
    record = (REPO_ROOT / "app" / "trading" / "verdict_record.py").read_text(encoding="utf-8")
    assert '/ "docs"' in record


def test_the_container_starts_uvicorn_in_exec_form_on_port_8000_and_declares_the_healthcheck():
    lines = _dockerfile_lines()

    cmd = next(line for line in lines if line.startswith("CMD "))
    assert json.loads(cmd[4:]) == [
        "python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
    ]
    assert "EXPOSE 8000" in lines
    health = next(line for line in lines if line.startswith("HEALTHCHECK"))
    assert 'CMD ["python", "docker/healthcheck.py"]' in health
    assert "--start-period=300s" in health


def test_the_dockerfile_installs_optional_root_certificates_from_docker_certs():
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY docker/certs/ /tmp/extra-certs/" in text
    assert "update-ca-certificates" in text
    assert (REPO_ROOT / "docker" / "certs" / "README.md").exists()  # la carpeta existe en el repo
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "docker/certs/*.crt" in ignore and "docker/certs/*.key" in ignore


def test_the_python_stage_matches_the_projects_supported_range():
    text = DOCKERFILE.read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    default = re.search(r"ARG PYTHON_VERSION=(\d+\.\d+)", text)
    requires = re.search(r'requires-python = ">=(\d+\.\d+),<(\d+\.\d+)"', pyproject)

    assert default and requires
    assert Version(requires.group(1)) <= Version(default.group(1)) < Version(requires.group(2))


# --- .dockerignore -------------------------------------------------------------------------------


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Traduce un patron de .dockerignore (reglas de Go filepath.Match + `**`) a regex."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


@functools.lru_cache(maxsize=1)
def _ignore_rules() -> tuple[re.Pattern[str], ...]:
    patterns = [
        line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert not any(p.startswith("!") for p in patterns), "este emulador no soporta excepciones"
    return tuple(_pattern_to_regex(p) for p in patterns)


def _ignored(path: str) -> bool:
    """Un patron que coincide con la ruta o con una de sus carpetas la excluye del contexto."""
    parts = path.split("/")
    prefixes = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    return any(rule.match(prefix) for rule in _ignore_rules() for prefix in prefixes)


def _kept_files(source: Path) -> list[Path]:
    """Archivos de `source` que llegan al contexto de build (poda las carpetas excluidas)."""
    if source.is_file():
        return [] if _ignored(source.relative_to(REPO_ROOT).as_posix()) else [source]
    kept: list[Path] = []
    for root, dirs, files in os.walk(source):
        rel_root = Path(root).relative_to(REPO_ROOT).as_posix()
        dirs[:] = [d for d in dirs if not _ignored(f"{rel_root}/{d}")]
        kept.extend(
            Path(root) / f for f in files
            if not _ignored(f"{rel_root}/{f}") and not f.endswith(".pyc")
        )
    return kept


@pytest.mark.parametrize("path", [
    ".env", ".env.example", ".env.local", "data/minerva_estabilidad.db",
    "data/minerva_estabilidad.db-wal", "logs/minerva.log", "backtest.log", "download2.log",
    ".git/config", ".claude/settings.local.json", ".venv/Scripts/python.exe",
    "tests/unit/test_x.py", "frontend/node_modules/react/index.js", "frontend/dist/index.html",
    "app/__pycache__/main.cpython-311.pyc", "docs/historial/SPEC.md", "docs/README.md", "README.md",
    "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE", ".github/pull_request_template.md",
    "scripts/start_bot.ps1", "scripts/run_dev_server.py", "scripts/export_docker_requirements.py",
    "scripts/docker_db.py", "scripts/migrate_to_docker.ps1", "scripts/rollback_from_docker.ps1",
    "scripts/docker_common.ps1", "minerva.db", "Dockerfile", "docker-compose.yml",
])
def test_the_build_context_excludes_secrets_data_and_what_is_not_needed(path):
    assert _ignored(path), path


@pytest.mark.parametrize("path", [
    "app/main.py", "app/trading/verdict_record.py", "scripts/telegram_daily.py",
    "scripts/measurement_status.py", "scripts/check_health.py", "docker/healthcheck.py",
    # las herramientas que se corren DENTRO del contenedor (docker compose exec/run)
    "scripts/backup_db.py", "scripts/restore_db.py", "scripts/db_fingerprint.py",
    "scripts/docker_support.py", "scripts/resume_drawdown_stop.py",
    "docker/requirements.lock", "docker/certs/README.md", "frontend/package.json",
    "frontend/package-lock.json", "frontend/index.html", "frontend/src/main.tsx",
    "frontend/vite.config.ts", "frontend/tsconfig.json",
])
def test_the_build_context_keeps_everything_a_stage_copies(path):
    assert not _ignored(path), path


def test_every_copy_source_exists_and_survives_the_dockerignore():
    for line in _dockerfile_lines():
        if not line.startswith("COPY ") or "--from=" in line:
            continue
        args = [a for a in line.split()[1:] if not a.startswith("--")]
        for source in args[:-1]:
            path = REPO_ROOT / source
            assert path.exists(), f"COPY de algo que no existe: {source}"
            assert _kept_files(path), f"el .dockerignore deja vacio el origen {source}"


def test_gitattributes_keeps_the_docker_files_with_unix_line_endings():
    text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")

    for name in ("Dockerfile", "docker-compose.yml", "docker/**", ".dockerignore"):
        assert re.search(rf"^{re.escape(name)} text eol=lf$", text, flags=re.MULTILINE), name


# --- archivo de versiones exactas ----------------------------------------------------------------


def _lock_pins() -> dict[str, str]:
    pins = {}
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==")
        pins[export_lock.normalize(name)] = version
    return pins


def test_every_runtime_dependency_of_pyproject_is_pinned_and_inside_its_range():
    pins = _lock_pins()

    for raw in export_lock.runtime_roots():
        req = Requirement(raw)
        name = export_lock.normalize(req.name)
        assert name in pins, f"{req.name} no esta fijado en docker/requirements.lock"
        assert req.specifier.contains(pins[name], prereleases=True), (
            f"{req.name}=={pins[name]} no cumple {req.specifier}"
        )


def test_the_lock_has_only_exact_pins_and_no_development_nor_windows_only_packages():
    pins = _lock_pins()
    lines = [
        line for line in LOCK.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert all(re.fullmatch(r"[A-Za-z0-9_.\-]+==[\w.]+", line) for line in lines)
    assert len(lines) == len(pins)  # sin duplicados
    for forbidden in ("pytest", "pytest-asyncio", "ruff", "colorama", "pluggy", "iniconfig"):
        assert forbidden not in pins, f"{forbidden} no debe ir en la imagen"
    # sin uvloop a proposito (no esta en el conjunto probado); uvicorn va sin extras
    assert "uvloop" not in pins
    assert all("[" not in line for line in lines)
    assert "tzdata" in pins  # zoneinfo (REPORT_TIMEZONE) necesita la base de zonas horarias


def test_the_lock_header_says_it_is_generated_and_how_to_regenerate_it():
    head = LOCK.read_text(encoding="utf-8").splitlines()[:4]

    assert any("GENERADO" in line for line in head)
    assert any("scripts/export_docker_requirements.py" in line for line in head)


class _FakeDist:
    def __init__(self, name: str, version: str, requires: list[str] | None = None) -> None:
        self.version = version
        self.requires = requires
        self.metadata = {"Name": name}


def _fake_distributions(*dists: _FakeDist):
    table = {export_lock.normalize(d.metadata["Name"]): d for d in dists}

    def lookup(name: str) -> _FakeDist:
        try:
            return table[export_lock.normalize(name)]
        except KeyError:
            from importlib import metadata
            raise metadata.PackageNotFoundError(name) from None

    return lookup


def test_the_closure_follows_dependencies_evaluates_linux_markers_and_extras():
    lookup = _fake_distributions(
        _FakeDist("app-like", "1.0", ["dep-a>=1", 'win-only; sys_platform == "win32"',
                                      'linux-only; sys_platform == "linux"']),
        _FakeDist("dep-a", "2.0", ["deep>=0.1"]),
        _FakeDist("deep", "0.5"),
        _FakeDist("win-only", "9.9"),
        _FakeDist("linux-only", "3.0"),
        _FakeDist("with-extra", "4.0", ['extra-dep; extra == "standard"', "base-dep"]),
        _FakeDist("extra-dep", "5.0"),
        _FakeDist("base-dep", "6.0"),
    )

    plain = export_lock.collect_closure(["app-like"], lookup)
    assert plain.versions == {
        "app-like": "1.0", "dep-a": "2.0", "deep": "0.5", "linux-only": "3.0",
    }  # win-only queda fuera: la imagen es Linux

    without = export_lock.collect_closure(["with-extra"], lookup)
    assert "extra-dep" not in without.versions and "base-dep" in without.versions
    with_extra = export_lock.collect_closure(["with-extra[standard]"], lookup)
    assert with_extra.versions["extra-dep"] == "5.0"


def test_a_linux_dependency_that_is_not_installed_is_reported_not_invented():
    lookup = _fake_distributions(
        _FakeDist("root", "1.0", ['uvloop; sys_platform != "win32"', "present"]),
        _FakeDist("present", "2.0"),
    )

    closure = export_lock.collect_closure(["root"], lookup)

    assert closure.missing == {"uvloop"}
    assert "uvloop" not in closure.versions
    pins = [line for line in export_lock.render_lock(closure).splitlines() if "==" in line]
    # (el encabezado del archivo menciona uvloop en un comentario: por eso se miran solo los pines)
    assert pins == ["present==2.0", "root==1.0"]


def test_the_rendered_lock_is_sorted_by_normalized_name_and_exact():
    lookup = _fake_distributions(
        _FakeDist("Zeta_Pkg", "1.0"), _FakeDist("httpcore", "1.0"), _FakeDist("httpcore2", "2.0"),
        _FakeDist("alpha", "0.1"),
    )
    closure = export_lock.collect_closure(["Zeta_Pkg", "httpcore", "httpcore2", "alpha"], lookup)

    body = [line for line in export_lock.render_lock(closure).splitlines() if "==" in line]

    assert body == ["alpha==0.1", "httpcore==1.0", "httpcore2==2.0", "Zeta_Pkg==1.0"]


# --- docker compose config (solo si el CLI esta instalado; no necesita el demonio) ---------------


def _compose_cli_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, timeout=30,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _compose_config(tmp_path: Path, **overrides: str) -> dict:
    """`docker compose config` sobre una copia del compose con un `.env` FALSO (nunca el real)."""
    shutil.copy(COMPOSE, tmp_path / "docker-compose.yml")
    (tmp_path / "docs").mkdir(exist_ok=True)
    fake = tmp_path / "fake.env"
    fake.write_text("TELEGRAM_BOT_TOKEN=000000:FAKE\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("MINERVA_")}
    env.update({"MINERVA_ENV_FILE": str(fake), **overrides})
    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(fake),
         "-f", str(tmp_path / "docker-compose.yml"), "config", "--format", "json"],
        capture_output=True, text=True, timeout=60, env=env, cwd=tmp_path,
        creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(not _compose_cli_available(), reason="falta el CLI de docker compose")
def test_docker_compose_accepts_the_file_and_resolves_the_real_bot_defaults(tmp_path):
    service = _compose_config(tmp_path)["services"]["minerva"]

    assert service["ports"][0]["host_ip"] == "127.0.0.1"
    assert service["ports"][0]["published"] == "8000" and service["ports"][0]["target"] == 8000
    assert service["restart"] == "unless-stopped"
    env = service["environment"]
    # el .env FALSO no entra al entorno del contenedor: se monta y lo lee la aplicacion
    assert "TELEGRAM_BOT_TOKEN" not in env and "AUTH_PASSWORD_HASH" not in env
    for name in ("LLM_LIVE_LABELING_ENABLED", "TELEGRAM_COMMANDS_ENABLED",
                 "PAPER_EXECUTION_ENABLED", "NEWS_COLLECTION_ENABLED", "TELEGRAM_PUSH_ENABLED"):
        assert env[name] == "true", name
    assert env["LESSONS_MEMORY_ENABLED"] == "false" and env["AUTO_OPEN_WITHOUT_LLM"] == "false"
    binds = {b["target"]: b for b in service["volumes"] if b["type"] == "bind"}
    assert set(binds) == {"/app/docs", "/app/.env"}
    assert all(b["read_only"] for b in binds.values())
    assert binds["/app/.env"]["source"].endswith("fake.env")


@pytest.mark.skipif(not _compose_cli_available(), reason="falta el CLI de docker compose")
def test_docker_compose_applies_the_test_instance_overrides(tmp_path):
    service = _compose_config(
        tmp_path, MINERVA_REAL_BOT="false", MINERVA_PORT="8001", MINERVA_RESTART="no",
    )["services"]["minerva"]

    port = service["ports"][0]
    assert port["published"] == "8001" and port["host_ip"] == "127.0.0.1"
    assert service["restart"] == "no"
    for name in ("LLM_LIVE_LABELING_ENABLED", "TELEGRAM_COMMANDS_ENABLED",
                 "PAPER_EXECUTION_ENABLED", "NEWS_COLLECTION_ENABLED", "TELEGRAM_PUSH_ENABLED"):
        assert service["environment"][name] == "false", name
