"""Genera `docker/requirements.lock`: las versiones EXACTAS de las dependencias de ejecucion de
la imagen Docker, tomadas del entorno que paso las pruebas (Fase 6, docs/FASE6_PLAN.md).

Por que existe: `pyproject.toml` solo fija rangos (`pandas>=2.2`). Una imagen construida meses
despues instalaria versiones nuevas que nadie probo, y un cambio sutil en pandas o pydantic
podria alterar indicadores y la comparabilidad de la medicion sin que nada lo avise. El archivo
de versiones congela el conjunto que si paso `pytest`.

Como: recorre la clausura de `[project].dependencies` con `importlib.metadata` (las versiones
instaladas en ESTE entorno) evaluando los marcadores de entorno para **Linux** (la imagen), asi
que no entran `colorama` ni nada exclusivo de Windows, ni las herramientas de desarrollo
(pytest, ruff). Un paquete exigido para Linux que no esta instalado aqui (p. ej. `uvloop`, extra
de `uvicorn[standard]`) se omite y se informa: no esta en el conjunto probado, asi que no se
agrega a ciegas.

Tambien genera `docker/requirements.old-cpu.lock`, la variante para procesadores SIN SSE4 (p. ej.
un Intel Atom N570): es el mismo conjunto de versiones con `numpy==2.3.5`, porque NumPy 2.4 exige
x86-64-v2 y su `import` falla con "instruccion ilegal" en esos procesadores. No se calcula desde el
entorno sino desde el lock principal, para que las dos listas solo difieran en esa linea.

Uso (solo desarrollo; necesita `packaging`, que trae pytest):

    python scripts/export_docker_requirements.py          # escribe los dos archivos de versiones
    python scripts/export_docker_requirements.py --check  # falla si alguno esta desactualizado
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
LOCK_PATH = REPO_ROOT / "docker" / "requirements.lock"
OLD_CPU_LOCK_PATH = REPO_ROOT / "docker" / "requirements.old-cpu.lock"
# La ultima serie de NumPy cuyas ruedas de Linux x86_64 funcionan sin SSE4 (baseline x86-64
# original). Verificada con una prueba de importacion bajo un procesador sin SSE4 emulado.
OLD_CPU_NUMPY = "2.3.5"

# Entorno de la imagen (python:3.11-slim, Linux x86_64): contra esto se evaluan los marcadores.
LINUX_ENVIRONMENT = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "os_name": "posix",
    "platform_machine": "x86_64",
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
    "python_version": "3.11",
    "python_full_version": "3.11.15",
}

HEADER = """\
# Versiones EXACTAS de las dependencias de ejecucion de la imagen Docker (Fase 6).
# GENERADO por scripts/export_docker_requirements.py desde el entorno que paso las pruebas; no se
# edita a mano. Para regenerarlo: python scripts/export_docker_requirements.py
# `uvicorn` va sin el extra [standard] a proposito: sus dependencias opcionales ya estan fijadas
# abajo, salvo uvloop (solo Linux), que no esta en el conjunto probado y por eso no se instala.
"""


def normalize(name: str) -> str:
    """El nombre de un paquete normalizado (minusculas y guiones), como lo compara pip.

    Args:
        name: nombre del paquete.

    Returns:
        El nombre normalizado.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Closure:
    """La clausura transitiva de dependencias de ejecucion.

    Attributes:
        versions: `nombre normalizado -> version instalada`.
        display: `nombre normalizado -> nombre con su capitalizacion original`.
        missing: paquetes exigidos para Linux pero no instalados en este entorno.
    """
    versions: dict[str, str] = field(default_factory=dict)  # nombre normalizado -> version
    display: dict[str, str] = field(default_factory=dict)  # nombre normalizado -> nombre bonito
    missing: set[str] = field(default_factory=set)  # exigidos para Linux pero no instalados aqui


def collect_closure(
    roots: list[str],
    distribution: Callable[[str], Any] = metadata.distribution,
    environment: dict[str, str] | None = None,
) -> Closure:
    """Clausura transitiva de `roots` evaluada para el entorno de la imagen (Linux, Python 3.11).

    Args:
        roots: dependencias directas (como en `pyproject.toml`).
        distribution: funcion que devuelve la distribucion instalada de un nombre; por
            defecto `importlib.metadata.distribution`.
        environment: valores de los marcadores de entorno; por defecto, el de la imagen.

    Returns:
        La `Closure`.
    """
    environment = environment or LINUX_ENVIRONMENT
    closure = Closure()
    queue: deque[tuple[str, frozenset[str]]] = deque()
    for raw in roots:
        req = Requirement(raw)
        queue.append((normalize(req.name), frozenset(req.extras)))
    seen: set[tuple[str, frozenset[str]]] = set()
    while queue:
        key = queue.popleft()
        if key in seen:
            continue
        seen.add(key)
        name, extras = key
        try:
            dist = distribution(name)
        except metadata.PackageNotFoundError:
            closure.missing.add(name)
            continue
        closure.versions[name] = dist.version
        closure.display[name] = dist.metadata["Name"]
        for line in dist.requires or []:
            req = Requirement(line)
            wanted = False
            if req.marker is None:
                wanted = True
            else:
                # Sin extra: el marcador debe cumplirse tal cual; con extras pedidos, tambien
                # cuentan las dependencias condicionadas a ese extra.
                for extra in {"", *extras}:
                    if req.marker.evaluate({**environment, "extra": extra}):
                        wanted = True
                        break
            if wanted:
                queue.append((normalize(req.name), frozenset(req.extras)))
    return closure


# Exigidas en ejecucion aunque ninguna dependencia las pida para Linux. `tzdata`: pandas solo la
# pide en Windows, pero `zoneinfo` (REPORT_TIMEZONE=America/Santiago) la necesita donde el
# sistema no trae la base de zonas horarias, y es la misma que usa el entorno probado.
EXTRA_RUNTIME_ROOTS = ["tzdata"]


def runtime_roots(pyproject: Path = PYPROJECT) -> list[str]:
    """Las dependencias de ejecucion de `pyproject.toml` mas las extra que se exigen.

    Las extra son las que se instalan aunque ninguna dependencia las pida (`tzdata`).

    Args:
        pyproject: archivo `pyproject.toml`.

    Returns:
        Lista de requisitos.

    Efectos:
        Lee `pyproject.toml`.
    """
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return [*data["project"]["dependencies"], *EXTRA_RUNTIME_ROOTS]


def render_lock(closure: Closure) -> str:
    """El texto de `docker/requirements.lock`: cabecera y una linea `Nombre==version` por paquete.

    Args:
        closure: la clausura calculada.

    Returns:
        El texto del archivo.
    """
    lines = [
        f"{closure.display[name]}=={closure.versions[name]}" for name in sorted(closure.versions)
    ]
    return HEADER + "\n".join(lines) + "\n"


OLD_CPU_HEADER = f"""\
# VARIANTE PARA PROCESADORES SIN SSE4 (p. ej. Intel Atom N570): las mismas versiones exactas que
# docker/requirements.lock, salvo numpy=={OLD_CPU_NUMPY} (NumPy 2.4 exige x86-64-v2 y su import
# falla con "instruccion ilegal" sin SSE4). GENERADO por scripts/export_docker_requirements.py a
# partir del lock principal; no se edita a mano. Se usa con
# `docker build --build-arg REQUIREMENTS_FILE=docker/requirements.old-cpu.lock .`
"""


def render_old_cpu_lock(main_lock: str) -> str:
    """El texto de `docker/requirements.old-cpu.lock`: el lock principal con NumPy anterior.

    Args:
        main_lock: el texto de `docker/requirements.lock`.

    Returns:
        La cabecera de la variante y las mismas lineas de versiones, con la de `numpy` cambiada
        a `OLD_CPU_NUMPY`.

    Raises:
        ValueError: si el lock principal no fija `numpy`.
    """
    pins = [
        line for line in main_lock.splitlines() if line.strip() and not line.startswith("#")
    ]
    if not any(normalize(line.split("==")[0]) == "numpy" for line in pins):
        raise ValueError("el lock principal no fija numpy")
    swapped = [
        f"{line.split('==')[0]}=={OLD_CPU_NUMPY}"
        if normalize(line.split("==")[0]) == "numpy" else line
        for line in pins
    ]
    return OLD_CPU_HEADER + "\n".join(swapped) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Regenera los archivos de versiones exactas de la imagen (principal y de CPU antigua).

    Args:
        argv: argumentos (por defecto, los de la linea de comandos); `--check` solo compara.

    Returns:
        0 si escribio o estan al dia; 1 con `--check` si alguno esta desactualizado.

    Efectos:
        Lee `pyproject.toml` y los paquetes instalados; escribe `docker/requirements.lock` y
        `docker/requirements.old-cpu.lock` (salvo con `--check`).
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="falla si docker/requirements.lock no coincide",
    )
    args = parser.parse_args(argv)

    closure = collect_closure(runtime_roots())
    text = render_lock(closure)
    for name in sorted(closure.missing):
        print(f"AVISO: {name} se exige para Linux pero no esta instalado aqui; no se fija.",
              file=sys.stderr)

    old_cpu_text = render_old_cpu_lock(text)
    targets = ((LOCK_PATH, text), (OLD_CPU_LOCK_PATH, old_cpu_text))

    if args.check:
        stale = [
            path for path, expected in targets
            if (path.read_text(encoding="utf-8") if path.exists() else "") != expected
        ]
        for path in stale:
            print(f"docker/{path.name} esta desactualizado: corre este script sin --check.",
                  file=sys.stderr)
        if stale:
            return 1
        print("docker/requirements.lock y docker/requirements.old-cpu.lock estan al dia.")
        return 0

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    for path, expected in targets:
        path.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Escrito {LOCK_PATH} con {len(closure.versions)} paquetes.")
    print(f"Escrito {OLD_CPU_LOCK_PATH} (igual, con numpy=={OLD_CPU_NUMPY}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
