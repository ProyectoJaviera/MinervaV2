"""Ayudantes de las pruebas de la mudanza a Docker (Fase 7): un `docker` falso listo para usar, una
base "nativa" sintetica y un bot nativo falso. Nada de esto toca Docker, `data/` ni el bot real."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from tests.support import fake_docker

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT = Path(__file__).resolve().parent
# Tablas con las que la restauracion reconoce una base de Minerva, mas la del brazo ejecutado.
TABLES = ("system_state", "trades", "shadow_trades", "llm_logs", "signals", "ohlcv_cache",
          "paper_execution_log")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_native_db(path: Path, rows: dict[str, int] | None = None) -> Path:
    """Una base SQLite en modo WAL con las tablas de `TABLES` y `rows` filas en cada una."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in TABLES:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY, valor TEXT)")
            for i in range((rows or {}).get(table, 3)):
                conn.execute(f"INSERT INTO {table} (valor) VALUES (?)", (f"fila-{i}",))
        conn.commit()
    finally:
        conn.close()
    return path


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


class FakeDocker:
    """Un `docker` falso con su estado en `root`. `env()` devuelve el entorno para que los scripts
    bajo prueba lo usen (`MINERVA_DOCKER_EXE`) en lugar del docker real."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state_dir = root / "fake_docker_state"
        self.bin_dir = root / "fake_bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cmd = self.bin_dir / "docker.cmd"
        script = Path(fake_docker.__file__).resolve()
        self.cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="ascii")
        self._write_state(fake_docker.default_state())

    # --- estado -------------------------------------------------------------------------------

    def _write_state(self, state: dict) -> None:
        (self.state_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def state(self) -> dict:
        return json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))

    def update(self, **fields) -> None:
        state = self.state()
        state.update(fields)
        self._write_state(state)

    def fail_when(self, *texts: str) -> None:
        self.update(fail=list(texts))

    def set_health(self, payload: dict) -> None:
        (self.state_dir / "health.json").write_text(json.dumps(payload), encoding="utf-8")

    def calls(self) -> list[str]:
        """Los argumentos de cada llamada a `docker`, como un texto."""
        return [" ".join(c["argv"]) for c in self.state()["calls"]]

    def calls_matching(self, text: str) -> list[dict]:
        return [c for c in self.state()["calls"] if text in " ".join(c["argv"])]

    @property
    def volume_dir(self) -> Path:
        return self.state_dir / "app" / "data"

    @property
    def volume_db(self) -> Path:
        return self.volume_dir / "minerva_estabilidad.db"

    def seed_volume(self, db: Path, project: str = "minerva") -> None:
        """Deja una base ya en el "volumen" (como si el bot corriera en Docker)."""
        self.volume_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "app" / "logs").mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        dest = sqlite3.connect(str(self.volume_db))
        source.backup(dest)
        dest.close()
        source.close()
        self.update(volumes=[f"{project}_data", f"{project}_logs"], project=project)

    # --- entorno para los scripts ---------------------------------------------------------------

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env["FAKE_DOCKER_STATE"] = str(self.state_dir)
        env["MINERVA_DOCKER_EXE"] = str(self.cmd)
        env["PATH"] = str(self.bin_dir) + os.pathsep + env["PATH"]
        env.update(extra)
        return env

    def shutdown(self) -> None:
        fake_docker.kill(self.state().get("server_pid"))


class FakeTasks:
    """El Programador de tareas falso (`schtasks`): estado en un JSON, llamadas registradas."""

    def __init__(self, root: Path) -> None:
        self.state_file = root / "fake_schtasks.json"
        self.cmd = root / "fake_bin" / "schtasks.cmd"
        self.cmd.parent.mkdir(parents=True, exist_ok=True)
        script = SUPPORT / "fake_schtasks.py"
        self.cmd.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="ascii")
        self.state_file.write_text("{}", encoding="utf-8")

    def _load(self) -> dict:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def set(self, name: str, state: str) -> None:
        data = self._load()
        data[name] = state
        self.state_file.write_text(json.dumps(data), encoding="utf-8")

    def state(self, name: str) -> str:
        return self._load().get(name, "Missing")

    def fail_when(self, *texts: str) -> None:
        data = self._load()
        data["_fail"] = list(texts)
        self.state_file.write_text(json.dumps(data), encoding="utf-8")

    def calls(self) -> list[str]:
        return self._load().get("_calls", [])


class FakeNativeBot:
    """El "bot nativo": un servidor de /health falso en un puerto, con scripts de start/stop
    falsos que la mudanza llama en lugar de los reales (MINERVA_START_BOT_SCRIPT /
    MINERVA_STOP_BOT_SCRIPT)."""

    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.port = port
        self.dir = root / "fake_native"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.health_file = self.dir / "health.json"
        self.pid_file = self.dir / "server.pid"
        self.log = self.dir / "calls.log"
        self.set_health(fake_docker.DEFAULT_HEALTH)
        python = sys.executable
        server = SUPPORT / "fake_health_server.py"
        (self.dir / "stop.ps1").write_text(
            "param([int]$Port = 8000)\n"
            f"$dir = '{self.dir}'\n"
            "Add-Content -Path (Join-Path $dir 'calls.log') -Value \"stop $Port\"\n"
            "if (Test-Path (Join-Path $dir 'fail_stop')) { exit 1 }\n"
            "$pidFile = Join-Path $dir 'server.pid'\n"
            "if (Test-Path $pidFile) {\n"
            "  Stop-Process -Id ([int](Get-Content $pidFile)) -Force "
            "-ErrorAction SilentlyContinue\n"
            "  Remove-Item $pidFile -Force\n"
            "}\n"
            "exit 0\n", encoding="ascii")
        (self.dir / "start.ps1").write_text(
            "param([int]$Port = 8000, [string]$DatabasePath = '', [string]$LogDir = '')\n"
            f"$dir = '{self.dir}'\n"
            "Add-Content -Path (Join-Path $dir 'calls.log') -Value \"start $Port $DatabasePath\"\n"
            "if (Test-Path (Join-Path $dir 'fail_start')) { exit 1 }\n"
            f"$p = Start-Process -FilePath '{python}' -PassThru -WindowStyle Hidden -ArgumentList "
            f"@('{server}', \"$Port\", '{self.health_file}')\n"
            "Set-Content -Path (Join-Path $dir 'server.pid') -Value $p.Id\n"
            "$deadline = (Get-Date).AddSeconds(15)\n"
            "while ((Get-Date) -lt $deadline) {\n"
            "  try { $c = New-Object System.Net.Sockets.TcpClient('127.0.0.1', $Port); "
            "$c.Close(); exit 0 }\n"
            "  catch { Start-Sleep -Milliseconds 200 }\n"
            "}\n"
            "exit 1\n", encoding="ascii")

    @property
    def stop_script(self) -> Path:
        return self.dir / "stop.ps1"

    @property
    def start_script(self) -> Path:
        return self.dir / "start.ps1"

    def set_health(self, payload: dict) -> None:
        self.health_file.write_text(json.dumps(payload), encoding="utf-8")

    def fail(self, what: str) -> None:
        """what: 'stop' o 'start' -- la siguiente llamada sale con codigo 1."""
        (self.dir / f"fail_{what}").write_text("1", encoding="ascii")

    def start(self) -> None:
        """Arranca el servidor "como si start_bot.ps1 lo hubiera hecho"."""
        process = subprocess.Popen(
            [sys.executable, str(SUPPORT / "fake_health_server.py"), str(self.port),
             str(self.health_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        self.pid_file.write_text(str(process.pid), encoding="ascii")
        assert wait_until(lambda: port_open(self.port)), "el bot nativo falso no abrio su puerto"

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="ascii").splitlines() if self.log.exists() else []

    def running(self) -> bool:
        return port_open(self.port)

    def shutdown(self) -> None:
        if self.pid_file.exists():
            fake_docker.kill(int(self.pid_file.read_text().strip()))
            self.pid_file.unlink(missing_ok=True)


class MigrationWorld:
    """Todo lo que necesitan las pruebas de scripts/migrate_to_docker.ps1 y
    rollback_from_docker.ps1: una base nativa sintetica, un bot nativo falso, un Docker falso, un
    Programador de tareas falso y puertos libres de verdad. NADA toca el bot real (nunca se usa
    el puerto 8000), `data/` del repo ni Docker."""

    TASK = "MinervaStartBotTest"
    PROJECT = "minerva-prueba"
    NOW = "2026-10-18T11:20:00Z"  # dentro de la ventana segura

    def __init__(
        self, root: Path, rows: dict[str, int] | None = None, task_state: str = "Enabled",
        native_running: bool = True, same_port: bool = False,
    ) -> None:
        self.root = root
        self.data = root / "data"
        self.logs = root / "logs"
        self.backups = self.data / "backups"
        self.db = make_native_db(
            self.data / "minerva_estabilidad.db",
            rows or {"shadow_trades": 6, "llm_logs": 4, "paper_execution_log": 2},
        )
        self.env_file = root / "credenciales.txt"
        self.env_file.write_text("archivo de credenciales de mentira\n", encoding="utf-8")
        self.docker = FakeDocker(root)
        self.tasks = FakeTasks(root)
        if task_state != "Missing":
            self.tasks.set(self.TASK, task_state)
        self.native_port = free_port()
        # `same_port` es la configuracion REAL (el bot nativo y el contenedor, ambos en el 8000): el
        # contenedor ocupa justo el puerto que el bot nativo acaba de dejar libre.
        self.docker_port = self.native_port if same_port else free_port()
        self.health_timeout = 25
        self.native = FakeNativeBot(root, self.native_port)
        if native_running:
            self.native.start()

    @property
    def marker(self) -> Path:
        return self.data / "MIGRADO_A_DOCKER.txt"

    def simulate_migrated(self, extra_rows: dict[str, int] | None = None) -> None:
        """Deja el sistema COMO TRAS una mudanza hecha (sin repetirla): la base en el volumen, el
        contenedor corriendo, la marca escrita, la tarea deshabilitada y el bot nativo detenido.
        `extra_rows` son filas que el bot "escribio" en Docker despues de la mudanza."""
        self.docker.seed_volume(self.db, project=self.PROJECT)
        for table, n in (extra_rows or {}).items():
            conn = sqlite3.connect(str(self.docker.volume_db))
            for i in range(n):
                conn.execute(f"INSERT INTO {table} (valor) VALUES (?)", (f"docker-{i}",))
            conn.commit()
            conn.close()
        if self.docker_port == self.native_port:
            self.native.shutdown()  # el contenedor toma el puerto que deja el bot nativo
        env = self.env()
        env.update(MINERVA_PORT=str(self.docker_port), COMPOSE_PROJECT_NAME=self.PROJECT)
        done = subprocess.run(
            [str(self.docker.cmd), "compose", "-p", self.PROJECT, "up", "-d"], env=env,
            capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr
        assert wait_until(lambda: port_open(self.docker_port)), (
            "el contenedor falso no abrio su puerto"
        )
        self.marker.write_text(
            "Minerva: marca de prueba\n"
            f"task_name={self.TASK}\ntask_state_before=Enabled\nproject={self.PROJECT}\n",
            encoding="ascii",
        )
        if self.tasks.state(self.TASK) != "Missing":
            self.tasks.set(self.TASK, "Disabled")
        self.native.shutdown()
        self.native.log.unlink(missing_ok=True)  # solo interesan las llamadas posteriores

    @property
    def log_text(self) -> str:
        path = self.logs / "mudanza_docker.log"
        return path.read_text(encoding="utf-8-sig") if path.exists() else ""

    def env(self, now: str | None = None) -> dict[str, str]:
        return self.docker.env(
            MINERVA_SCHTASKS_EXE=str(self.tasks.cmd),
            FAKE_SCHTASKS_STATE=str(self.tasks.state_file),
            MINERVA_STOP_BOT_SCRIPT=str(self.native.stop_script),
            MINERVA_START_BOT_SCRIPT=str(self.native.start_script),
            MINERVA_ASSUME_NOW_UTC=now or self.NOW,
        )

    def common_args(self) -> list[str]:
        return [
            "-NativePort", str(self.native_port), "-DockerPort", str(self.docker_port),
            "-DatabasePath", str(self.db), "-LogDir", str(self.logs),
            "-BackupDir", str(self.backups), "-ProjectName", self.PROJECT,
            "-EnvFile", str(self.env_file), "-HealthTimeoutSeconds", str(self.health_timeout),
        ]

    def run(
        self, script: str, *extra: str, now: str | None = None, timeout: int = 300,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(REPO_ROOT / "scripts" / script), *self.common_args(), *extra,
        ]
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=REPO_ROOT, env=self.env(now),
        )

    def migrate(self, *extra: str, **kwargs) -> subprocess.CompletedProcess[str]:
        return self.run("migrate_to_docker.ps1", "-TaskName", self.TASK, *extra, **kwargs)

    def rollback(self, *extra: str, **kwargs) -> subprocess.CompletedProcess[str]:
        return self.run("rollback_from_docker.ps1", *extra, **kwargs)

    def shutdown(self) -> None:
        self.native.shutdown()
        self.docker.shutdown()
