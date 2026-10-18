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

Uso (solo desarrollo; necesita `packaging`, que trae pytest):

    python scripts/export_docker_requirements.py          # escribe docker/requirements.lock
    python scripts/export_docker_requirements.py --check  # falla si el archivo esta desactualizado
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
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Closure:
    versions: dict[str, str] = field(default_factory=dict)  # nombre normalizado -> version
    display: dict[str, str] = field(default_factory=dict)  # nombre normalizado -> nombre bonito
    missing: set[str] = field(default_factory=set)  # exigidos para Linux pero no instalados aqui


def collect_closure(
    roots: list[str],
    distribution: Callable[[str], Any] = metadata.distribution,
    environment: dict[str, str] | None = None,
) -> Closure:
    """Clausura transitiva de `roots` para el entorno de la imagen. `distribution(nombre)` debe
    devolver algo con `.version`, `.requires` y `.metadata["Name"]` (como `importlib.metadata`)."""
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
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return [*data["project"]["dependencies"], *EXTRA_RUNTIME_ROOTS]


def render_lock(closure: Closure) -> str:
    lines = [
        f"{closure.display[name]}=={closure.versions[name]}" for name in sorted(closure.versions)
    ]
    return HEADER + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
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

    if args.check:
        current = LOCK_PATH.read_text(encoding="utf-8") if LOCK_PATH.exists() else ""
        if current != text:
            print("docker/requirements.lock esta desactualizado: corre este script sin --check.",
                  file=sys.stderr)
            return 1
        print("docker/requirements.lock esta al dia.")
        return 0

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"Escrito {LOCK_PATH} con {len(closure.versions)} paquetes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
