"""Un `docker` FALSO para probar, sin Docker, los scripts que lo invocan (Fase 7):
`scripts/docker_db.py`, `scripts/migrate_to_docker.ps1` y `scripts/rollback_from_docker.ps1`.

    python fake_docker.py <argumentos de docker...>          # con FAKE_DOCKER_STATE=<carpeta>

Entiende el subconjunto de `docker` / `docker compose` que usan esos scripts y lo hace de verdad en
una carpeta temporal:

- el "volumen" `/app/data` y `/app/logs` es `<estado>/app/data` y `<estado>/app/logs`;
- `compose run|exec ... python scripts/X.py ...` ejecuta el script REAL del repo (backup_db,
  restore_db, db_fingerprint) con las rutas del contenedor traducidas a las del "volumen" y a las de
  los montajes `-v`, de modo que respaldos, restauraciones y huellas son los de verdad;
- `compose up` levanta un servidor de /health falso (tests/support/fake_health_server.py) en
  `MINERVA_PORT`, y `stop`/`down` lo matan;
- cada llamada queda registrada en `state.json` (`calls`) para que la prueba compruebe el orden;
- `state["fail"]` es una lista de textos: toda llamada cuyos argumentos contengan uno falla.

No es un emulador completo de Docker: lo que no entiende sale con codigo 127 y lo dice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT = Path(__file__).resolve().parent
SERVICE = "minerva"
WATCHED_ENV = (
    "MINERVA_PORT", "MINERVA_REAL_BOT", "MINERVA_RESTART", "MINERVA_ENV_FILE",
    "COMPOSE_PROJECT_NAME",
)
DEFAULT_HEALTH = {
    "status": "ok", "mode": "PAPER_TRADING",
    "control": {"paused": False, "kill_switch_active": False},
    "llm_labeling": {"enabled": True, "frozen_hash_ok": True},
    "paper_execution": {"enabled": True, "active": True},
    "telegram_push": {"enabled": True, "active": True},
}
COMPOSE_SUBCOMMANDS = {
    "config", "ps", "build", "up", "stop", "start", "restart", "down", "run", "exec", "cp", "logs",
    "version", "pull", "kill", "rm",
}


def state_dir() -> Path:
    return Path(os.environ["FAKE_DOCKER_STATE"])


def default_state() -> dict:
    return {
        "engine": "up", "service": "absent", "volumes": [], "fail": [], "calls": [],
        "server_pid": None, "port": None, "project": None,
    }


def load() -> dict:
    path = state_dir() / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default_state()


def save(state: dict) -> None:
    (state_dir()).mkdir(parents=True, exist_ok=True)
    (state_dir() / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


def app_dir() -> Path:
    return state_dir() / "app"


def health_file() -> Path:
    return state_dir() / "health.json"


# --- traduccion de rutas del contenedor -------------------------------------------------------


def to_host(arg: str, mounts: dict[str, str]) -> str:
    for container_path, host_path in mounts.items():
        if arg == container_path or arg.startswith(container_path + "/"):
            return host_path + arg[len(container_path):]
    if arg == "/app" or arg.startswith("/app/"):
        return str(app_dir()) + arg[len("/app"):]
    return arg


def to_container(text: str, mounts: dict[str, str]) -> str:
    """Las rutas del host que imprimio un script, de vuelta a como las veria el contenedor."""
    pairs = [(str(app_dir()), "/app")] + [(host, ctr) for ctr, host in mounts.items()]
    for host, container in pairs:
        pattern = re.escape(host) + r"[^\s()]*"
        text = re.sub(
            pattern,
            lambda m, h=host, c=container: c + m.group(0)[len(h):].replace("\\", "/"),
            text,
        )
    return text


# --- "dentro del contenedor" -------------------------------------------------------------------


def ensure_volume(state: dict) -> None:
    (app_dir() / "data").mkdir(parents=True, exist_ok=True)
    (app_dir() / "logs").mkdir(parents=True, exist_ok=True)
    project = state.get("project") or "minerva"
    for name in (f"{project}_data", f"{project}_logs"):
        if name not in state["volumes"]:
            state["volumes"].append(name)


def container_run(command: list[str], mounts: dict[str, str], state: dict) -> int:
    ensure_volume(state)
    if not command:
        return 0
    program, rest = command[0], command[1:]
    if program == "python" and rest:
        script = REPO_ROOT / rest[0]
        args = [to_host(a, mounts) for a in rest[1:]]
        result = subprocess.run(
            [sys.executable, str(script), *args], cwd=app_dir(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        sys.stdout.write(to_container(result.stdout, mounts))
        sys.stderr.write(to_container(result.stderr, mounts))
        if result.returncode == 0 and rest[0].endswith("restore_db.py") and state.get("tamper"):
            # Gancho de las pruebas: la copia queda DISTINTA de la original (una fila de mas en DOS
            # tablas: con una sola no se ve si el informe de diferencias sigue bien tras la primera)
            import sqlite3
            conn = sqlite3.connect(str(app_dir() / "data" / "minerva_estabilidad.db"))
            conn.execute("INSERT INTO trades (valor) VALUES ('fila de mas')")
            conn.execute("INSERT INTO llm_logs (valor) VALUES ('fila de mas')")
            conn.commit()
            conn.close()
        return result.returncode
    if program == "sha256sum" and rest:
        path = Path(to_host(rest[0], mounts))
        if not path.is_file():
            sys.stderr.write(f"sha256sum: {rest[0]}: No such file or directory\n")
            return 1
        print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {rest[0]}")
        return 0
    if program == "rm":
        for target in [a for a in rest if not a.startswith("-")]:
            Path(to_host(target, mounts)).unlink(missing_ok=True)
        return 0
    if program == "cp" and len(rest) == 2:
        shutil.copy2(to_host(rest[0], mounts), to_host(rest[1], mounts))
        return 0
    if program == "true":
        return 0
    sys.stderr.write(f"fake docker: comando no soportado en el contenedor: {command}\n")
    return 127


# --- el servidor de /health del "contenedor" -----------------------------------------------------


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def kill(pid: int | None) -> None:
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)


def start_service(state: dict) -> int:
    port = int(os.environ.get("MINERVA_PORT", "8000"))
    if state.get("server_pid") and port_in_use(state.get("port") or port):
        return 0  # ya corre
    if port_in_use(port):
        sys.stderr.write(
            f"Error response from daemon: ports are not available: exposing port TCP "
            f"127.0.0.1:{port} -> 127.0.0.1:0: listen tcp 127.0.0.1:{port}: bind: "
            "Only one usage of each socket address is normally permitted.\n"
        )
        return 1
    ensure_volume(state)
    if not health_file().is_file():
        health_file().write_text(json.dumps(DEFAULT_HEALTH), encoding="utf-8")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    process = subprocess.Popen(
        [sys.executable, str(SUPPORT / "fake_health_server.py"), str(port), str(health_file())],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    deadline = time.time() + 10
    while time.time() < deadline and not port_in_use(port):
        time.sleep(0.1)
    state.update(service="running", server_pid=process.pid, port=port)
    return 0


def stop_service(state: dict, remove: bool = False) -> None:
    kill(state.get("server_pid"))
    state["server_pid"] = None
    if remove:
        state["service"] = "absent"
    elif state["service"] != "absent":
        state["service"] = "exited"


# --- docker compose ------------------------------------------------------------------------------


def parse_mounts(values: list[str]) -> dict[str, str]:
    mounts = {}
    for value in values:
        body = value[:-3] if value.endswith(":ro") else value
        host, container = body.rsplit(":", 1)
        mounts[container] = host
    return mounts


def split_run_args(tokens: list[str]) -> tuple[dict[str, str], list[str], list[str]]:
    """(montajes, [servicio], comando) de lo que sigue a `run` / `exec`."""
    mounts_raw: list[str] = []
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        flag = tokens[i]
        if flag in ("-v", "--volume"):
            mounts_raw.append(tokens[i + 1])
            i += 2
        elif flag in ("-e", "--env", "-w", "--workdir", "-u", "--user", "--name", "--entrypoint"):
            i += 2
        else:
            i += 1  # --rm, --no-deps, -T, ...
    return parse_mounts(mounts_raw), tokens[i:i + 1], tokens[i + 1:]


def compose(argv: list[str], state: dict) -> int:
    project = os.environ.get("COMPOSE_PROJECT_NAME") or "minerva"
    i = 0
    while i < len(argv) and argv[i] not in COMPOSE_SUBCOMMANDS:
        if argv[i] in ("-p", "--project-name") and i + 1 < len(argv):
            project = argv[i + 1]
        i += 2 if argv[i] in (
            "--progress", "-f", "--file", "--env-file", "-p", "--project-name",
            "--project-directory", "--profile", "--ansi",
        ) else 1
    if i >= len(argv):
        return 0
    state["project"] = project
    sub, rest = argv[i], argv[i + 1:]

    if sub == "version":
        print("5.6.0")
        return 0
    if state["engine"] == "down" and sub != "config":
        sys.stderr.write("failed to connect to the docker API: the daemon is not running\n")
        return 1
    if sub in ("config", "build", "pull"):
        return 0
    if sub == "ps":
        if state["service"] != "absent":
            labels = "com.docker.compose.oneoff=False"
            print(json.dumps({
                "Name": f"{project}-{SERVICE}-1", "Service": SERVICE, "State": state["service"],
                "Health": "healthy" if state["service"] == "running" else "",
                "Labels": labels,
            }))
        return 0
    if sub in ("up", "start", "restart"):
        if sub == "restart":
            stop_service(state)
        code = start_service(state)
        if code == 0:
            state["up_env"] = {k: os.environ.get(k) for k in WATCHED_ENV}
        return code
    if sub in ("stop", "kill"):
        stop_service(state)
        return 0
    if sub == "down":
        remove_volumes = any(a in ("-v", "--volumes") for a in rest)
        stop_service(state, remove=True)
        if remove_volumes:
            state["volumes"] = [v for v in state["volumes"] if not v.startswith(f"{project}_")]
            shutil.rmtree(app_dir(), ignore_errors=True)
        return 0
    if sub in ("run", "exec"):
        mounts, service, command = split_run_args(rest)
        if sub == "exec" and state["service"] != "running":
            sys.stderr.write(f"service \"{SERVICE}\" is not running\n")
            return 1
        return container_run(command, mounts, state)
    if sub == "cp":
        if state["service"] == "absent":
            sys.stderr.write(f"no container found for service \"{SERVICE}\"\n")
            return 1
        source, destination = rest[-2], rest[-1]
        if source.startswith(f"{SERVICE}:"):
            origin = Path(to_host(source.split(":", 1)[1], {}))
            if not origin.is_file():
                sys.stderr.write(f"Could not find the file {source}\n")
                return 1
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
        else:
            shutil.copy2(source, to_host(destination.split(":", 1)[1], {}))
        return 0
    if sub == "logs":
        print("fake log line")
        return 0
    sys.stderr.write(f"fake docker: compose {sub} no esta soportado\n")
    return 127


def main(argv: list[str]) -> int:
    state = load()
    state["calls"].append({
        "argv": argv, "env": {k: os.environ.get(k) for k in WATCHED_ENV},
        "time": time.time(),
    })
    joined = " ".join(argv)
    forced = next((text for text in state["fail"] if text in joined), None)
    if forced is not None:
        save(state)
        sys.stderr.write(
            f"fake docker: fallo forzado por la prueba ({forced!r}): docker {joined}\n"
        )
        return 1
    if not argv:
        code = 0
    elif argv[0] == "info":
        if state["engine"] == "down":
            sys.stderr.write("error during connect: the docker daemon is not running\n")
            code = 1
        else:
            print("fake-engine")
            code = 0
    elif argv[0] == "compose":
        code = compose(argv[1:], state)
    elif argv[0] == "volume":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "ls":
            print("\n".join(state["volumes"]))
            code = 0
        elif sub == "inspect":
            code = 0 if argv[-1] in state["volumes"] else 1
        elif sub == "rm":
            state["volumes"] = [v for v in state["volumes"] if v not in argv[2:]]
            code = 0
        else:
            code = 0
    else:
        sys.stderr.write(f"fake docker: {argv[0]} no esta soportado\n")
        code = 127
    save(state)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
