"""Huella de una base de Minerva: integridad y filas por tabla (Fase 7, docs/MUDANZA_DOCKER.md).

Sirve para demostrar que una mudanza, un respaldo o una restauracion no perdieron nada: se corre
sobre el origen y sobre el destino y se comparan las dos huellas. SOLO cuenta filas y resume el
esquema: nunca lee resultados de operaciones, asi que es seguro con la medicion ciega.

Imprime UNA linea JSON, por ejemplo:

    {"integrity": "ok", "journal_mode": "delete", "schema_sha256": "...",
     "tables": {"trades": 0, "...": 0}}

    python scripts/db_fingerprint.py --db data/backups/docker_20261018_120000.db
    docker compose run --rm --no-deps -T minerva python scripts/db_fingerprint.py \\
        --db /app/data/minerva_estabilidad.db

Abre la base en SOLO LECTURA (tambien desde un medio de solo lectura) y no corre migraciones.

Codigos de salida: 0 integridad ok; 1 la integridad falla; 2 error de uso; 3 la base no existe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

# Se puede lanzar desde cualquier directorio (Programador de tareas, `docker exec`).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.restore_db import connect_readonly  # noqa: E402

EXIT_OK, EXIT_BAD_INTEGRITY, EXIT_USAGE, EXIT_NO_DB = 0, 1, 2, 3


def fingerprint(path: Path) -> dict:
    """Integridad, modo del diario, hash del esquema y conteo de filas de cada tabla.

    Solo cuenta filas y resume el esquema: nunca lee resultados de operaciones.

    Args:
        path: base a resumir (se abre en solo lectura).

    Returns:
        Diccionario con `integrity`, `journal_mode`, `schema_sha256` y `tables`.

    Efectos:
        Lee la base (conteos, no datos).
    """
    conn = connect_readonly(path)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        schema_rows = conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        tables = {
            name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for kind, name, _sql in schema_rows if kind == "table"
        }
    finally:
        conn.close()
    schema = "\n".join(f"{kind}|{name}|{' '.join(sql.split())}" for kind, name, sql in schema_rows)
    return {
        "integrity": integrity,
        "journal_mode": journal_mode,
        "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
        "tables": dict(sorted(tables.items())),
    }


def differences(left: dict, right: dict) -> list[str]:
    """Que cambia entre dos huellas (vacio si son equivalentes).

    El modo del diario NO cuenta: un respaldo se guarda en modo clasico y el bot lo pasa a
    WAL al conectarse.

    Args:
        left: huella del origen.
        right: huella del destino.

    Returns:
        Lista de diferencias en texto.
    """
    found = []
    for label, fp in (("origen", left), ("destino", right)):
        if fp.get("integrity") != "ok":
            found.append(f"la integridad del {label} no es ok ({fp.get('integrity')!r})")
    if left.get("schema_sha256") != right.get("schema_sha256"):
        found.append("el esquema es distinto")
    for table in sorted(set(left["tables"]) | set(right["tables"])):
        a, b = left["tables"].get(table), right["tables"].get(table)
        if a != b:
            found.append(f"{table}: origen {a} filas, destino {b}")
    return found


def main(argv: list[str] | None = None) -> int:
    """Imprime UNA linea JSON con la huella de una base.

    Args:
        argv: argumentos (por defecto, los de la linea de comandos).

    Returns:
        0 con integridad ok; 1 si la integridad falla; 3 si la base no existe.

    Efectos:
        Lee la base en solo lectura y escribe el JSON en stdout.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", required=True, help="base a resumir (se abre en solo lectura)")
    args = parser.parse_args(argv)
    path = Path(args.db)
    if not path.is_file():
        print(f"No existe la base: {path}", file=sys.stderr)
        return EXIT_NO_DB
    try:
        result = fingerprint(path)
    except sqlite3.Error as exc:
        print(f"No se pudo leer la base ({type(exc).__name__}): {exc}", file=sys.stderr)
        return EXIT_BAD_INTEGRITY
    print(json.dumps(result, sort_keys=True))
    return EXIT_OK if result["integrity"] == "ok" else EXIT_BAD_INTEGRITY


if __name__ == "__main__":
    sys.exit(main())
