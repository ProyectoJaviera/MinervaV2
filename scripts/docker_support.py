"""Soporte para operar Minerva cuando el bot corre en Docker (Fase 7, docs/MUDANZA_DOCKER.md).

Tras la mudanza la base viva esta en un volumen de Docker, NO en `data/` del host. Dos cosas siguen
de ahi:

1. **La marca de la mudanza** (`data/MIGRADO_A_DOCKER.txt`, junto a la base nativa). La escribe
   `scripts/migrate_to_docker.ps1` y la borra `scripts/rollback_from_docker.ps1`. Mientras exista,
   `data/minerva_estabilidad.db` es una copia VIEJA: `start_bot.ps1` se niega a arrancar el bot
   nativo sobre ella y las herramientas que la usarian por defecto (respaldo, restauracion,
   levantar el stop por drawdown) se niegan a tocarla en silencio -- dar por respaldado o por
   arreglado algo que no es la base viva es peor que un error. Pasar `--db` explicitamente la
   esquiva a proposito.
2. **Sacar una copia consistente de la base del volumen**: un respaldo con la API de backup de
   SQLite DENTRO del contenedor (seguro con el bot escribiendo) y `docker compose cp` al host. Sobre
   esa copia corren, como antes, los informes y el procedimiento del veredicto.

Solo biblioteca estandar, y viaja en la imagen (los scripts de respaldo la importan); dentro del
contenedor la marca nunca existe, asi que alli no cambia nada.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKER_NAME = "MIGRADO_A_DOCKER.txt"
SERVICE = "minerva"
CONTAINER_BACKUP_DIR = "/app/data/backups"
# Variables de interpolacion de Compose SIN secretos: con `--env-file` Compose no lee ni interpola
# el `.env` real (ver docker/compose.vars).
COMPOSE_VARS = REPO_ROOT / "docker" / "compose.vars"
BACKUP_LINE = re.compile(r"^Respaldo: (?P<path>\S+) \(", re.MULTILINE)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DockerError(RuntimeError):
    """Un fallo al hablar con Docker; el mensaje dice que paso y es seguro mostrarlo."""


# --- la marca de la mudanza ------------------------------------------------------------------


def marker_path(db_path: str | Path) -> Path:
    """La marca vive junto a la base nativa: la protege justo a ella."""
    return Path(db_path).resolve().parent / MARKER_NAME


def docker_mode_active(db_path: str | Path) -> bool:
    return marker_path(db_path).is_file()


def docker_mode_refusal(db_path: str | Path, hint: str) -> str | None:
    """El rechazo si `db_path` es la base nativa VIEJA de una mudanza ya hecha; si no, `None`."""
    if not docker_mode_active(db_path):
        return None
    return (
        f"El bot corre en Docker desde la mudanza (existe {marker_path(db_path)}): la base de "
        f"{db_path} es una copia VIEJA, no la que usa el bot. {hint} "
        "Si de verdad quieres esa base, pasa --db explicitamente."
    )


# --- hablar con Docker -----------------------------------------------------------------------


def docker_exe() -> str:
    return os.environ.get("MINERVA_DOCKER_EXE", "docker")


def run_docker(
    args: list[str], *, timeout: float = 900, check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Ejecuta `docker <args>` capturando la salida. Nunca usa el shell ni espera una terminal."""
    command = [docker_exe(), *args]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )
    except FileNotFoundError:
        raise DockerError(
            "No se encontro el comando 'docker'. Instala Docker Desktop y abrelo."
        ) from None
    except subprocess.TimeoutExpired:
        raise DockerError(f"'docker {' '.join(args[:3])}' tardo mas de {timeout:.0f} s") from None
    if check and result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise DockerError(
            f"'docker {' '.join(args[:4])}' fallo (codigo {result.returncode}): {' | '.join(tail)}"
        )
    return result


def compose(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    """`docker compose` sobre el compose del repo. `COMPOSE_PROJECT_NAME` (si esta) elige el
    proyecto; el `.env` real no se interpola (ver `COMPOSE_VARS`)."""
    base = ["compose", "--progress", "quiet", "-f", str(REPO_ROOT / "docker-compose.yml")]
    if COMPOSE_VARS.is_file():
        base += ["--env-file", str(COMPOSE_VARS)]
    return run_docker([*base, *args], **kwargs)


def engine_ready(timeout: float = 120) -> bool:
    """Espera (hasta `timeout`) a que el motor de Docker responda: tras encender el PC, Docker
    Desktop tarda un rato en estar listo."""
    deadline = time.time() + timeout
    while True:
        try:
            if run_docker(["info", "--format", "{{.ServerVersion}}"], timeout=30, check=False
                          ).returncode == 0:
                return True
        except DockerError:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(3)


def parse_compose_ps(output: str) -> list[dict]:
    """`docker compose ps --format json`: un objeto por linea (Compose reciente) o una lista."""
    text = output.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]


def service_state() -> str:
    """`running`, otro estado de Docker (`exited`, `created`, ...) o `absent` si no hay contenedor
    del servicio. Los contenedores de un solo uso (`compose run`) no cuentan."""
    result = compose(["ps", "-a", "--format", "json", SERVICE], check=False)
    entries = [
        e for e in parse_compose_ps(result.stdout)
        if "com.docker.compose.oneoff=True" not in str(e.get("Labels", ""))
    ]
    if not entries:
        return "absent"
    states = [str(e.get("State", "")).lower() for e in entries]
    return "running" if "running" in states else (states[0] or "absent")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --- sacar una copia consistente de la base del volumen ---------------------------------------


def export_db_copy(
    out_dir: Path, prefix: str = "docker", *, keep_in_volume: bool = False, volume_keep: int = 0,
    engine_timeout: float = 60,
) -> Path:
    """Respaldo consistente de la base del volumen, traido al host y VERIFICADO.

    1. `scripts/backup_db.py` DENTRO del contenedor (API de backup de SQLite, el origen en solo
       lectura: seguro con el bot escribiendo; verifica la integridad antes de darle su nombre).
       Si el servicio esta detenido, lo hace un contenedor de un solo uso sobre el mismo volumen.
    2. SHA-256 del archivo dentro del contenedor.
    3. `docker compose cp` al host (o, si no existe ni el contenedor detenido, un contenedor de un
       solo uso con la carpeta montada) y comparacion del SHA-256: una copia a medias nunca pasa.
    4. Salvo `keep_in_volume`, borra el archivo del volumen: las copias para informes no se
       acumulan. `volume_keep > 0` deja solo los N respaldos mas recientes con ese prefijo.
    `engine_timeout` es cuanto espera a que Docker responda: tras encender el PC, Docker Desktop
    tarda un rato en estar listo.
    """
    if not engine_ready(timeout=engine_timeout):
        raise DockerError("Docker no responde. Abre Docker Desktop y espera a que este listo.")
    out_dir.mkdir(parents=True, exist_ok=True)
    state = service_state()

    def in_container(command: list[str]) -> subprocess.CompletedProcess[str]:
        if state == "running":
            return compose(["exec", "-T", SERVICE, *command])
        return compose(["run", "--rm", "--no-deps", "-T", SERVICE, *command])

    backup = in_container([
        "python", "scripts/backup_db.py", "--out-dir", CONTAINER_BACKUP_DIR, "--prefix", prefix,
        "--keep", str(volume_keep),
    ])
    found = BACKUP_LINE.search(backup.stdout)
    if not found:
        raise DockerError(
            f"backup_db.py no informo la ruta del respaldo: {backup.stdout.strip()[:200]}"
        )
    container_path = found.group("path")
    expected = in_container(["sha256sum", container_path]).stdout.split()[0]

    destination = out_dir / posixpath.basename(container_path)
    if destination.exists():
        raise DockerError(f"Ya existe {destination}; no se sobrescribe (reintenta en un segundo).")
    try:
        if state == "absent":
            # sin contenedor del servicio no hay `compose cp`: un contenedor de un solo uso copia
            compose([
                "run", "--rm", "--no-deps", "-T", "-v", f"{out_dir.resolve().as_posix()}:/out",
                SERVICE, "cp", container_path, f"/out/{destination.name}",
            ])
        else:
            compose(["cp", f"{SERVICE}:{container_path}", str(destination)])
        if not destination.is_file() or sha256_of(destination) != expected:
            raise DockerError("La copia traida al host no coincide con el original (SHA-256).")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    if not keep_in_volume:
        in_container(["rm", "-f", container_path])
    return destination
