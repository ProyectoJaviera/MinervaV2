"""Respaldo CONSISTENTE de la base de datos del bot (Fase 6, docs/USO.md, seccion Respaldos).

Usa la API de backup de SQLite con el origen abierto en SOLO LECTURA: es seguro con el bot corriendo
(la base va en modo WAL, y copiar el archivo a mano puede dejar una copia inconsistente) y nunca
escribe el origen. La copia se escribe primero con un nombre temporal, se verifica con
`PRAGMA integrity_check` y solo entonces toma su nombre definitivo `<prefijo>_AAAAMMDD_HHMMSS.db`
(hora UTC): un respaldo a medias o corrupto nunca queda con aspecto de bueno.

Imprime la ruta, el tamano y el resultado de la verificacion. NUNCA resultados de operaciones.

    python scripts/backup_db.py             # data/minerva_estabilidad.db -> data/backups/
    python scripts/backup_db.py --db otra.db --out-dir respaldos --keep 14

`--keep N` (N > 0) borra los respaldos MAS ANTIGUOS de esa carpeta con el mismo prefijo hasta dejar
N; no toca ningun otro archivo. Por defecto (`--keep 0`) no borra nada.

En Docker corre dentro del contenedor (la base y los respaldos viven en el volumen `minerva_data`):

    docker compose exec minerva python scripts/backup_db.py

Codigos de salida: 0 ok; 2 error de uso; 3 el origen no existe; 4 la copia no pasa la verificacion
(no se conserva); 5 el destino ya existe.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_DB = "data/minerva_estabilidad.db"
DEFAULT_OUT_DIR = "data/backups"
DEFAULT_PREFIX = "minerva"

EXIT_OK, EXIT_USAGE, EXIT_NO_SOURCE, EXIT_BAD_COPY, EXIT_EXISTS = 0, 2, 3, 4, 5


def backup_name(prefix: str, now: datetime) -> str:
    return f"{prefix}_{now.astimezone(UTC):%Y%m%d_%H%M%S}.db"


def integrity_check(path: Path) -> str:
    """Resultado de `PRAGMA integrity_check` sobre `path`, abierto en solo lectura."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def copy_consistently(source: Path, destination: Path) -> None:
    """Copia con la API de backup, el origen en solo lectura.

    La copia hereda del origen el modo WAL en su cabecera, y un archivo asi NO se puede abrir desde
    un medio de solo lectura (SQLite necesita crear el `-shm`): lo comprobo la prueba real con
    Docker, donde el respaldo se monta `:ro` en el contenedor que lo restaura. Se guarda en modo
    de diario clasico (`DELETE`): un archivo suelto, sin `-wal`/`-shm`, que abre en cualquier parte.
    El bot lo vuelve a poner en WAL al conectarse (`Database.connect`)."""
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE").fetchone()
        finally:
            dst.close()
    finally:
        src.close()


def prune(out_dir: Path, prefix: str, keep: int) -> list[Path]:
    """Borra los respaldos mas antiguos con ese prefijo hasta dejar `keep`; devuelve los
    borrados."""
    if keep <= 0:
        return []
    mine = sorted(
        (p for p in out_dir.glob(f"{prefix}_????????_??????.db") if p.is_file()),
        key=lambda p: p.name,
    )
    doomed = mine[:-keep] if len(mine) > keep else []
    for path in doomed:
        path.unlink()
    return doomed


def run(
    db: str, out_dir: str, prefix: str, keep: int, now: datetime | None = None,
) -> tuple[int, str]:
    """(codigo de salida, mensaje). Pura respecto a la consola: la usan el CLI y las pruebas."""
    source = Path(db)
    if not source.is_file():
        return EXIT_NO_SOURCE, f"No existe la base de origen: {source}"
    destination_dir = Path(out_dir)
    destination = destination_dir / backup_name(prefix, now or datetime.now(UTC))
    if destination.resolve() == source.resolve():  # imposible por el nombre; barato de asegurar
        return EXIT_USAGE, "El destino no puede ser la base de origen"
    if destination.exists():
        return EXIT_EXISTS, f"El respaldo ya existe: {destination} (espera un segundo y repite)"
    destination_dir.mkdir(parents=True, exist_ok=True)

    temporary = destination.with_suffix(".db.tmp")
    if temporary.exists():
        temporary.unlink()  # restos de un intento anterior interrumpido
    try:
        copy_consistently(source, temporary)
        verdict = integrity_check(temporary)
        if verdict != "ok":
            temporary.unlink(missing_ok=True)
            return EXIT_BAD_COPY, f"La copia no paso la verificacion ({verdict}); no se conserva."
        os.replace(temporary, destination)
    except sqlite3.Error as exc:
        temporary.unlink(missing_ok=True)
        return EXIT_BAD_COPY, f"No se pudo copiar la base ({type(exc).__name__}): {exc}"

    lines = [f"Respaldo: {destination} ({destination.stat().st_size / 1_048_576:.1f} MB)",
             "Verificacion de integridad: ok"]
    for old in prune(destination_dir, prefix, keep):
        lines.append(f"Borrado por antiguedad (--keep {keep}): {old.name}")
    return EXIT_OK, "\n".join(lines)


def docker_mode_refusal(db: str) -> str | None:
    """Tras la mudanza a Docker, `data/minerva_estabilidad.db` es una copia VIEJA: respaldarla en
    silencio daria por respaldado algo que no es la base viva (scripts/docker_support.py)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.docker_support import docker_mode_refusal as refusal

    return refusal(db, "Para respaldar la base de Docker usa: python scripts/docker_db.py backup.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=None, help=f"base a respaldar (def.: {DEFAULT_DB})")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="carpeta de los respaldos")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="prefijo del nombre del archivo")
    parser.add_argument("--keep", type=int, default=0,
                        help="conservar solo los N respaldos mas recientes (0 = no borrar nada)")
    args = parser.parse_args(argv)
    if args.keep < 0 or not args.prefix.replace("_", "").replace("-", "").isalnum():
        parser.error("--keep debe ser >= 0 y --prefix solo letras, numeros, '-' y '_'")
    if args.db is None:  # la base por defecto: solo ahi aplica la proteccion de la mudanza
        refusal = docker_mode_refusal(DEFAULT_DB)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT_USAGE
    code, message = run(args.db or DEFAULT_DB, args.out_dir, args.prefix, args.keep)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
