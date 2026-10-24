"""Identidad de la medicion (Fase 6, docs/USO.md, rutina semanal): ¿la medicion en curso usa UN solo
modelo, UN solo prompt (el congelado) y UNA sola version de features?

El hash congelado (`FROZEN_PROMPT_SHA256`) cubre el texto del prompt y las features, pero NO el
modelo: si `ANTHROPIC_SONNET_MODEL` cambiara, el etiquetado seguiria "sano" y nada lo senalaria, y
el veredicto mezclaria dos modelos sin avisar. Esta herramienta lo hace visible: cuenta las filas de
`llm_logs` con fase='MEDICION' por (modelo, version del prompt, hash, version de features) y avisa
si

- hay mas de una combinacion (la medicion mezcla modelos o prompts),
- alguna combinacion no es la del prompt congelado, o
- el modelo configurado AHORA en este entorno no es el que etiqueto las filas (un cambio que se
  haria efectivo al proximo reinicio).

Solo LEE (URI `mode=ro`, no corre migraciones) y SOLO imprime conteos y fechas: nunca decisiones,
costos ni resultados, asi que es seguro con el gate cerrado. Como el resto de las herramientas que
aceptan `--db`, se corre sobre una COPIA (`scripts/backup_db.py`): ruta distinta de
`settings.database_path`, verificada con un assert explicito.

    python scripts/measurement_identity.py --db data/backups/minerva_copia.db
    docker compose exec minerva python scripts/measurement_identity.py --db /app/data/backups/X.db

Codigos de salida: 0 identidad unica y congelada (o aun sin filas); 1 anomalia; 2 error de uso;
3 la base no existe.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

# Los scripts se pueden lanzar desde cualquier directorio (Programador de tareas, `docker exec`):
# se fija la raiz del repo (para que `.env` y los imports se resuelvan) pero las rutas relativas que
# se pasen por la linea de comandos se interpretan desde donde se lanzo.
LAUNCH_DIR = Path.cwd()
os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ".")

from app.config import settings  # noqa: E402
from app.trading.shadow_live_labeler import FROZEN_PROMPT_SHA256  # noqa: E402

EXIT_OK, EXIT_ANOMALY, EXIT_USAGE, EXIT_NO_DB = 0, 1, 2, 3
NO_DATA = "(sin dato)"


class Group(NamedTuple):
    """Un grupo de filas de la MEDICION con la misma identidad.

    Attributes:
        model: modelo que etiqueto.
        prompt_version: version del texto del prompt.
        prompt_sha256: hash del prompt y de la version de features.
        features_version: version del mensaje de features.
        rows: cuantas filas tiene.
        first_at: fecha de la primera fila.
        last_at: fecha de la ultima fila.
    """
    model: str
    prompt_version: str
    prompt_sha256: str
    features_version: str
    rows: int
    first_at: str
    last_at: str


def load_groups(conn: sqlite3.Connection) -> list[Group]:
    """Las filas de la MEDICION agrupadas por su identidad, de la mas antigua a la mas reciente.

    Solo cuenta filas y toma fechas: no lee decisiones, costos ni respuestas.

    Args:
        conn: conexion de solo lectura a una copia.

    Returns:
        Lista de `Group`.

    Efectos:
        Lee `llm_logs`.
    """
    rows = conn.execute(
        "SELECT model, prompt_version, prompt_sha256, features_version, COUNT(*), "
        "MIN(created_at), MAX(created_at) FROM llm_logs WHERE fase = 'MEDICION' "
        "GROUP BY model, prompt_version, prompt_sha256, features_version "
        "ORDER BY MIN(created_at)"
    ).fetchall()
    return [
        Group(model, version, sha, features or NO_DATA, int(count), str(first), str(last))
        for model, version, sha, features, count, first, last in rows
    ]


def assess(
    groups: list[Group], configured_model: str, frozen_hash: str = FROZEN_PROMPT_SHA256,
) -> tuple[int, list[str]]:
    """Evalua si la medicion tiene UNA sola identidad (modelo, prompt congelado, features).

    Pura: no toca la base ni el entorno. Avisa de mas de una combinacion, de filas que no
    usan el prompt congelado y de un modelo configurado ahora distinto del que etiqueto.

    Args:
        groups: los grupos de `load_groups`.
        configured_model: modelo configurado en este entorno.
        frozen_hash: hash del prompt congelado.

    Returns:
        La tupla `(codigo de salida, lineas del informe)`: 0 sin anomalias; 1 con anomalia.
    """
    lines = [
        "=== Identidad de la medicion (llm_logs, fase MEDICION) ===",
        f"Hash del prompt congelado: {frozen_hash}",
        f"Modelo configurado ahora en este entorno: {configured_model}",
    ]
    if not groups:
        lines.append("Todavia no hay filas de MEDICION en esta copia: nada que comprobar.")
        return EXIT_OK, lines

    lines.append(f"Combinaciones distintas encontradas: {len(groups)}")
    for number, group in enumerate(groups, start=1):
        hash_state = (
            "coincide con el congelado" if group.prompt_sha256 == frozen_hash
            else "NO es el congelado"
        )
        lines.append(
            f"  {number}) modelo={group.model} | prompt={group.prompt_version} | "
            f"features={group.features_version} | hash {group.prompt_sha256[:12]}... "
            f"({hash_state}) | {group.rows} filas | de {group.first_at} a {group.last_at}"
        )

    problems = []
    if len(groups) > 1:
        problems.append(
            "hay mas de una combinacion: la medicion mezcla modelos, prompts o features"
        )
    if any(g.prompt_sha256 != frozen_hash for g in groups):
        problems.append("hay filas que no usan el prompt congelado")
    models = {g.model for g in groups}
    if configured_model not in models:
        problems.append(
            f"el modelo configurado ahora ({configured_model}) no es el que etiqueto la medicion "
            f"({', '.join(sorted(models))}): al reiniciar con esta configuracion cambiaria"
        )

    if problems:
        lines.append("ANOMALIA:")
        lines.extend(f"  - {problem}" for problem in problems)
        lines.append(
            "No sigas operando sin decidirlo: cambiar el modelo o el prompt invalida la medicion "
            "(docs/CONFIGURACION.md, seccion 'Que no cambiar durante una medicion')."
        )
        return EXIT_ANOMALY, lines
    lines.append("OK: un solo modelo, el prompt congelado y una sola version de features.")
    return EXIT_OK, lines


def main(argv: list[str] | None = None) -> int:
    """Imprime la identidad de la medicion de una copia y avisa de anomalias.

    Args:
        argv: argumentos (por defecto, los de la linea de comandos).

    Returns:
        0 identidad unica y congelada (o sin filas todavia); 1 anomalia; 3 la base no existe.

    Efectos:
        Lee `llm_logs` de la copia en solo lectura.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="copia de la base (nunca la de trabajo)")
    args = parser.parse_args(argv)

    path = (LAUNCH_DIR / args.db).resolve()
    assert path != Path(settings.database_path).resolve(), (
        "la ruta de --db no puede ser la base de trabajo (settings.database_path)"
    )
    if not path.is_file():
        print(f"No existe la base: {path}", file=sys.stderr)
        return EXIT_NO_DB
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        groups = load_groups(conn)
    finally:
        conn.close()
    code, lines = assess(groups, settings.anthropic_sonnet_model)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
